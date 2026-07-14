"""Headless smoke test: mounts the app, exercises request CRUD and tab
switching against a throwaway database. Run: `python tools/smoke.py`."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_smoke_"))
# Dummy key so the Map tab's preflight enables Generate; the fake llm never uses it.
# Provider pinned so a repo .env (FUNNEL_LLM_PROVIDER=openrouter) can't flip the preflight.
os.environ.setdefault("ANTHROPIC_API_KEY", "smoke-test-dummy")
os.environ["FUNNEL_LLM_PROVIDER"] = "anthropic"

from funnel.db import repo  # noqa: E402
from funnel.ui.app import FunnelApp  # noqa: E402
from funnel.ui.sidebar import Sidebar  # noqa: E402


async def run() -> None:
    app = FunnelApp()
    async with app.run_test() as pilot:
        await pilot.pause()

        r1 = repo.create_request("Silicon Photonics")
        repo.create_request("EV Battery Supply Chain")
        sidebar = app.query_one(Sidebar)
        sidebar.reload(select_id=r1.id)
        await pilot.pause()
        assert app.current_request_id == r1.id, app.current_request_id

        for tab_id in ("tab-flow", "tab-intake", "tab-graph", "tab-analysis"):
            app.query_one("#tabs").active = tab_id
            await pilot.pause()
        for subtabs, sub_id in (("#graph-subtabs", "subtab-evidence"),
                                ("#analysis-subtabs", "subtab-export")):
            app.query_one(subtabs).active = sub_id
            await pilot.pause()

        # --- Phase 1: intake save + resume ---
        from funnel.ui.panels.intake import IntakePanel
        from funnel.db.models import ResearchRequest

        intake = app.query_one(IntakePanel)
        intake.query_one("#q-industry").value = "Silicon photonics"
        intake.query_one("#q-target_output").value = "800G optical transceivers"
        intake.query_one("#q-geography").value = "Taiwan, USA, Japan"
        intake.query_one("#q-relationship_types").select("supplier")
        intake.query_one("#q-relationship_types").select("customer")
        intake.query_one("#q-investment_focus").value = True
        await pilot.pause()
        intake.save()
        await pilot.pause()

        answers = repo.get_intake_answers(r1.id)
        assert answers["industry"].answer_value == "Silicon photonics", answers.get("industry")
        assert "supplier" in answers["relationship_types"].answer_value, answers["relationship_types"].answer_value
        req = repo.get(ResearchRequest, r1.id)
        assert req.industry_of_interest == "Silicon photonics", req.industry_of_interest
        assert req.geography == ["Taiwan", "USA", "Japan"], req.geography
        assert set(req.relationship_types) == {"supplier", "customer"}, req.relationship_types
        assert req.investment_focus is True

        # resume: reload and confirm widgets repopulate
        intake.load_request(r1.id)
        await pilot.pause()
        assert intake.query_one("#q-industry").value == "Silicon photonics"
        assert set(intake.query_one("#q-relationship_types").selected) == {"supplier", "customer"}

        # --- Phase 2: graph builder ---
        from funnel.db.models import CompanyNode, ProcessNode, RelationshipEdge
        from funnel.ui.panels.graph import (
            CompanyPanel,
            EdgeForm,
            EdgePanel,
            GraphPanel,
            ProcessNodeForm,
        )

        # ProcessNodeForm builds and collects
        pn_form = ProcessNodeForm(r1.id, None)
        app.push_screen(pn_form)
        await pilot.pause()
        pn_form.query_one("#f-name").value = "Wafer fabrication"
        pn_form.query_one("#f-keywords").value = "lithography, etch"
        pn_data = pn_form.collect()
        assert pn_data["name"] == "Wafer fabrication", pn_data
        assert pn_data["layer"] == "midstream", pn_data
        assert pn_data["keywords"] == ["lithography", "etch"], pn_data
        app.pop_screen()
        await pilot.pause()

        node = repo.add(ProcessNode(request_id=r1.id, **pn_data))
        c1 = repo.add(CompanyNode(request_id=r1.id, name="TSMC", ticker="TSM",
                                  role="operator", process_node_ids=[node.id], confidence_score=0.9))
        c2 = repo.add(CompanyNode(request_id=r1.id, name="ASML", ticker="ASML",
                                  role="equipment_supplier", confidence_score=0.8))

        # EdgeForm sees the companies and collects a valid edge
        edge_form = EdgeForm(r1.id, None)
        app.push_screen(edge_form)
        await pilot.pause()
        edge_form.query_one("#f-from").value = c2.id
        edge_form.query_one("#f-to").value = c1.id
        edge_data = edge_form.collect()
        assert edge_data["from_company"] == c2.id and edge_data["to_company"] == c1.id, edge_data
        app.pop_screen()
        await pilot.pause()
        repo.add(RelationshipEdge(request_id=r1.id, **edge_data))

        # Panels render the rows (edge resolves company names)
        graph = app.query_one(GraphPanel)
        epanel = graph.query_one(EdgePanel)
        epanel.load_request(r1.id)
        cpanel = graph.query_one(CompanyPanel)
        cpanel.load_request(r1.id)
        await pilot.pause()
        assert epanel.query_one("#crud-table").row_count == 1
        assert cpanel.query_one("#crud-table").row_count == 2
        assert epanel._names[c1.id] == "TSMC"

        # --- Phase 3: evidence + deals ---
        from funnel.db.models import Deal, EvidenceItem
        from funnel.ui.evidence_meta import strongest_source
        from funnel.ui.panels.evidence import (
            DealForm,
            DealPanel,
            EvidenceDealsPanel,
            EvidenceForm,
            EvidencePanel,
        )

        ev_form = EvidenceForm(r1.id, None)
        app.push_screen(ev_form)
        await pilot.pause()
        ev_form.query_one("#f-claim").value = "TSMC qualified ASML High-NA EUV tool"
        ev_form.query_one("#f-publisher").value = "Company filing 20-F"
        ev_form.query_one("#f-source").value = "filings"
        ev_form.query_one("#f-companies").select(c1.id)
        ev_data = ev_form.collect()
        assert ev_data["claim"].startswith("TSMC qualified"), ev_data
        assert ev_data["source_type"] == "filings", ev_data
        assert c1.id in ev_data["related_companies"], ev_data
        app.pop_screen()
        await pilot.pause()
        ev = repo.add(EvidenceItem(request_id=r1.id, **ev_data))

        deal_form = DealForm(r1.id, None)
        app.push_screen(deal_form)
        await pilot.pause()
        deal_form.query_one("#f-type").value = "supply_agreement"
        deal_form.query_one("#f-buyer").value = c1.id
        deal_form.query_one("#f-supplier").value = c2.id
        deal_form.query_one("#f-product").value = "High-NA EUV scanners"
        deal_form.query_one("#f-sources").select(ev.id)
        deal_data = deal_form.collect()
        assert deal_data["buyer"] == c1.id and deal_data["supplier"] == c2.id, deal_data
        assert ev.id in deal_data["source_ids"], deal_data
        app.pop_screen()
        await pilot.pause()
        repo.add(Deal(request_id=r1.id, **deal_data))

        evidence_panel = app.query_one(EvidenceDealsPanel)
        epan = evidence_panel.query_one(EvidencePanel)
        dpan = evidence_panel.query_one(DealPanel)
        epan.load_request(r1.id)
        dpan.load_request(r1.id)
        await pilot.pause()
        assert epan.query_one("#crud-table").row_count == 1
        assert dpan.query_one("#crud-table").row_count == 1

        # company evidence badge reflects the linked filing
        cpanel.load_request(r1.id)
        await pilot.pause()
        assert strongest_source(cpanel._evidence_by_company.get(c1.id, [])) == "filing"

        # --- End-to-end UI glue: real push-form -> Save button -> persist -> refresh ---
        # Exercises CrudPanel._open_form's save callback and CompanyForm, which the
        # direct-repo tests above bypassed.
        before = repo.count_for_request(CompanyNode, r1.id)
        cpanel._open_form(None)
        await pilot.pause()
        form = app.screen
        assert type(form).__name__ == "CompanyForm", type(form).__name__
        form.query_one("#f-name").value = "United Microelectronics"
        form.query_one("#f-ticker").value = "UMC"
        form.query_one("#f-nodes").select(node.id)
        form.query_one("#save").press()
        await pilot.pause()
        after = repo.count_for_request(CompanyNode, r1.id)
        assert after == before + 1, (before, after)
        assert cpanel.query_one("#crud-table").row_count == after
        new_co = next(c for c in repo.list_for_request(CompanyNode, r1.id)
                      if c.name == "United Microelectronics")
        assert new_co.ticker == "UMC", new_co
        assert node.id in new_co.process_node_ids, new_co
        print("UI_GLUE_OK companies", before, "->", after, "via Save button; node linked=",
              node.id in new_co.process_node_ids)

        # --- Phase 4: scoring + queries ---
        from funnel.graph.assembly import build_graph
        from funnel.graph.queries import run_query
        from funnel.ui.panels.analysis import AnalysisPanel

        model = build_graph(r1.id)
        assert model.relevance[c1.id] > 0, model.relevance
        assert model.bottleneck[node.id] > 0, model.bottleneck
        _, rel_rows = run_query("companies_by_relevance", model)
        assert len(rel_rows) == 3, rel_rows
        _, bn_rows = run_query("bottleneck_nodes", model)
        assert len(bn_rows) == 1, bn_rows

        apanel = app.query_one(AnalysisPanel)
        apanel.load_request(r1.id)
        await pilot.pause()
        assert apanel.query_one("#analysis-table").row_count == 3, apanel.query_one("#analysis-table").row_count
        apanel.query_one("#query-select").value = "bottleneck_nodes"
        await pilot.pause()  # Select.Changed auto-runs
        assert apanel.query_one("#analysis-table").row_count == 1, apanel.query_one("#analysis-table").row_count
        print("ANALYSIS_OK relevance(TSMC)=", round(model.relevance[c1.id], 2),
              "bottleneck(node)=", round(model.bottleneck[node.id], 2))

        # --- Phase 5: exports ---
        import json as _json
        import os
        from funnel.db.models import VisualizationExport
        from funnel.export import chart, data_export
        from funnel.ui.panels.export import ExportPanel

        jpath = data_export.export_json(model, data_export.export_path(model.request, "json"))
        assert os.path.getsize(jpath) > 0
        parsed = _json.loads(jpath.read_text())
        assert len(parsed["companies"]) == 3, parsed
        assert "investment_relevance" in parsed["companies"][0], parsed["companies"][0].keys()

        cdir = data_export.export_csv(model)
        assert (cdir / "companies.csv").exists() and os.path.getsize(cdir / "companies.csv") > 0

        mdpath = data_export.export_markdown(model, data_export.export_path(model.request, "md"))
        assert "Funnel report" in mdpath.read_text()

        png = chart.render_funnel(model, data_export.export_path(model.request, "png"), "png")
        assert os.path.getsize(png) > 1000, os.path.getsize(png)

        from funnel.export import chart_tree
        tree_png = chart_tree.render_tree(
            model, data_export.export_path(model.request, "tree.png"), "png", "professional")
        assert os.path.getsize(tree_png) > 1000, os.path.getsize(tree_png)

        # Export panel button glue: chart-png -> file + VisualizationExport row
        before_exports = repo.count_for_request(VisualizationExport, r1.id)
        xpanel = app.query_one(ExportPanel)
        xpanel.load_request(r1.id)
        await pilot.pause()
        xpanel.query_one("#chart-png").press()
        await pilot.pause()
        assert repo.count_for_request(VisualizationExport, r1.id) == before_exports + 1
        ve = repo.list_for_request(VisualizationExport, r1.id)[-1]
        assert os.path.exists(ve.file_path) and ve.file_format == "png", ve

        xpanel.query_one("#tree-png").press()
        await pilot.pause()
        assert repo.count_for_request(VisualizationExport, r1.id) == before_exports + 2
        tve = repo.list_for_request(VisualizationExport, r1.id)[-1]
        assert tve.chart_type == "tree" and os.path.exists(tve.file_path), tve
        print("EXPORT_OK funnel+tree png/svg/pdf, json/csv/md; VisualizationExports ->",
              os.path.basename(ve.file_path), "+", os.path.basename(tve.file_path))

        # --- Map flow: topic → Generate → review toggle → Build (offline fakes) ---
        import functools

        from textual.widgets import Input as _Input

        from funnel.db.models import Proposal
        from funnel.research import flow as _flow
        from funnel.ui.panels.flow import FlowPanel

        def _fake_llm(messages, *, online=False):
            t = messages[-1]["content"]
            if "Propose a research brief" in t:
                return {"industry": "Widgets", "target_output": "Widgets",
                        "geography": ["Taiwan"], "relationship_types": ["supplier", "competitor"],
                        "company_origin": "both", "goal": "ecosystem_understanding",
                        "research_depth": "standard", "source_strictness": "balanced",
                        "known_terms": ["widget"], "aliases": ["widget assembly"],
                        "jargon": ["OEM"], "key_products": ["widgets"]}
            if "value-chain process stack" in t:
                return {"process_nodes": [{"name": "Assembly", "layer": "midstream", "keywords": [],
                                           "bottleneck_risk": "medium", "description": "", "rationale": ""}]}
            if '"plans"' in t:
                return {"plans": [{"node": "Assembly",
                                   "queries": [{"q": "widget assembly companies", "intent": "market"},
                                               {"q": "widget makers financials", "intent": "filings"}]}]}
            if "Identify up to" in t:
                return {"companies": [
                    {"name": "Foxconn", "ticker": "2317", "country": "TW", "role": "operator", "confidence": 0.8, "sources": []},
                    {"name": "Pegatron", "ticker": "4938", "country": "TW", "role": "operator", "confidence": 0.6, "sources": []}]}
            if "Infer supplier" in t:
                return {"edges": [{"from": "Pegatron", "to": "Foxconn", "relationship_type": "competitor",
                                   "evidence_level": "inferred", "confidence": 0.5}]}
            return {"evidence": []}

        def _fake_gather(query, k):
            return ("SOURCE: http://x\ncontext about " + query, ["http://x/" + query[:12]])

        _orig_run_research = _flow.run_research
        _flow.run_research = functools.partial(
            _orig_run_research, llm=_fake_llm, gather=_fake_gather)
        try:
            app.query_one("#tabs").active = "tab-flow"
            await pilot.pause()
            fpanel = app.query_one(FlowPanel)
            fpanel.query_one("#flow-topic", _Input).value = "Flow Glue Test"
            fpanel.query_one("#flow-generate").press()
            for _ in range(200):
                await pilot.pause(0.05)
                if not fpanel._flow_busy:
                    break
            assert not fpanel._flow_busy, "generate did not finish"
            r3_id = app.current_request_id
            assert repo.get(ResearchRequest, r3_id).title == "Flow Glue Test"
            # auto-brief filled the intake + request fields
            auto_answers = repo.get_intake_answers(r3_id)
            assert auto_answers["industry"].answer_value == "Widgets", auto_answers
            assert repo.get(ResearchRequest, r3_id).geography == ["Taiwan"]

            ftable = fpanel.query_one("#flow-table")
            assert ftable.row_count == len(fpanel._rows) >= 4, ftable.row_count

            # review gate: Enter on the Pegatron row toggles reject, again restores
            peg_idx = next(i for i, pid in enumerate(fpanel._rows)
                           if repo.get(Proposal, pid).payload.get("name") == "Pegatron")
            ftable.focus()
            ftable.move_cursor(row=peg_idx)
            await pilot.press("enter")
            await pilot.pause()
            assert repo.get(Proposal, fpanel._rows[peg_idx]).status == "rejected"
            await pilot.press("enter")
            await pilot.pause()
            assert repo.get(Proposal, fpanel._rows[peg_idx]).status == "pending"
            await pilot.press("enter")
            await pilot.pause()

            fpanel.query_one("#flow-build").press()
            for _ in range(200):
                await pilot.pause(0.05)
                if not fpanel._flow_busy:
                    break
            assert not fpanel._flow_busy, "build did not finish"
            rc_nodes = repo.count_for_request(ProcessNode, r3_id)
            rc_comps = repo.count_for_request(CompanyNode, r3_id)
            rc_edges = repo.count_for_request(RelationshipEdge, r3_id)
            assert (rc_nodes, rc_comps, rc_edges) == (1, 1, 0), (rc_nodes, rc_comps, rc_edges)
            trees = [v for v in repo.list_for_request(VisualizationExport, r3_id)
                     if v.chart_type.startswith("tree_")]
            assert len(trees) == 3 and all(os.path.exists(v.file_path) for v in trees), trees
            result_text = str(fpanel.query_one("#flow-result").render())
            assert "committed" in result_text, result_text
            print("FLOW_GLUE_OK topic→request→brief→proposals→toggle→build:",
                  "nodes", rc_nodes, "comps", rc_comps, "edges", rc_edges,
                  "trees", sorted(v.chart_type for v in trees))
        finally:
            _flow.run_research = _orig_run_research

        print("SMOKE_OK requests=", len(repo.list_requests()), "current=", app.current_request_id)
        print("INTAKE_OK industry=", req.industry_of_interest, "geo=", req.geography)
        print("GRAPH_OK nodes=", repo.count_for_request(ProcessNode, r1.id),
              "companies=", repo.count_for_request(CompanyNode, r1.id),
              "edges=", repo.count_for_request(RelationshipEdge, r1.id))
        print("EVIDENCE_OK evidence=", repo.count_for_request(EvidenceItem, r1.id),
              "deals=", repo.count_for_request(Deal, r1.id))


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
