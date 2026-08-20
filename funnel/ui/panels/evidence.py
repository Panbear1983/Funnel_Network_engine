"""Phase 3 — evidence items and deals, each linkable to companies / nodes."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.widgets import Input, Label, SelectionList
from textual.widgets.selection_list import Selection

from funnel.db import repo
from funnel.db.models import CompanyNode, Deal, EvidenceItem, ProcessNode
from funnel.ui.crud import CrudPanel
from funnel.ui.evidence_meta import SOURCE_TYPES, STRENGTHS, tier_badge
from funnel.ui.fieldutils import build_select, clamp01, parse_float, truncate
from funnel.ui.modals import FormScreen


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------

class EvidenceForm(FormScreen):
    title_text = "Evidence item"

    def build_fields(self) -> ComposeResult:
        e = self.existing
        companies = repo.list_for_request(CompanyNode, self.request_id)
        nodes = repo.list_for_request(ProcessNode, self.request_id)
        sel_companies = set(e.related_companies) if e else set()
        sel_nodes = set(e.related_process_nodes) if e else set()
        yield Label("Source type", classes="field-label")
        yield build_select(SOURCE_TYPES, e.source_type if e else None, id="f-source",
                           allow_blank=False, default="news")
        yield Label("Source URL", classes="field-label")
        yield Input(value=(e.source_url if e else ""), id="f-url")
        yield Label("Publisher", classes="field-label")
        yield Input(value=(e.publisher if e else ""), id="f-publisher")
        yield Label("Date (e.g. 2026-05-01)", classes="field-label")
        yield Input(value=(e.date if e else ""), id="f-date")
        yield Label("Claim *", classes="field-label")
        yield Input(value=(e.claim if e else ""), id="f-claim")
        yield Label("Extracted quote", classes="field-label")
        yield Input(value=(e.extracted_quote if e else ""), id="f-quote")
        yield Label("Related companies", classes="field-label")
        yield SelectionList(
            *[Selection(c.name or c.id, c.id, c.id in sel_companies) for c in companies],
            id="f-companies", classes="multi-select",
        )
        yield Label("Related process nodes", classes="field-label")
        yield SelectionList(
            *[Selection(n.name or n.id, n.id, n.id in sel_nodes) for n in nodes],
            id="f-nodes", classes="multi-select",
        )
        yield Label("Evidence strength", classes="field-label")
        yield build_select(STRENGTHS, e.evidence_strength if e else None, id="f-strength",
                           allow_blank=False, default="moderate")
        yield Label("Confidence (0–1)", classes="field-label")
        yield Input(value=(str(e.confidence_score) if e else ""), id="f-conf")

    def collect(self) -> Optional[dict]:
        claim = self._input_value("#f-claim")
        if not claim:
            self.app.notify("Claim is required.", severity="warning")
            return None
        return {
            "source_type": self._select_value("#f-source") or "news",
            "source_url": self._input_value("#f-url"),
            "publisher": self._input_value("#f-publisher"),
            "date": self._input_value("#f-date"),
            "claim": claim,
            "extracted_quote": self._input_value("#f-quote"),
            "related_companies": self._multi_value("#f-companies"),
            "related_process_nodes": self._multi_value("#f-nodes"),
            "evidence_strength": self._select_value("#f-strength") or "moderate",
            "confidence_score": clamp01(parse_float(self._input_value("#f-conf"))),
        }


class DealForm(FormScreen):
    title_text = "Deal"
    DEAL_TYPES = [
        ("Purchase order", "purchase_order"), ("Supply agreement", "supply_agreement"),
        ("Capacity reservation", "capacity_reservation"), ("Offtake agreement", "offtake"),
        ("Joint development", "jda"), ("Customer win", "customer_win"),
        ("Partnership", "partnership"), ("Acquisition", "acquisition"),
        ("Fab / capacity expansion", "fab_expansion"), ("Capex order", "capex_order"),
        ("Government subsidy", "subsidy"), ("Export restriction impact", "export_restriction"),
    ]

    def build_fields(self) -> ComposeResult:
        e = self.existing
        companies = repo.list_for_request(CompanyNode, self.request_id)
        nodes = repo.list_for_request(ProcessNode, self.request_id)
        evidence = repo.list_for_request(EvidenceItem, self.request_id)
        self._has_companies = bool(companies)
        if not companies:
            yield Label("Add at least one company before recording a deal.", classes="field-label")
            return
        comp_opts = [(c.name or c.id, c.id) for c in companies]
        node_opts = [(n.name or n.id, n.id) for n in nodes]
        sel_sources = set(e.source_ids) if e else set()
        yield Label("Deal type", classes="field-label")
        yield build_select(self.DEAL_TYPES, e.deal_type if e else None, id="f-type",
                           allow_blank=False, default="partnership")
        yield Label("Buyer", classes="field-label")
        yield build_select(comp_opts, e.buyer if e else None, id="f-buyer")
        yield Label("Supplier", classes="field-label")
        yield build_select(comp_opts, e.supplier if e else None, id="f-supplier")
        yield Label("Process node (optional)", classes="field-label")
        yield build_select(node_opts, e.process_node if e else None, id="f-node")
        yield Label("Product or service", classes="field-label")
        yield Input(value=(e.product_or_service if e else ""), id="f-product")
        yield Label("Announced date (e.g. 2026-05-01)", classes="field-label")
        yield Input(value=(e.announced_date if e else ""), id="f-date")
        yield Label("Deal value (optional, e.g. $2.4B)", classes="field-label")
        yield Input(value=(e.deal_value or "" if e else ""), id="f-value")
        yield Label("Supporting evidence", classes="field-label")
        yield SelectionList(
            *[Selection(truncate(ev.claim or ev.publisher or ev.id, 50), ev.id, ev.id in sel_sources)
              for ev in evidence],
            id="f-sources", classes="multi-select",
        )
        yield Label("Confidence (0–1)", classes="field-label")
        yield Input(value=(str(e.confidence_score) if e else ""), id="f-conf")

    def collect(self) -> Optional[dict]:
        if not getattr(self, "_has_companies", False):
            self.app.notify("Add at least one company first.", severity="warning")
            return None
        return {
            "deal_type": self._select_value("#f-type") or "partnership",
            "buyer": self._select_value("#f-buyer"),
            "supplier": self._select_value("#f-supplier"),
            "process_node": self._select_value("#f-node") or None,
            "product_or_service": self._input_value("#f-product"),
            "announced_date": self._input_value("#f-date"),
            "deal_value": self._input_value("#f-value") or None,
            "source_ids": self._multi_value("#f-sources"),
            "confidence_score": clamp01(parse_float(self._input_value("#f-conf"))),
        }


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

class EvidencePanel(CrudPanel):
    model = EvidenceItem
    form_cls = EvidenceForm
    columns = ("Source", "Publisher", "Claim", "Strength", "Cos")
    entity_name = "evidence"

    def row_cells(self, e) -> tuple:
        # "~" marks model-recalled evidence: no source was fetched, the URL
        # is unverified (see EvidenceItem.provenance).
        strength = e.evidence_strength
        if getattr(e, "provenance", "") == "model_knowledge":
            strength = f"~{strength}"
        return (
            tier_badge(e.source_type),
            e.publisher or "—",
            truncate(e.claim, 44),
            strength,
            str(len(e.related_companies)),
        )


class DealPanel(CrudPanel):
    model = Deal
    form_cls = DealForm
    columns = ("Type", "Buyer", "Supplier", "Product", "Value", "Conf")
    entity_name = "deal"

    def fetch(self) -> list:
        self._names = {
            c.id: (c.name or c.id)
            for c in repo.list_for_request(CompanyNode, self.request_id)
        }
        return repo.list_for_request(Deal, self.request_id)

    def row_cells(self, e) -> tuple:
        names = getattr(self, "_names", {})
        return (
            e.deal_type,
            names.get(e.buyer, e.buyer or "—"),
            names.get(e.supplier, e.supplier or "—"),
            truncate(e.product_or_service, 28),
            e.deal_value or "—",
            f"{e.confidence_score:.2f}",
        )
