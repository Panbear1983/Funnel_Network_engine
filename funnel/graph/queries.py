"""Canned analysis queries over a GraphModel. Each returns (columns, rows)."""

from __future__ import annotations

from funnel.sources import strongest_source

# (label, key) — order shown in the Analysis dropdown.
QUERIES = [
    ("Companies by investment relevance", "companies_by_relevance"),
    ("Bottleneck nodes (ranked)", "bottleneck_nodes"),
    ("Nodes with no confirmed edges", "nodes_no_confirmed"),
    ("Weak / inferred relationships", "weak_edges"),
    ("Public companies by layer", "public_by_layer"),
    ("Companies by evidence quality", "companies_by_evidence"),
]

_WEAK_LEVELS = ("unknown", "inferred", "weak")


def _company_name(m, cid: str) -> str:
    c = m.company_by_id.get(cid)
    return c.name if c else (cid or "?")


def companies_by_relevance(m):
    cols = ("Company", "Ticker", "Role", "Relevance", "Evidence")
    ordered = sorted(m.companies, key=lambda c: m.relevance.get(c.id, 0.0), reverse=True)
    rows = [
        (c.name, c.ticker or "—", c.role, f"{m.relevance.get(c.id, 0.0):.2f}",
         str(len(m.evidence_by_company.get(c.id, []))))
        for c in ordered
    ]
    return cols, rows


def bottleneck_nodes(m):
    cols = ("Node", "Layer", "Bottleneck", "Companies")
    ordered = sorted(m.nodes, key=lambda n: m.bottleneck.get(n.id, 0.0), reverse=True)
    rows = [
        (n.name, n.layer, f"{m.bottleneck.get(n.id, 0.0):.2f}",
         str(m.company_count_by_node.get(n.id, 0)))
        for n in ordered
    ]
    return cols, rows


def nodes_no_confirmed(m):
    cols = ("Node", "Layer", "Companies")
    confirmed = set()
    for e in m.edges:
        if e.evidence_level == "confirmed":
            confirmed.add(e.from_company)
            confirmed.add(e.to_company)
    rows = []
    for n in m.nodes:
        members = [c for c in m.companies if n.id in c.process_node_ids]
        if not any(c.id in confirmed for c in members):
            rows.append((n.name, n.layer, str(len(members))))
    return cols, rows


def weak_edges(m):
    cols = ("From", "Type", "To", "Evidence", "Strength")
    rows = [
        (_company_name(m, e.from_company), e.relationship_type, _company_name(m, e.to_company),
         e.evidence_level, f"{m.edge_strength.get(e.id, 0.0):.2f}")
        for e in m.edges if e.evidence_level in _WEAK_LEVELS
    ]
    return cols, rows


def public_by_layer(m):
    cols = ("Company", "Ticker", "Exchange", "Layer(s)")

    def layers_for(c):
        layers = sorted({m.node_by_id[nid].layer for nid in c.process_node_ids if nid in m.node_by_id})
        return ", ".join(layers) or "—"

    public = [c for c in m.companies if c.ticker]
    public.sort(key=layers_for)
    rows = [(c.name, c.ticker, c.exchange or "—", layers_for(c)) for c in public]
    return cols, rows


def companies_by_evidence(m):
    cols = ("Company", "Evidence", "Strongest", "Quality")
    ordered = sorted(m.companies, key=lambda c: m.source_quality.get(c.id, 0.0), reverse=True)
    rows = []
    for c in ordered:
        evs = m.evidence_by_company.get(c.id, [])
        rows.append((c.name, str(len(evs)), strongest_source(evs) or "—",
                     f"{m.source_quality.get(c.id, 0.0):.2f}"))
    return cols, rows


_DISPATCH = {
    "companies_by_relevance": companies_by_relevance,
    "bottleneck_nodes": bottleneck_nodes,
    "nodes_no_confirmed": nodes_no_confirmed,
    "weak_edges": weak_edges,
    "public_by_layer": public_by_layer,
    "companies_by_evidence": companies_by_evidence,
}


def run_query(key: str, model):
    return _DISPATCH[key](model)
