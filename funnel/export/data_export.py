"""Structured exports: JSON, per-entity CSVs, and a Markdown report.

Scores from the GraphModel are folded into the node/company/edge records so the
exports carry the analysis, not just the raw rows.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from funnel import config


def _slug(text: str) -> str:
    text = (text or "request").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return (text or "request")[:40]


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def export_path(request, ext: str) -> Path:
    config.ensure_dirs()
    return config.EXPORT_DIR / f"{_slug(request.title or request.industry_of_interest or request.id)}_{_stamp()}.{ext}"


def _node_record(m, n) -> dict:
    d = n.model_dump()
    d["bottleneck_score"] = m.bottleneck.get(n.id)
    d["company_count"] = m.company_count_by_node.get(n.id, 0)
    return d


def _company_record(m, c) -> dict:
    d = c.model_dump()
    d["investment_relevance"] = m.relevance.get(c.id)
    d["source_quality"] = m.source_quality.get(c.id)
    d["evidence_count"] = len(m.evidence_by_company.get(c.id, []))
    return d


def _edge_record(m, e) -> dict:
    d = e.model_dump()
    d["relationship_strength"] = m.edge_strength.get(e.id)
    d["from_company_name"] = _name(m, e.from_company)
    d["to_company_name"] = _name(m, e.to_company)
    return d


def _name(m, cid):
    c = m.company_by_id.get(cid)
    return c.name if c else cid


def model_to_dict(m) -> dict:
    return {
        "request": m.request.model_dump(),
        "process_nodes": [_node_record(m, n) for n in m.nodes],
        "companies": [_company_record(m, c) for c in m.companies],
        "relationships": [_edge_record(m, e) for e in m.edges],
        "evidence": [e.model_dump() for e in m.evidence],
        "deals": [d.model_dump() for d in m.deals],
    }


def export_json(m, path: Path) -> Path:
    path.write_text(json.dumps(model_to_dict(m), indent=2, default=str))
    return path


def _cell(value):
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        path.write_text("")
        return
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: _cell(row.get(k)) for k in fieldnames})


def export_csv(m) -> Path:
    """Write one CSV per entity into a timestamped folder; returns the folder."""
    config.ensure_dirs()
    base = config.EXPORT_DIR / f"{_slug(m.request.title or m.request.industry_of_interest or m.request.id)}_{_stamp()}_csv"
    base.mkdir(parents=True, exist_ok=True)
    _write_csv(base / "process_nodes.csv", [_node_record(m, n) for n in m.nodes])
    _write_csv(base / "companies.csv", [_company_record(m, c) for c in m.companies])
    _write_csv(base / "relationships.csv", [_edge_record(m, e) for e in m.edges])
    _write_csv(base / "evidence.csv", [e.model_dump() for e in m.evidence])
    _write_csv(base / "deals.csv", [d.model_dump() for d in m.deals])
    return base


def export_markdown(m, path: Path) -> Path:
    r = m.request
    title = r.title or r.industry_of_interest or r.id
    out: list[str] = [f"# Funnel report — {title}", ""]
    if r.industry_of_interest:
        out.append(f"**Industry:** {r.industry_of_interest}")
    if r.target_output:
        out.append(f"**Target output:** {r.target_output}")
    if r.geography:
        out.append(f"**Geography:** {', '.join(r.geography)}")
    out.append(f"**Depth:** {r.research_depth}  ·  **Source strictness:** {r.source_strictness}")
    out.append("")
    out.append(f"Process nodes: {len(m.nodes)} · Companies: {len(m.companies)} · "
               f"Relationships: {len(m.edges)} · Evidence: {len(m.evidence)} · Deals: {len(m.deals)}")

    out += ["", "## Companies by investment relevance", "",
            "| Company | Ticker | Role | Relevance | Evidence |",
            "| --- | --- | --- | --- | --- |"]
    for c in sorted(m.companies, key=lambda c: m.relevance.get(c.id, 0.0), reverse=True):
        out.append(f"| {c.name} | {c.ticker or '—'} | {c.role} | "
                   f"{m.relevance.get(c.id, 0.0):.2f} | {len(m.evidence_by_company.get(c.id, []))} |")

    out += ["", "## Bottleneck nodes", "",
            "| Node | Layer | Bottleneck | Companies |",
            "| --- | --- | --- | --- |"]
    for n in sorted(m.nodes, key=lambda n: m.bottleneck.get(n.id, 0.0), reverse=True):
        out.append(f"| {n.name} | {n.layer} | {m.bottleneck.get(n.id, 0.0):.2f} | "
                   f"{m.company_count_by_node.get(n.id, 0)} |")

    out += ["", "## Relationships", "",
            "| From | Type | To | Evidence | Strength |",
            "| --- | --- | --- | --- | --- |"]
    for e in m.edges:
        out.append(f"| {_name(m, e.from_company)} | {e.relationship_type} | {_name(m, e.to_company)} | "
                   f"{e.evidence_level} | {m.edge_strength.get(e.id, 0.0):.2f} |")

    if m.evidence:
        out += ["", "## Evidence bibliography", ""]
        for ev in m.evidence:
            ref = ev.source_url or ev.publisher or "(no source)"
            out.append(f"- **{ev.source_type}** — {ev.claim or '(no claim)'} — {ref}"
                       + (f" ({ev.date})" if ev.date else ""))

    path.write_text("\n".join(out) + "\n")
    return path
