"""Offline UI regression test for novice navigation and bounded layouts.

Mount the app at a normal terminal viewport and assert the Map-primary
workspace hierarchy, fixed Intake save action, toolbar layout, and
novice-facing Map action labels.
Run: `python tools/smoke_ui.py`."""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

os.environ.setdefault("FUNNEL_HOME", tempfile.mkdtemp(prefix="funnel_ui_"))
# Keep the offline test deterministic: never auto-probe a real ISR sibling DB.
os.environ.setdefault("FUNNEL_ISR_DB", "")

from funnel.db import repo  # noqa: E402
from funnel.db.models import Proposal, ResearchRun, ResearchRunOutcome  # noqa: E402
from funnel.intake.questions import RESEARCH_CONTEXT_ID  # noqa: E402
from funnel.ui.app import FunnelApp  # noqa: E402
from funnel.ui.panels.flow import FlowPanel, _outcome_guidance, _plain_stage  # noqa: E402
from funnel.ui.panels.graph import ProcessNodePanel  # noqa: E402
from funnel.ui.panels.intake import IntakePanel  # noqa: E402
from funnel.ui.sidebar import Sidebar  # noqa: E402
from textual.widgets import (  # noqa: E402
    Collapsible,
    Switch,
    Tab,
    TabbedContent,
    Tabs,
)


