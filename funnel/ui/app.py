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
from funnel.ui.panels.evidence import DealPanel, EvidencePanel
from funnel.ui.panels.export import ExportPanel
from funnel.ui.panels.flow import FlowPanel
from funnel.ui.panels.graph import CompanyPanel, EdgePanel, ProcessNodePanel
from funnel.ui.panels.intake import IntakePanel
from funnel.ui.panels.treeview import TreeViewPanel
from funnel.ui.manual import ManualScreen
from funnel.ui.sidebar import Sidebar


class FunnelApp(App):
    CSS_PATH = "styles.tcss"
    TITLE = "Funnel Network Engine"
    BINDINGS = [
        ("ctrl+n", "new_request", "New request"),
        ("ctrl+d", "delete_request", "Delete request"),
        ("f2", "goto_advanced", "Advanced"),
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
                # Flattened dashboard: Map is the app; everything else lives
                # in ONE flat Advanced row — never more than two tab levels.
                with TabbedContent(id="tabs"):
                    with TabPane("Map", id="tab-flow"):
                        yield FlowPanel(id="flow")
                    with TabPane("Tree", id="tab-tree"):
                        yield TreeViewPanel(id="treeview")
                    with TabPane("Advanced", id="tab-advanced"):
                        with TabbedContent(id="advanced-tabs"):
                            with TabPane("Refine", id="adv-intake"):
                                yield IntakePanel(id="intake")
                            with TabPane("Nodes", id="adv-nodes"):
                                yield ProcessNodePanel()
                            with TabPane("Companies", id="adv-companies"):
                                yield CompanyPanel()
                            with TabPane("Relations", id="adv-edges"):
                                yield EdgePanel()
                            with TabPane("Evidence", id="adv-evidence"):
                                yield EvidencePanel()
                            with TabPane("Deals", id="adv-deals"):
                                yield DealPanel()
                            with TabPane("Analysis", id="adv-analysis"):
                                yield AnalysisPanel(id="analysis")
                            with TabPane("Export", id="adv-export"):
                                yield ExportPanel(id="export")
        yield Footer()

    def on_mount(self) -> None:
        init_db()
        reaped = repo.reap_running_runs()
        if reaped:
            self.notify(f"{reaped} interrupted run(s) from a previous session "
                        "were marked failed; staged items remain reviewable.",
                        severity="warning")
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
                "No request yet — type a topic in the Map tab and choose Build Map.")
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

    def action_goto_advanced(self) -> None:
        """F2 toggles between the Map and the Advanced tab."""
        tabs = self.query_one("#tabs", TabbedContent)
        tabs.active = ("tab-advanced" if tabs.active == "tab-flow"
                       else "tab-flow")

    def action_show_manual(self) -> None:
        self.push_screen(ManualScreen())
