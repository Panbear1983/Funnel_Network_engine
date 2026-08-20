"""Offline test for the ISR-seed path (no network, no LLM). Seeds a map from an
ISR theme, asserting: companies grouped into tier nodes, dossiers attached,
tree charts exported, and re-seeding merges idempotently. This is the low-input
'a map exists before any paid research' path. Run: `python tools/smoke_isr_seed.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="funnel_isr_seed_")
os.environ.setdefault("FUNNEL_HOME", _TMP)

from isr_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(_TMP)
os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import CompanyNode, ProcessNode  # noqa: E402
from funnel.knowledge.isr import IsrCompany, IsrKnowledge  # noqa: E402
from funnel.services.isr_seed import run_isr_seed, seed_proposals  # noqa: E402


def _isr_company(cid, name, tier="potential"):
    return IsrCompany(
        company_id=cid, name=name, raw_name=name, ticker="", base_ticker="",
        yahoo_ticker="", country="China", industry="半導體業", tier=tier,
        raw_tier="潛力股", research_status="deep_researched",
        dossier_updated_at=None)


def run() -> None:
    init_db()
    isr = IsrKnowledge.open_default()

    # seed_proposals is pure: 半導體業 theme -> 2 tier nodes, 3 companies.
    theme = isr.by_theme(industry="半導體業")
    props = seed_proposals(theme)
    node_props = [p for p in props if p["entity_type"] == "process_node"]
    comp_props = [p for p in props if p["entity_type"] == "company"]
    assert {p["payload"]["name"] for p in node_props} == {"Leaders (ISR)", "Potential (ISR)"}
    assert len(comp_props) == 3, comp_props
    # ref_key is ticker-bearing (survives CJK): 台積電 (2330) -> '2330'
    tsmc = next(p for p in comp_props if "台積電" in p["payload"]["name"])
    assert tsmc["ref_key"] == "2330", tsmc["ref_key"]
    assert tsmc["payload"]["ticker"] == "2330.TW"

    # Regression: two tickerless pure-CJK companies must NOT collapse to ref
    # "x" (slugify of a bare CJK name) — they are id-disambiguated.
    tickerless = seed_proposals([_isr_company(101, "上海新陽"),
                                 _isr_company(102, "中芯國際")])
    tl_comp = [p for p in tickerless if p["entity_type"] == "company"]
    tl_refs = {p["ref_key"] for p in tl_comp}
    assert len(tl_comp) == 2 and len(tl_refs) == 2, [p["ref_key"] for p in tl_comp]
    assert "x" not in tl_refs, tl_refs

    # Full offline seed: commit + dossiers + charts.
    r = repo.create_request("半導體業")
    result = run_isr_seed(r.id, industry="半導體業", isr=isr)
    assert result["status"] == "ok", result
    assert result["process_node"] == 2 and result["company"] == 3, result
    assert set(result.get("charts", {})) == {"high", "professional", "deep"}, result
    assert os.path.exists(result["charts"]["professional"])
    assert os.path.exists(result["report"])

    nodes = repo.list_for_request(ProcessNode, r.id)
    comps = repo.list_for_request(CompanyNode, r.id)
    assert len(nodes) == 2 and len(comps) == 3
    # every company links to a node and carries its ISR dossier
    tsmc_c = next(c for c in comps if "台積電" in c.name)
    assert tsmc_c.process_node_ids and tsmc_c.ticker == "2330.TW"
    assert (tsmc_c.financials or {}).get("isr", {}).get("dossier"), tsmc_c.financials

    # Idempotent: re-seed the same theme merges, no duplicates.
    result2 = run_isr_seed(r.id, industry="半導體業", isr=isr, export=False)
    assert result2["status"] == "ok", result2
    assert repo.count_for_request(ProcessNode, r.id) == 2, "nodes duplicated"
    assert repo.count_for_request(CompanyNode, r.id) == 3, "companies duplicated"

    # Tier filter narrows the theme.
    r2 = repo.create_request("leaders only")
    res3 = run_isr_seed(r2.id, industry="半導體業", tier="leader", isr=isr, export=False)
    assert res3["company"] == 2, res3               # 台積電 + NVIDIA
    assert res3["process_node"] == 1, res3          # only the Leaders node

    # Empty theme + disabled connector degrade cleanly.
    r3 = repo.create_request("empty")
    assert run_isr_seed(r3.id, industry="does-not-exist", isr=isr)["status"] == "empty"
    os.environ["FUNNEL_ISR_DB"] = ""
    import funnel.knowledge.isr as isr_mod
    isr_mod._cache = None
    assert run_isr_seed(r3.id)["status"] == "no_isr"
    os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

    print("ISR_SEED_OK theme->tier-nodes+companies+dossiers+charts, idempotent, "
          "tier-filter, empty/disabled graceful")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
