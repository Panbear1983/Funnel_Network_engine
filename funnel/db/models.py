"""Core data model — a direct implementation of docs/schema_v1.md.

All cross-entity references are stored as plain string ids (or JSON lists of ids)
rather than ORM relationships. This keeps every attribute eagerly loaded, so
objects stay usable after their Session closes (no DetachedInstanceError), which
suits a manual research tool where a single request holds at most a few hundred
rows. List/dict fields are persisted as JSON columns.

`title` on ResearchRequest is a small UX extension over the schema so the sidebar
has a human label before the intake has been filled in.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Optional

from sqlalchemy import JSON
from sqlmodel import Field, SQLModel


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


class ResearchRequest(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("request"), primary_key=True)
    title: str = ""
    industry_of_interest: str = ""
    target_output: str = ""
    geography: list[str] = Field(default_factory=list, sa_type=JSON)
    research_depth: str = "standard"
    investment_focus: bool = True
    public_company_preference: str = "include_private_and_public"
    relationship_types: list[str] = Field(default_factory=list, sa_type=JSON)
    source_strictness: str = "balanced"
    created_at: datetime = Field(default_factory=datetime.now)


class IntakeAnswer(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("answer"), primary_key=True)
    request_id: str = Field(index=True)
    section: str = ""
    question_id: str = ""
    question_text: str = ""
    answer_value: str = ""


class ProcessNode(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("process_node"), primary_key=True)
    request_id: str = Field(index=True)
    name: str = ""
    layer: str = "midstream"
    description: str = ""
    inputs: list[str] = Field(default_factory=list, sa_type=JSON)
    outputs: list[str] = Field(default_factory=list, sa_type=JSON)
    keywords: list[str] = Field(default_factory=list, sa_type=JSON)
    required_capabilities: list[str] = Field(default_factory=list, sa_type=JSON)
    bottleneck_risk: str = "unknown"
    confidence_score: float = 0.0


class CompanyNode(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("company"), primary_key=True)
    request_id: str = Field(index=True)
    name: str = ""
    ticker: Optional[str] = None
    exchange: Optional[str] = None
    country: Optional[str] = None
    process_node_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    role: str = "operator"
    capability_status: str = "unknown"
    revenue_purity_estimate: Optional[float] = None
    market_share_estimate: Optional[float] = None
    customer_concentration: Optional[str] = None
    confidence_score: float = 0.0
    # Fetched fundamentals (market_cap, revenue, gross_margin, …) — Phase C.
    financials: dict = Field(default_factory=dict, sa_type=JSON)


class RelationshipEdge(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("edge"), primary_key=True)
    request_id: str = Field(index=True)
    from_company: str = ""
    to_company: str = ""
    from_process_node: Optional[str] = None
    to_process_node: Optional[str] = None
    relationship_type: str = "supplier"
    evidence_level: str = "unknown"
    confidence_score: float = 0.0
    deal_ids: list[str] = Field(default_factory=list, sa_type=JSON)


class EvidenceItem(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("evidence"), primary_key=True)
    request_id: str = Field(index=True)
    source_type: str = "news"
    source_url: str = ""
    publisher: str = ""
    date: str = ""
    claim: str = ""
    related_companies: list[str] = Field(default_factory=list, sa_type=JSON)
    related_process_nodes: list[str] = Field(default_factory=list, sa_type=JSON)
    evidence_strength: str = "moderate"
    extracted_quote: str = ""
    confidence_score: float = 0.0


class Deal(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("deal"), primary_key=True)
    request_id: str = Field(index=True)
    deal_type: str = "partnership"
    buyer: str = ""
    supplier: str = ""
    process_node: Optional[str] = None
    product_or_service: str = ""
    announced_date: str = ""
    deal_value: Optional[str] = None
    source_ids: list[str] = Field(default_factory=list, sa_type=JSON)
    confidence_score: float = 0.0


class VisualizationExport(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("export"), primary_key=True)
    request_id: str = Field(index=True)
    chart_type: str = "funnel_network"
    file_format: str = "png"
    file_path: str = ""
    layout_options: dict = Field(default_factory=dict, sa_type=JSON)
    created_at: datetime = Field(default_factory=datetime.now)


# --- Phase 6 web-research staging -----------------------------------------

class ResearchRunOutcome(str, Enum):
    """Explicit terminal outcomes stored in ``ResearchRun.status``."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    NEEDS_CREDENTIALS = "needs_credentials"
    NO_CREDIBLE_RESULTS = "no_credible_results"
    FAILED = "failed"


class ResearchRun(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _new_id("run"), primary_key=True)
    request_id: str = Field(index=True)
    tier: str = "professional"
    model: str = ""
    status: str = "running"  # running, then one of ResearchRunOutcome
    started_at: datetime = Field(default_factory=datetime.now)
    finished_at: Optional[datetime] = None
    summary: str = ""
    log: list = Field(default_factory=list, sa_type=JSON)
    counts: dict = Field(default_factory=dict, sa_type=JSON)


class MapFeedback(SQLModel, table=True):
    """Optional local pilot feedback for one successfully built map.

    This is product feedback, not research evidence. Keeping it in a separate
    table prevents notes about the UI/result from entering claim provenance or
    exported evidence.
    """

    id: str = Field(default_factory=lambda: _new_id("feedback"), primary_key=True)
    request_id: str = Field(index=True)
    run_id: str = Field(index=True)
    useful: bool
    note: str = ""
    created_at: datetime = Field(default_factory=datetime.now)


class Proposal(SQLModel, table=True):
    """A single staged entity awaiting human accept/reject. `payload` mirrors the
    target model's fields; cross-references are stored by `ref_key` (a slug), which
    ingest resolves to real ids on commit."""
    id: str = Field(default_factory=lambda: _new_id("proposal"), primary_key=True)
    run_id: str = Field(index=True)
    request_id: str = Field(index=True)
    entity_type: str = ""  # process_node | company | edge | evidence | deal
    ref_key: str = ""
    payload: dict = Field(default_factory=dict, sa_type=JSON)
    rationale: str = ""
    confidence: float = 0.0
    sources: list = Field(default_factory=list, sa_type=JSON)
    status: str = "pending"  # pending | accepted | rejected | committed | skipped
    created_at: datetime = Field(default_factory=datetime.now)


# Models that hang off a request, used for cascade deletes.
CHILD_MODELS = (
    IntakeAnswer,
    ProcessNode,
    CompanyNode,
    RelationshipEdge,
    EvidenceItem,
    Deal,
    VisualizationExport,
    ResearchRun,
    MapFeedback,
    Proposal,
)
