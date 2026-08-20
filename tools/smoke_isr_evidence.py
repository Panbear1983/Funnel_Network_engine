"""Offline test for ISR evidence import (stage 5c) + dossier resolution.
Proves: screenings of grounded companies become evidence proposals with
preserved dates and isr: sources; commit materializes EvidenceItem rows;
re-running pipeline+commit never duplicates them (the ASCII claim prefix
keeps CJK merge keys unique); disabled env yields zero ISR evidence.
Run: `python tools/smoke_isr_evidence.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="funnel_isr_evidence_")
os.environ.setdefault("FUNNEL_HOME", _TMP)

from isr_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(_TMP)
os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import CompanyNode, EvidenceItem  # noqa: E402
from funnel.research.enrich import enrich_from_isr  # noqa: E402
from funnel.knowledge.isr import IsrKnowledge  # noqa: E402
from funnel.research.ingest import commit_proposals  # noqa: E402
from funnel.research.pipeline import run_pipeline  # noqa: E402
from funnel.ui.panels.dossier import (  # noqa: E402
    resolve_dossier, staleness_line, staleness_suffix,
)


def fake_llm(messages, *, online=False, **_kw):
    text = messages[-1]["content"]
    if "process stack" in text:
        return {"process_nodes": [
            {"name": "Switch manufacturing", "layer": "midstream",
             "keywords": ["networking"], "bottleneck_risk": "medium",
             "description": "", "rationale": ""},
        ]}
    if "Identify up to" in text:
        return {"companies": [
            {"name": "Accton", "ticker": None, "country": None,
             "role": "operator", "confidence": 0.8, "sources": []},
            {"name": "台積電", "ticker": "2330", "country": "Taiwan",
             "role": "supplier", "confidence": 0.9, "sources": []},
        ]}
    return {}


def fake_gather(query, k):
    return (f"SOURCE: http://s\nText about {query}.", ["http://s"])


def _one_round(request_id: str):
    run = run_pipeline(request_id, "high_level", llm=fake_llm,
                       gather=fake_gather, online_fallback=False)
    assert run.status in ("complete", "partial"), run.status
    counts = commit_proposals(request_id, run_id=run.id)
    return run, counts


def run() -> None:
    init_db()
    r = repo.create_request("ISR evidence smoke")
    r.industry_of_interest = "Networking hardware"
    r = repo.update(r)

    run1, _counts = _one_round(r.id)

    # Evidence proposals staged from fixture screenings (1 for Accton,
    # 1 for TSMC — needs_update rows only).
    props = repo.list_proposals(r.id, run_id=run1.id)
    ev_props = [p for p in props if p.entity_type == "evidence"]
    isr_ev = [p for p in ev_props if any(s.startswith("isr:") for s in p.sources)]
    assert len(isr_ev) == 2, [(p.ref_key, p.sources) for p in ev_props]
    accton_ev = next(p for p in isr_ev if "isr:2345" in p.sources)
    assert accton_ev.payload["claim"].startswith("[ISR 2345 2026-08-04]"), accton_ev.payload
    assert accton_ev.payload["date"] == "2026-08-01"          # evidence_date preserved
    assert accton_ev.payload["publisher"] == "ISR maintenance screening"
    assert "https://example.com/accton-order" in accton_ev.sources
    # CJK reason did not collapse the ref key into a collision.
    tsmc_ev = next(p for p in isr_ev if "isr:2330" in p.sources)
    assert accton_ev.ref_key != tsmc_ev.ref_key, (accton_ev.ref_key, tsmc_ev.ref_key)

    evs = repo.list_for_request(EvidenceItem, r.id)
    isr_rows = [e for e in evs if e.publisher == "ISR maintenance screening"]
    assert len(isr_rows) == 2, [(e.claim, e.publisher) for e in evs]
    assert all(e.related_companies for e in isr_rows)

    # Idempotency: second Generate + Build must merge, not duplicate.
    _run2, _counts2 = _one_round(r.id)
    evs2 = repo.list_for_request(EvidenceItem, r.id)
    isr_rows2 = [e for e in evs2 if e.publisher == "ISR maintenance screening"]
    assert len(isr_rows2) == 2, [(e.claim) for e in isr_rows2]

    # Dossier resolution: live lookup, then stored fallback.
    comps = repo.list_for_request(CompanyNode, r.id)
    accton_c = next(c for c in comps if c.name == "Accton")
    bundle = resolve_dossier(accton_c)
    assert bundle and bundle["live"], bundle
    assert "Clients & Orders" in bundle["fields"]
    assert bundle["screenings"], bundle
    assert "ISR dossier" in staleness_line(bundle["updated_at"])
    assert "unknown" in staleness_line(None)

    # Staleness thresholds (all four branches) — dates chosen relative to a
    # 2026-mid 'now' so the age bands are stable.
    assert "aging" in staleness_line("2026-06-20")      # ~30-90d -> yellow
    assert "stale" in staleness_line("2026-01-01")      # >90d -> red
    assert staleness_line("not-a-date").startswith("[dim]")
    assert "ago" in staleness_suffix("2026-08-10") and "green" in staleness_suffix("2026-08-10")
    assert "stale" in staleness_suffix("2026-01-01")
    assert staleness_suffix(None) == "" and staleness_suffix("garbage") == ""

    # enrich exchange fallback: bare '2330' has no suffix, so the label must
    # come from the yahoo form '2330.TW' -> TWSE.
    enrich_from_isr(comps, IsrKnowledge.open_default())
    tsmc_c = repo.get(CompanyNode, next(c.id for c in comps if c.name == "台積電"))
    assert tsmc_c.exchange == "TWSE", tsmc_c.exchange

    # Stored-copy fallback with the connector disabled.
    os.environ["FUNNEL_ISR_DB"] = ""
    import funnel.knowledge.isr as isr_mod
    isr_mod._cache = None
    accton_c2 = repo.get(CompanyNode, accton_c.id)
    bundle2 = resolve_dossier(accton_c2)
    assert bundle2 and not bundle2["live"], bundle2
    assert bundle2["fields"].get("Core Business"), bundle2

    # Disabled run stages zero ISR evidence.
    r3 = repo.create_request("disabled evidence")
    r3.industry_of_interest = "Networking hardware"
    repo.update(r3)
    run3, _ = _one_round(r3.id)
    props3 = repo.list_proposals(r3.id, run_id=run3.id)
    assert not any(p.entity_type == "evidence" and
                   any(s.startswith("isr:") for s in p.sources) for p in props3)

    os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)
    print("ISR_EVIDENCE_OK staged+committed+idempotent, dossier live+stored, "
          "disabled parity")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
