"""In-dashboard value-chain tree — the funnel map rendered as an interactive,
terminal-native Textual Tree (target output → layers → process nodes →
companies), instead of only a matplotlib PNG in the exports folder. Crisp at any
size, scrollable, and selectable: pick a company to open its ISR dossier. The
PNG/PDF/Markdown exports still happen (press `e` here, or the Export panel) — a
high-res raster is for sharing, this view is for reading in the app.
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static, Tree

from funnel import config
from funnel.export.chart import LAYER_LABEL, short_label
from funnel.export.chart_tree import companies_for_node, layers_present
from funnel.export.openers import open_chart
from funnel.graph.assembly import build_graph
from funnel.ui.panels.dossier import DossierScreen, resolve_dossier
from funnel.ui.workers import JobRunnerMixin


def _export_job(request_id: str):
    def job(progress):
        from funnel.services.build import export_map
        return export_map(request_id, progress=progress)
    return job


class TreeViewPanel(JobRunnerMixin, Vertical):
    """Request-aware: rebuilds the tree whenever the selected request or its
    graph changes."""

    BINDINGS = [Binding("e", "export", "Export charts")]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button("Export charts (PNG/PDF/report)", id="tree-export")
            yield Static("", id="tree-hint")
        tree: Tree = Tree("Value chain", id="map-tree")
        tree.show_root = True
        tree.guide_depth = 3
        yield tree

    # --- data ----------------------------------------------------------------
    def load_request(self, request_id: Optional[str]) -> None:
        self.request_id = request_id
        self._rebuild()

    def _company_label(self, model, c) -> str:
        rel = model.relevance.get(c.id, 0.0)
        tick = f" ({c.ticker})" if c.ticker else ""
        grounded = " ★" if (c.financials or {}).get("isr") else ""
        return f"{short_label(c.name, 30)}{tick}  rel {rel:.2f}{grounded}"

    def _node_label(self, model, n) -> str:
        count = model.company_count_by_node.get(n.id, 0)
        bn = model.bottleneck.get(n.id, 0.0)
        bar = "●" * max(0, min(3, round(bn * 3)))
        tail = f"  bottleneck {bar}" if bar else ""
        return f"{short_label(n.name, 32)}  [{count}]{tail}"

    def _rebuild(self) -> None:
        tree = self.query_one("#map-tree", Tree)
        hint = self.query_one("#tree-hint", Static)
        tree.clear()
        if not self.request_id:
            tree.root.label = "No request selected — start on the Map tab."
            hint.update("")
            return
        model = build_graph(self.request_id)
        if not model.nodes and not model.companies:
            tree.root.label = ("No map yet — type a topic on the Map tab and "
                               "Build Map, or Seed from ISR.")
            hint.update("")
            return

        title = (model.request.target_output
                 or model.request.industry_of_interest
                 or model.request.title or "Value chain")
        tree.root.label = short_label(title, 44)
        node_ids = {n.id for n in model.nodes}

        for layer in layers_present(model):
            layer_nodes = [n for n in model.nodes if n.layer == layer]
            branch = tree.root.add(
                f"{LAYER_LABEL.get(layer, layer)}  ({len(layer_nodes)})",
                expand=True)
            for n in layer_nodes:
                node_branch = branch.add(self._node_label(model, n), expand=True)
                for c in companies_for_node(model, n.id):
                    node_branch.add_leaf(self._company_label(model, c),
                                         data=("company", c.id))

        unassigned = [c for c in model.companies
                      if not any(nid in node_ids for nid in c.process_node_ids)]
        if unassigned:
            branch = tree.root.add(f"Unassigned  ({len(unassigned)})", expand=True)
            for c in unassigned:
                branch.add_leaf(self._company_label(model, c),
                                data=("company", c.id))

        tree.root.expand()
        star = " · ★ = ISR-grounded" if any(
            (c.financials or {}).get("isr") for c in model.companies) else ""
        hint.update(f"{len(model.nodes)} nodes · {len(model.companies)} companies"
                    f"  ·  enter a company for its dossier{star}")

    # --- interaction ---------------------------------------------------------
    def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
        data = event.node.data
        if not data or data[0] != "company":
            return
        from funnel.db.models import CompanyNode
        from funnel.db import repo
        company = repo.get(CompanyNode, data[1])
        if company is None:
            return
        bundle = resolve_dossier(company)
        if bundle is not None:
            self.app.push_screen(DossierScreen(company, bundle))
        else:
            tick = f" ({company.ticker})" if company.ticker else ""
            self.app.notify(
                f"{company.name}{tick} — {company.role}"
                + ("  · no ISR dossier" if config.isr_db_path() else ""))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "tree-export":
            event.stop()
            self.action_export()

    def action_export(self) -> None:
        if not self.request_id:
            self.app.notify("Select or build a request first.", severity="warning")
            return
        rid = self.request_id
        self._run_job(_export_job(rid), self._export_done,
                      on_error=lambda msg: self.app.notify(
                          f"Export failed: {msg}", severity="error"))

    def _export_done(self, result: dict) -> None:
        charts = result.get("charts", {})
        self.app.notify(f"Exported {len(charts)} tree charts + report → "
                        f"{config.EXPORT_DIR}")
        open_chart(charts, headless=getattr(self.app, "is_headless", False))
