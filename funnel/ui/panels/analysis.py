"""Analysis tab (Phase 4): scored summary + canned graph queries."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Select, Static

from funnel.graph.assembly import GraphModel, build_graph
from funnel.graph.queries import QUERIES, run_query


class AnalysisPanel(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None
        self._model: Optional[GraphModel] = None

    def compose(self) -> ComposeResult:
        yield Static("Analysis", classes="panel-title")
        yield Static("Select a request.", id="analysis-summary")
        with Horizontal(classes="toolbar"):
            yield Select(
                [(label, key) for label, key in QUERIES],
                value=QUERIES[0][1], allow_blank=False, id="query-select",
            )
            yield Button("Run query", id="run-query", variant="primary")
        yield DataTable(id="analysis-table", zebra_stripes=True)

    def on_mount(self) -> None:
        self.query_one("#analysis-table", DataTable).cursor_type = "row"

    def load_request(self, request_id: Optional[str]) -> None:
        self.request_id = request_id
        self._rebuild()

    def _rebuild(self) -> None:
        summary = self.query_one("#analysis-summary", Static)
        table = self.query_one("#analysis-table", DataTable)
        if not self.request_id:
            self._model = None
            summary.update("Select a request.")
            table.clear(columns=True)
            return
        self._model = build_graph(self.request_id)
        self._render_summary()
        self._run_current()

    def _render_summary(self) -> None:
        m = self._model
        summary = self.query_one("#analysis-summary", Static)
        counts = (f"Nodes {len(m.nodes)}  ·  Companies {len(m.companies)}  ·  "
                  f"Edges {len(m.edges)}  ·  Evidence {len(m.evidence)}  ·  Deals {len(m.deals)}")
        lines = [counts]
        if m.nodes:
            top = max(m.nodes, key=lambda n: m.bottleneck.get(n.id, 0.0))
            lines.append(f"Top bottleneck: [b]{top.name}[/b] ({m.bottleneck[top.id]:.2f})")
        if m.companies:
            top = max(m.companies, key=lambda c: m.relevance.get(c.id, 0.0))
            lines.append(f"Top investment relevance: [b]{top.name}[/b] ({m.relevance[top.id]:.2f})")
        weak = sum(1 for e in m.edges if e.evidence_level in ("unknown", "inferred", "weak"))
        lines.append(f"Weak / inferred relationships: {weak} / {len(m.edges)}")
        summary.update("\n".join(lines))

    def _run_current(self) -> None:
        if not self._model:
            return
        key = self.query_one("#query-select", Select).value
        cols, rows = run_query(key, self._model)
        table = self.query_one("#analysis-table", DataTable)
        table.clear(columns=True)
        table.add_columns(*cols)
        for row in rows:
            table.add_row(*[str(cell) for cell in row])

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-query":
            event.stop()
            self._run_current()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "query-select":
            self._run_current()
