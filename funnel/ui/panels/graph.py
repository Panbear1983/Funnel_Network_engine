"""Phase 2 — manual graph builder: process nodes, companies, and the
relationship edges between companies."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.widgets import (
    Button, Input, Label, Select, SelectionList, Static,
)
from textual.widgets.selection_list import Selection

from funnel.db import repo
from funnel.db.models import CompanyNode, EvidenceItem, ProcessNode, RelationshipEdge
from funnel.knowledge.isr import exchange_label, get_isr
from funnel.ui.panels.dossier import DossierScreen, resolve_dossier
from funnel.ui.crud import CrudPanel
from funnel.ui.evidence_meta import strongest_source
from funnel.ui.fieldutils import (
    build_select,
    clamp01,
    join_list,
    parse_float,
    parse_optional_float,
    split_list,
)
from funnel.ui.modals import FormScreen


# --------------------------------------------------------------------------
# Forms
# --------------------------------------------------------------------------

class ProcessNodeForm(FormScreen):
    title_text = "Process node"
    LAYERS = [
        ("Raw materials", "raw_materials"), ("Upstream", "upstream"),
        ("Midstream", "midstream"), ("Downstream", "downstream"),
        ("Equipment", "equipment"), ("Distribution", "distribution"), ("Custom", "custom"),
    ]
    RISK = [("Unknown", "unknown"), ("Low", "low"), ("Medium", "medium"), ("High", "high")]

    def build_fields(self) -> ComposeResult:
        e = self.existing
        yield Label("Name *", classes="field-label")
        yield Input(value=(e.name if e else ""), id="f-name")
        yield Label("Layer", classes="field-label")
        yield build_select(self.LAYERS, e.layer if e else None, id="f-layer",
                           allow_blank=False, default="midstream")
        yield Label("Description", classes="field-label")
        yield Input(value=(e.description if e else ""), id="f-desc")
        yield Label("Inputs (comma-separated)", classes="field-label")
        yield Input(value=(join_list(e.inputs) if e else ""), id="f-inputs")
        yield Label("Outputs (comma-separated)", classes="field-label")
        yield Input(value=(join_list(e.outputs) if e else ""), id="f-outputs")
        yield Label("Keywords (comma-separated)", classes="field-label")
        yield Input(value=(join_list(e.keywords) if e else ""), id="f-keywords")
        yield Label("Required capabilities (comma-separated)", classes="field-label")
        yield Input(value=(join_list(e.required_capabilities) if e else ""), id="f-caps")
        yield Label("Bottleneck risk", classes="field-label")
        yield build_select(self.RISK, e.bottleneck_risk if e else None, id="f-risk",
                           allow_blank=False, default="unknown")
        yield Label("Confidence (0–1)", classes="field-label")
        yield Input(value=(str(e.confidence_score) if e else ""), id="f-conf")

    def collect(self) -> Optional[dict]:
        name = self._input_value("#f-name")
        if not name:
            self.app.notify("Name is required.", severity="warning")
            return None
        return {
            "name": name,
            "layer": self._select_value("#f-layer") or "midstream",
            "description": self._input_value("#f-desc"),
            "inputs": split_list(self._input_value("#f-inputs")),
            "outputs": split_list(self._input_value("#f-outputs")),
            "keywords": split_list(self._input_value("#f-keywords")),
            "required_capabilities": split_list(self._input_value("#f-caps")),
            "bottleneck_risk": self._select_value("#f-risk") or "unknown",
            "confidence_score": clamp01(parse_float(self._input_value("#f-conf"))),
        }


class CompanyForm(FormScreen):
    title_text = "Company"
    BINDINGS = [Binding("ctrl+f", "isr_fill", "Autofill from ISR")]
    ROLES = [
        ("Operator / producer", "operator"), ("Supplier", "supplier"),
        ("Materials supplier", "materials_supplier"), ("Equipment supplier", "equipment_supplier"),
        ("Customer", "customer"), ("Distributor", "distributor"),
        ("Integrator", "integrator"), ("Partner", "partner"),
    ]
    CAPABILITY = [
        ("Unknown", "unknown"), ("Confirmed", "confirmed"),
        ("Claimed", "claimed"), ("Refuted", "refuted"),
    ]

    def build_fields(self) -> ComposeResult:
        e = self.existing
        nodes = repo.list_for_request(ProcessNode, self.request_id)
        selected = set(e.process_node_ids) if e else set()
        yield Label("Name *", classes="field-label")
        yield Input(value=(e.name if e else ""), id="f-name")
        yield Label("Ticker", classes="field-label")
        yield Input(value=(e.ticker or "" if e else ""), id="f-ticker")
        yield Label("Exchange", classes="field-label")
        yield Input(value=(e.exchange or "" if e else ""), id="f-exchange")
        yield Label("Country", classes="field-label")
        yield Input(value=(e.country or "" if e else ""), id="f-country")
        if get_isr() is not None:
            yield Static("Ctrl+F fills ticker/exchange/country from the ISR "
                         "knowledge base (type a name or ticker first).",
                         id="f-isr-status", classes="field-label")
        yield Label("Role", classes="field-label")
        yield build_select(self.ROLES, e.role if e else None, id="f-role",
                           allow_blank=False, default="operator")
        yield Label("Process nodes this company operates in", classes="field-label")
        yield SelectionList(
            *[Selection(n.name or n.id, n.id, n.id in selected) for n in nodes],
            id="f-nodes", classes="multi-select",
        )
        yield Label("Capability status", classes="field-label")
        yield build_select(self.CAPABILITY, e.capability_status if e else None, id="f-cap",
                           allow_blank=False, default="unknown")
        yield Label("Revenue purity estimate (0–1, optional)", classes="field-label")
        yield Input(value=(_opt_str(e.revenue_purity_estimate) if e else ""), id="f-revpurity")
        yield Label("Market share estimate (0–1, optional)", classes="field-label")
        yield Input(value=(_opt_str(e.market_share_estimate) if e else ""), id="f-mktshare")
        yield Label("Customer concentration (optional note)", classes="field-label")
        yield Input(value=(e.customer_concentration or "" if e else ""), id="f-concentration")
        yield Label("Confidence (0–1)", classes="field-label")
        yield Input(value=(str(e.confidence_score) if e else ""), id="f-conf")

    # NOTE: never override an inherited Textual message handler here (e.g.
    # on_button_pressed with a super() call) — doing so on these screens
    # leaves the button-press animation pending and any later
    # pilot.press(...) in the smoke tests deadlocks in
    # animator.wait_until_complete. Bindings / new handlers only.
    def action_isr_fill(self) -> None:
        self._isr_fill()

    def _isr_fill(self) -> None:
        """Look the typed name/ticker up in the ISR knowledge base and fill
        only the still-empty identity fields."""
        isr = get_isr()
        if isr is None:
            self.app.notify("ISR knowledge base unavailable.",
                            severity="warning")
            return
        query = self._input_value("#f-ticker") or self._input_value("#f-name")
        status = self.query_one("#f-isr-status", Static)
        if not query:
            status.update("Type a name or ticker first.")
            return
        hit = isr.lookup(query, country=self._input_value("#f-country") or None)
        if hit is None:
            status.update(f"No ISR match for '{query}'.")
            return
        for selector, value in (("#f-name", hit.name),
                                ("#f-ticker", hit.yahoo_ticker),
                                ("#f-exchange", exchange_label(hit.ticker)
                                 or exchange_label(hit.yahoo_ticker)),
                                ("#f-country", hit.country)):
            widget = self.query_one(selector, Input)
            if value and not widget.value.strip():
                widget.value = value
        status.update(f"ISR: {hit.label} · {hit.country} · {hit.industry} · "
                      f"dossier {hit.dossier_updated_at or '—'}")

    def collect(self) -> Optional[dict]:
        name = self._input_value("#f-name")
        if not name:
            self.app.notify("Name is required.", severity="warning")
            return None
        return {
            "name": name,
            "ticker": self._input_value("#f-ticker") or None,
            "exchange": self._input_value("#f-exchange") or None,
            "country": self._input_value("#f-country") or None,
            "role": self._select_value("#f-role") or "operator",
            "process_node_ids": self._multi_value("#f-nodes"),
            "capability_status": self._select_value("#f-cap") or "unknown",
            "revenue_purity_estimate": parse_optional_float(self._input_value("#f-revpurity")),
            "market_share_estimate": parse_optional_float(self._input_value("#f-mktshare")),
            "customer_concentration": self._input_value("#f-concentration") or None,
            "confidence_score": clamp01(parse_float(self._input_value("#f-conf"))),
        }


class EdgeForm(FormScreen):
    title_text = "Relationship"
    REL_TYPES = [
        ("Supplier", "supplier"), ("Customer", "customer"), ("Partner", "partner"),
        ("Competitor", "competitor"), ("Distributor", "distributor"), ("Contractor", "contractor"),
    ]
    EVIDENCE = [
        ("Unknown", "unknown"), ("Inferred", "inferred"),
        ("Weak", "weak"), ("Confirmed", "confirmed"),
    ]

    def build_fields(self) -> ComposeResult:
        e = self.existing
        companies = repo.list_for_request(CompanyNode, self.request_id)
        nodes = repo.list_for_request(ProcessNode, self.request_id)
        self._enough_companies = len(companies) >= 2
        if not self._enough_companies:
            yield Label("Add at least two companies before creating a relationship.",
                        classes="field-label")
            return
        comp_opts = [(c.name or c.id, c.id) for c in companies]
        node_opts = [(n.name or n.id, n.id) for n in nodes]
        yield Label("From company *", classes="field-label")
        yield build_select(comp_opts, e.from_company if e else None, id="f-from")
        yield Label("To company *", classes="field-label")
        yield build_select(comp_opts, e.to_company if e else None, id="f-to")
        yield Label("Relationship type", classes="field-label")
        yield build_select(self.REL_TYPES, e.relationship_type if e else None, id="f-type",
                           allow_blank=False, default="supplier")
        yield Label("From process node (optional)", classes="field-label")
        yield build_select(node_opts, e.from_process_node if e else None, id="f-fromnode")
        yield Label("To process node (optional)", classes="field-label")
        yield build_select(node_opts, e.to_process_node if e else None, id="f-tonode")
        yield Label("Evidence level", classes="field-label")
        yield build_select(self.EVIDENCE, e.evidence_level if e else None, id="f-evidence",
                           allow_blank=False, default="unknown")
        yield Label("Confidence (0–1)", classes="field-label")
        yield Input(value=(str(e.confidence_score) if e else ""), id="f-conf")

    def collect(self) -> Optional[dict]:
        if not getattr(self, "_enough_companies", False):
            self.app.notify("Add at least two companies first.", severity="warning")
            return None
        from_company = self._select_value("#f-from")
        to_company = self._select_value("#f-to")
        if not from_company or not to_company:
            self.app.notify("Both companies are required.", severity="warning")
            return None
        if from_company == to_company:
            self.app.notify("From and To must be different companies.", severity="warning")
            return None
        return {
            "from_company": from_company,
            "to_company": to_company,
            "relationship_type": self._select_value("#f-type") or "supplier",
            "from_process_node": self._select_value("#f-fromnode") or None,
            "to_process_node": self._select_value("#f-tonode") or None,
            "evidence_level": self._select_value("#f-evidence") or "unknown",
            "confidence_score": clamp01(parse_float(self._input_value("#f-conf"))),
        }


def _opt_str(value) -> str:
    return "" if value is None else str(value)


# --------------------------------------------------------------------------
# Panels
# --------------------------------------------------------------------------

class ProcessNodePanel(CrudPanel):
    model = ProcessNode
    form_cls = ProcessNodeForm
    columns = ("Name", "Layer", "Bottleneck", "Conf", "Keywords")
    entity_name = "node"

    def row_cells(self, e) -> tuple:
        return (e.name, e.layer, e.bottleneck_risk, f"{e.confidence_score:.2f}", join_list(e.keywords))


class CompanyPanel(CrudPanel):
    model = CompanyNode
    form_cls = CompanyForm
    columns = ("Name", "Ticker", "Role", "Country", "Conf", "Nodes", "Evidence")
    entity_name = "company"
    BINDINGS = [Binding("i", "show_dossier", "Dossier")]

    def extra_buttons(self) -> ComposeResult:
        yield Button("Dossier", id="company-dossier")

    def handle_extra_button(self, event: Button.Pressed) -> None:
        if event.button.id == "company-dossier":
            event.stop()
            self.action_show_dossier()

    def action_show_dossier(self) -> None:
        eid = self.selected_id()
        if eid is None:
            self.app.notify("Select a company row first.", severity="warning")
            return
        company = repo.get(CompanyNode, eid)
        bundle = resolve_dossier(company) if company else None
        if bundle is None:
            self.app.notify("No ISR dossier for this company.",
                            severity="information")
            return
        self.app.push_screen(DossierScreen(company, bundle))

    def fetch(self) -> list:
        self._evidence_by_company: dict[str, list] = {}
        for ev in repo.list_for_request(EvidenceItem, self.request_id):
            for cid in (ev.related_companies or []):
                self._evidence_by_company.setdefault(cid, []).append(ev)
        return repo.list_for_request(CompanyNode, self.request_id)

    def row_cells(self, e) -> tuple:
        evs = getattr(self, "_evidence_by_company", {}).get(e.id, [])
        evidence_cell = f"{len(evs)} · {strongest_source(evs)}" if evs else "—"
        return (
            e.name, e.ticker or "—", e.role, e.country or "—",
            f"{e.confidence_score:.2f}", str(len(e.process_node_ids)), evidence_cell,
        )


class EdgePanel(CrudPanel):
    model = RelationshipEdge
    form_cls = EdgeForm
    columns = ("From", "Type", "To", "Evidence", "Conf")
    entity_name = "relationship"

    def fetch(self) -> list:
        self._names = {
            c.id: (c.name or c.id)
            for c in repo.list_for_request(CompanyNode, self.request_id)
        }
        return repo.list_for_request(RelationshipEdge, self.request_id)

    def row_cells(self, e) -> tuple:
        names = getattr(self, "_names", {})
        return (
            names.get(e.from_company, e.from_company or "?"),
            e.relationship_type,
            names.get(e.to_company, e.to_company or "?"),
            e.evidence_level,
            f"{e.confidence_score:.2f}",
        )
