"""Assemble a request's entities into a scored in-memory model + NetworkX graph.

Built on demand (a research request holds at most a few hundred rows), consumed
by the analysis queries and the chart/data exporters.
"""

from __future__ import annotations

from dataclasses import dataclass

import networkx as nx

from funnel.db import repo
from funnel.db.models import (
    CompanyNode,
    Deal,
    EvidenceItem,
    ProcessNode,
    RelationshipEdge,
    ResearchRequest,
)
from funnel.scoring import scorer


@dataclass
class GraphModel:
    request: ResearchRequest
    nodes: list
    companies: list
    edges: list
    evidence: list
    deals: list
    node_by_id: dict
    company_by_id: dict
    evidence_by_company: dict
    company_count_by_node: dict
    bottleneck: dict        # node_id  -> score
    relevance: dict         # company_id -> score
    source_quality: dict    # company_id -> score
    edge_strength: dict     # edge_id -> score
    graph: nx.DiGraph


def build_graph(request_id: str) -> GraphModel:
    request = repo.get(ResearchRequest, request_id)
    nodes = repo.list_for_request(ProcessNode, request_id)
    companies = repo.list_for_request(CompanyNode, request_id)
    edges = repo.list_for_request(RelationshipEdge, request_id)
    evidence = repo.list_for_request(EvidenceItem, request_id)
    deals = repo.list_for_request(Deal, request_id)

    node_by_id = {n.id: n for n in nodes}
    company_by_id = {c.id: c for c in companies}

    evidence_by_company: dict[str, list] = {}
    for ev in evidence:
        for cid in (ev.related_companies or []):
            evidence_by_company.setdefault(cid, []).append(ev)

    company_count_by_node = {n.id: 0 for n in nodes}
    for c in companies:
        for nid in c.process_node_ids:
            if nid in company_count_by_node:
                company_count_by_node[nid] += 1

    bottleneck = {
        n.id: scorer.node_bottleneck_score(n, company_count_by_node.get(n.id, 0))
        for n in nodes
    }

    graph = nx.DiGraph()
    for c in companies:
        graph.add_node(c.id, name=c.name, ticker=c.ticker)
    for e in edges:
        if e.from_company in company_by_id and e.to_company in company_by_id:
            graph.add_edge(e.from_company, e.to_company, type=e.relationship_type)

    def deal_count(edge) -> int:
        pair = {edge.from_company, edge.to_company}
        return sum(1 for d in deals if {d.buyer, d.supplier} == pair)

    def evidence_count(edge) -> int:
        return sum(
            1 for ev in evidence
            if edge.from_company in (ev.related_companies or [])
            or edge.to_company in (ev.related_companies or [])
        )

    edge_strength = {
        e.id: scorer.edge_strength(e, deal_count(e), evidence_count(e)) for e in edges
    }

    relevance = {}
    source_quality = {}
    for c in companies:
        evs = evidence_by_company.get(c.id, [])
        degree = graph.degree(c.id) if c.id in graph else 0
        source_quality[c.id] = scorer.company_source_quality(evs)
        relevance[c.id] = scorer.company_investment_relevance(c, bottleneck, evs, degree)

    return GraphModel(
        request=request,
        nodes=nodes,
        companies=companies,
        edges=edges,
        evidence=evidence,
        deals=deals,
        node_by_id=node_by_id,
        company_by_id=company_by_id,
        evidence_by_company=evidence_by_company,
        company_count_by_node=company_count_by_node,
        bottleneck=bottleneck,
        relevance=relevance,
        source_quality=source_quality,
        edge_strength=edge_strength,
        graph=graph,
    )