async def run() -> None:
    app = FunnelApp()
    async with app.run_test(size=(100, 30)) as pilot:
        await pilot.pause()
        breadcrumb = str(app.query_one("#breadcrumb").render())
        assert "Build Map" in breadcrumb and "Generate" not in breadcrumb, breadcrumb

        # Novices land in Map and see only one secondary top-level workspace.
        main_tabs = app.query_one("#tabs", TabbedContent)
        main_tab_bar = main_tabs.query_one(Tabs)
        assert main_tabs.active == "tab-flow", main_tabs.active
        assert [str(tab.label) for tab in main_tab_bar.query(Tab)] == [
            "Map", "Tree", "Advanced",
        ]

        # Existing manual capabilities stay available, one flat level down.
        main_tabs.active = "tab-advanced"
        await pilot.pause()
        details_tabs = app.query_one("#advanced-tabs", TabbedContent)
        details_tab_bar = details_tabs.query_one(Tabs)
        assert [str(tab.label) for tab in details_tab_bar.query(Tab)] == [
            "Refine", "Nodes", "Companies", "Relations",
            "Evidence", "Deals", "Analysis", "Export",
        ]

        # Flattened dashboard: never more than two tab levels anywhere.
        for tc in app.query(TabbedContent):
            depth = sum(1 for a in tc.ancestors if isinstance(a, TabbedContent))
            assert depth <= 1, ("nested tabs too deep", tc.id, depth)

        # F2 toggles Map <-> Advanced from anywhere.
        await pilot.press("f2")
        assert main_tabs.active == "tab-flow", main_tabs.active
        await pilot.press("f2")
        assert main_tabs.active == "tab-advanced", main_tabs.active

        r = repo.create_request("UI Layout Check")
        app.query_one(Sidebar).reload(select_id=r.id)
        await pilot.pause()

        # Sidebar New vs Delete (should already be equal — width:1fr)
        sb = app.query_one(Sidebar)
        new_w = sb.query_one("#new-req").size.width
        del_w = sb.query_one("#del-req").size.width
        assert new_w == del_w, ("sidebar", new_w, del_w)

        # The questionnaire owns the scrollable space while Save intake stays
        # fixed immediately below it and wholly above the footer.
        details_tabs.active = "adv-intake"
        await pilot.pause()
        intake = app.query_one(IntakePanel)
        for section in intake.query(Collapsible):
            section.collapsed = False
        await pilot.pause()
        intake_form = intake.query_one("#intake-form")
        save_intake = intake.query_one("#save-intake")
        footer = app.query_one("Footer")
        assert intake_form.virtual_size.height > intake_form.size.height, (
            intake_form.virtual_size, intake_form.size)
        intake_form.scroll_end(animate=False)
        await pilot.pause()
        assert intake_form.scroll_y > 0, intake_form.scroll_y
        assert intake_form.region.bottom <= save_intake.region.y, (
            intake_form.region, save_intake.region)
        assert save_intake.region.bottom <= footer.region.y, (
            save_intake.region, footer.region)
        save_bottom = save_intake.region.bottom
        footer_top = footer.region.y

        # Graph CRUD toolbar remains available within the Advanced workspace.
        details_tabs.active = "adv-nodes"
        await pilot.pause()
        gp = app.query_one(ProcessNodePanel)
        add_w = gp.query_one("#crud-add").size.width
        edit_w = gp.query_one("#crud-edit").size.width
        deld_w = gp.query_one("#crud-delete").size.width
        assert edit_w == deld_w, ("crud edit/delete", edit_w, deld_w)
        assert edit_w >= 12, ("crud thin", edit_w)
        assert add_w >= edit_w, ("add smaller than edit?", add_w, edit_w)

        # The beginner view exposes only topic, optional context, depth, and
        # one Build Map action. Proposal mechanics live in a collapsed path.
        main_tabs.active = "tab-flow"
        await pilot.pause()
        fp = app.query_one(FlowPanel)
        topic_w = fp.query_one("#flow-topic").size.width
        depth_w = fp.query_one("#flow-depth").size.width
        gen = fp.query_one("#flow-generate")
        finish = fp.query_one("#flow-build")
        context = fp.query_one("#flow-context")
        assert str(gen.label) == "Build Map", gen.label
        assert not gen.disabled, "Build Map must retain requests when credentials are absent"
        assert str(finish.label) == "Finish Reviewed Map", finish.label
        assert "(optional)" in (context.placeholder or ""), context.placeholder
        advanced = fp.query_one("#flow-advanced-review", Collapsible)
        advanced_contents = advanced.query_one(Collapsible.Contents)
        assert advanced.collapsed is True
        assert fp.query_one("#auto-build", Switch).value is True
        assert not advanced_contents.display, (
            "advanced proposal controls leaked into beginner view")
        assert gen.visible, "the single beginner Build Map action must be visible"
        assert context.value == ""
        assert RESEARCH_CONTEXT_ID not in repo.get_intake_answers(r.id)
        context.value = "Focus on suppliers with durable switching costs."
        fp._save_research_context(r.id, context.value)
        fp.load_request(r.id)
        assert context.value == "Focus on suppliers with durable switching costs."
        assert repo.get_intake_answers(r.id)[RESEARCH_CONTEXT_ID].answer_value == context.value
        assert topic_w >= 25, ("topic input too narrow", topic_w)
        assert depth_w == 26, ("depth select", depth_w)
        assert gen.size.width >= 12, gen.size.width

        # The Map result explains only stored coverage/provenance: usable
        # findings, source-backed evidence, gaps, and the detail inspection path.
        stored_run = repo.add(ResearchRun(
            request_id=r.id,
            status=ResearchRunOutcome.PARTIAL.value,
            summary=(
                "partial: process_node 1. Skipped: "
                "auto-brief: ValueError: malformed offline fixture"
            ),
            log=["Skipped source gathering: RuntimeError: offline source unavailable"],
            counts={"process_node": 1, "company": 3, "edge": 1, "evidence": 2},
        ))
        stored = [
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="process_node",
                     ref_key="assembly", payload={"name": "Assembly"}),
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="company",
                     ref_key="alpha", payload={"name": "Alpha"}),
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="company",
                     ref_key="beta", payload={"name": "Beta"}),
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="company",
                     ref_key="screened", payload={"name": "Screened"}, status="rejected"),
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="edge",
                     ref_key="alpha->beta",
                     payload={"from_company": "alpha", "to_company": "beta",
                              "evidence_level": "confirmed"}),
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="evidence",
                     ref_key="sourced", payload={
                         "claim": "Alpha supplies Beta",
                         "source_url": "https://filings.example.test/alpha-beta",
                         "related_companies": ["alpha", "beta"],
                     }),
            Proposal(run_id=stored_run.id, request_id=r.id, entity_type="evidence",
                     ref_key="unsourced", payload={
                         "claim": "Capacity may be constrained",
                         "source_url": "",
                         "related_companies": ["alpha"],
                     }),
        ]
        for proposal in stored:
            repo.add(proposal)
        fp.load_request(r.id)
        await pilot.pause()
        coverage = str(fp.query_one("#flow-coverage").render())
        assert "Found: 1 process step, 2 companies, and 1 relationship" in coverage, coverage
        assert "1 of 2 saved evidence items include a source link" in coverage, coverage
        assert "2 of 2 companies and 1 of 1 relationships" in coverage, coverage
        assert "1 evidence item has no source link" in coverage, coverage
        assert "source gathering, auto-brief was unavailable" in coverage, coverage
        assert "malformed offline fixture" not in coverage, coverage
        assert "Advanced → Evidence" in coverage, coverage
        assert "press v" in coverage, coverage
        assert fp.query_one("#flow-coverage").size.height >= 4, (
            fp.query_one("#flow-coverage").size,
            fp.query_one("#flow-coverage").styles.height,
        )

        # Internal progress is translated to a small, stable set of
        # beginner-facing stages while the detailed log remains available.
        expected_stages = {
            "Understanding the topic…": "Stage 1 of 5",
            "Discovering process stack…": "Stage 2 of 5",
            "Researching companies for 'Assembly'…": "Stage 3 of 5",
            "Extracting evidence…": "Stage 4 of 5",
            "Tree chart (professional) → map.png": "Stage 5 of 5",
        }
        fp._set_busy(True)
        for progress_line, expected in expected_stages.items():
            assert expected in (_plain_stage(progress_line) or ""), progress_line
            fp._log_line(progress_line)
            assert expected in str(fp.query_one("#flow-stage").render()), progress_line
        fp._set_busy(False)
        fp._set_active_stage(None)

        # Every explicit outcome has exactly one visible next action.
        for outcome in ResearchRunOutcome:
            guidance = _outcome_guidance(
                outcome.value, auto_build=False, key_name="TEST_API_KEY")
            assert guidance.count("Next:") == 1, (outcome, guidance)
            if outcome in {
                ResearchRunOutcome.COMPLETE,
                ResearchRunOutcome.PARTIAL,
            }:
                assert "Finish Reviewed Map" in guidance, (outcome, guidance)

        # A completed default run still proceeds directly to finalization.
        auto_actions: list[str] = []
        fp._render_proposals = lambda keep_cursor=False: setattr(  # type: ignore[method-assign]
            fp, "_rows", ["offline-proposal"])
        fp._start_build = lambda: auto_actions.append("finish")  # type: ignore[method-assign]
        fp._generate_done("offline-run", "complete", "offline UI check")
        assert auto_actions == ["finish"], auto_actions
        assert "Next:" in str(fp.query_one("#flow-result").render())

        # Partial output is buildable, while credential/no-result states retain
        # the request and explain a recovery path without finalizing proposals.
        fp._generate_done(
            "partial-run", ResearchRunOutcome.PARTIAL.value,
            "partial: process_node 1. Skipped: evidence extraction")
        assert auto_actions == ["finish", "finish"], auto_actions
        partial_guidance = str(fp.query_one("#flow-result").render())
        assert "usable items were saved" in partial_guidance
        assert "Next:" in partial_guidance
        fp._generate_done(
            "credential-run", ResearchRunOutcome.NEEDS_CREDENTIALS.value,
            "needs credentials: request saved")
        assert auto_actions == ["finish", "finish"], auto_actions
        credential_guidance = str(fp.query_one("#flow-result").render())
        assert "request saved" in credential_guidance.casefold()
        assert "Next:" in credential_guidance
        fp._generate_done(
            "empty-run", ResearchRunOutcome.NO_CREDIBLE_RESULTS.value,
            "no credible results: request saved")
        assert auto_actions == ["finish", "finish"], auto_actions
        no_result_guidance = str(fp.query_one("#flow-result").render())
        assert "No credible map items" in no_result_guidance
        assert "Next:" in no_result_guidance
        fp._generate_done(
            "failed-run", ResearchRunOutcome.FAILED.value,
            "failed: request saved")
        assert auto_actions == ["finish", "finish"], auto_actions
        failed_guidance = str(fp.query_one("#flow-result").render())
        assert "Research stopped" in failed_guidance
        assert "Next:" in failed_guidance

        # Expanding the explicit advanced path restores the manual controls.
        advanced.collapsed = False
        await pilot.pause()
        assert advanced_contents.display
        assert str(fp.query_one(".switch-label").render()) == "build automatically"

        print("UI_OK  top-level=Map + Advanced (flat)",
              "| intake-save-bottom/footer=", save_bottom, footer_top,
              "| sidebar new/del=", new_w, del_w,
              "| crud add/edit/del=", add_w, edit_w, deld_w,
              "| flow topic/depth/build=", topic_w, depth_w, gen.size.width)


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
