"""Funnel Network Engine — Textual application shell."""

from __future__ import annotations

from typing import Optional

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static, TabbedContent, TabPane

from funnel.db import repo
from funnel.db.engine import init_db
from funnel.db.models import ResearchRequest
from funnel.ui.panels.analysis import AnalysisPanel
from funnel.ui.panels.evidence import EvidenceDealsPanel
from funnel.ui.panels.export import ExportPanel
from funnel.ui.panels.flow import FlowPanel
from funnel.ui.panels.graph import GraphPanel
from funnel.ui.panels.intake import IntakePanel
from funnel.ui.manual import ManualScreen
from funnel.ui.sidebar import Sidebar


class FunnelApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "Funnel Network Engine"
    BINDINGS = [
        ("ctrl+n", "new_request", "New request"),
        ("ctrl+d", "delete_request", "Delete request"),
        ("f1", "show_manual", "Manual"),
        ("ctrl+q", "quit", "Quit"),
    ]

    current_request_id: reactive[Optional[str]] = reactive(None)

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="body"):
            yield Sidebar(id="sidebar")
            with Vertical(id="main"):
                yield Static("No request selected.", id="breadcrumb")
                with TabbedContent(id="tabs"):
                    with TabPane("Map", id="tab-flow"):
                        yield FlowPanel(id="flow")
                    with TabPane("Refine (intake)", id="tab-intake"):
                        yield IntakePanel(id="intake")
                    with TabPane("Graph & Evidence", id="tab-graph"):
                        with TabbedContent(id="graph-subtabs"):
                            with TabPane("Graph", id="subtab-graph"):
                                yield GraphPanel(id="graph")
                            with TabPane("Evidence & Deals", id="subtab-evidence"):
                                yield EvidenceDealsPanel(id="evidence")
                    with TabPane("Analysis & Export", id="tab-analysis"):
                        with TabbedContent(id="analysis-subtabs"):
                            with TabPane("Analysis", id="subtab-analysis"):
                                yield AnalysisPanel(id="analysis")
                            with TabPane("Export", id="subtab-export"):
                                yield ExportPanel(id="export")
        yield Footer()

    def on_mount(self) -> None:
        init_db()
        self._initialized = True
        self.query_one(Sidebar).reload()

    # --- request selection ----------------------------------------------
    def on_sidebar_request_selected(self, message: Sidebar.RequestSelected) -> None:
        self.current_request_id = message.request_id

    def watch_current_request_id(self, request_id: Optional[str]) -> None:
        if not getattr(self, "_initialized", False):
            return
        self._update_breadcrumb(request_id)
        for panel in self.query(".request-aware"):
            panel.load_request(request_id)

    def refresh_dependent_panels(self) -> None:
        """Called after data changes so summary-style panels re-read counts."""
        for panel in self.query(".request-aware"):
            loader = getattr(panel, "load_request", None)
            if loader:
                loader(self.current_request_id)

    def request_updated(self) -> None:
        """Called after a request's own fields change (e.g. intake saved)."""
        self._update_breadcrumb(self.current_request_id)
        self.refresh_dependent_panels()

    def _update_breadcrumb(self, request_id: Optional[str]) -> None:
        breadcrumb = self.query_one("#breadcrumb", Static)
        if not request_id:
            breadcrumb.update(
                "No request yet — type a topic in the Map tab and press Generate.")
            return
        req = repo.get(ResearchRequest, request_id)
        if req is None:
            breadcrumb.update("No request selected.")
            return
        label = req.title or req.industry_of_interest or req.id
        bits = [f"[b]{label}[/b]"]
        if req.industry_of_interest:
            bits.append(req.industry_of_interest)
        if req.target_output:
            bits.append(f"→ {req.target_output}")
        breadcrumb.update("    ".join(bits))

    # --- actions ---------------------------------------------------------
    def action_new_request(self) -> None:
        self.query_one(Sidebar).new_request()

    def action_delete_request(self) -> None:
        self.query_one(Sidebar).delete_current()

    def action_show_manual(self) -> None:
        self.push_screen(ManualScreen())
