"""Export tab (Phase 5): render the funnel chart and export structured data.

Files land in config.EXPORT_DIR (~/.funnel_network_engine/exports). Chart
exports also record a VisualizationExport row.
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Select, Static

from funnel.db import models as m
from funnel.db import repo
from funnel.export import chart, chart_tree, data_export
from funnel.graph.assembly import build_graph

_CHART_FORMATS = {"chart-png": "png", "chart-svg": "svg", "chart-pdf": "pdf"}
_TREE_FORMATS = {"tree-png": "png", "tree-svg": "svg", "tree-pdf": "pdf"}
_DATA_IDS = ("exp-json", "exp-csv", "exp-md")


class ExportPanel(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None

    def compose(self) -> ComposeResult:
        yield Static("Export", classes="panel-title")
        yield Static("", id="export-summary")
        yield Static("Funnel chart", classes="field-label")
        with Horizontal(classes="toolbar"):
            yield Button("Funnel PNG", id="chart-png", variant="primary")
            yield Button("Funnel SVG", id="chart-svg")
            yield Button("Funnel PDF", id="chart-pdf")
        yield Static("Tree chart", classes="field-label")
        with Horizontal(classes="toolbar"):
            yield Select([("High-level", "high"), ("Professional", "professional"), ("Deep", "deep")],
                        value="professional", allow_blank=False, id="tree-resolution")
            yield Button("Tree PNG", id="tree-png", variant="primary")
            yield Button("Tree SVG", id="tree-svg")
            yield Button("Tree PDF", id="tree-pdf")
        yield Static("Data", classes="field-label")
        with Horizontal(classes="toolbar"):
            yield Button("JSON", id="exp-json", variant="primary")
            yield Button("CSV", id="exp-csv")
            yield Button("Markdown", id="exp-md")
        yield Static("", id="export-status")

    def on_mount(self) -> None:
        self._refresh_summary()

    def load_request(self, request_id: Optional[str]) -> None:
        self.request_id = request_id
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        summary = self.query_one("#export-summary", Static)
        if not self.request_id:
            summary.update("Select a request to export.")
            return
        counts = {
            "Process nodes": repo.count_for_request(m.ProcessNode, self.request_id),
            "Companies": repo.count_for_request(m.CompanyNode, self.request_id),
            "Relationships": repo.count_for_request(m.RelationshipEdge, self.request_id),
            "Evidence items": repo.count_for_request(m.EvidenceItem, self.request_id),
            "Deals": repo.count_for_request(m.Deal, self.request_id),
        }
        summary.update("\n".join(f"  {label}: {value}" for label, value in counts.items()))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid not in _CHART_FORMATS and bid not in _TREE_FORMATS and bid not in _DATA_IDS:
            return
        event.stop()
        if not self.request_id:
            self.app.notify("Select a request first.", severity="warning")
            return
        try:
            path = self._export(bid)
        except Exception as exc:  # surface, don't crash the app
            self.app.notify(f"Export failed: {exc}", severity="error")
            self.query_one("#export-status", Static).update(f"[b]Export failed:[/b] {exc}")
            return
        self.app.notify(f"Exported to {path}")
        self.query_one("#export-status", Static).update(f"[b]Last export:[/b] {path}")

    def _export(self, bid: str):
        model = build_graph(self.request_id)
        if bid in _CHART_FORMATS:
            fmt = _CHART_FORMATS[bid]
            path = data_export.export_path(model.request, fmt)
            chart.render_funnel(model, path, fmt)
            repo.add(m.VisualizationExport(
                request_id=self.request_id, chart_type="funnel_network",
                file_format=fmt, file_path=str(path),
                layout_options={"group_by": "process_layer"},
            ))
            return path
        if bid in _TREE_FORMATS:
            fmt = _TREE_FORMATS[bid]
            resolution = self.query_one("#tree-resolution", Select).value
            path = data_export.export_path(model.request, f"tree.{fmt}")
            chart_tree.render_tree(model, path, fmt, resolution)
            repo.add(m.VisualizationExport(
                request_id=self.request_id, chart_type="tree",
                file_format=fmt, file_path=str(path),
                layout_options={"resolution": resolution},
            ))
            return path
        if bid == "exp-json":
            return data_export.export_json(model, data_export.export_path(model.request, "json"))
        if bid == "exp-csv":
            return data_export.export_csv(model)
        return data_export.export_markdown(model, data_export.export_path(model.request, "md"))
