"""Generic Add/Edit/Delete table panel.

Each entity manager subclasses CrudPanel and sets `model`, `form_cls`,
`columns`, `entity_name`, and implements `row_cells`. The base handles layout,
the toolbar, row selection, and persisting form results.
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static

from funnel.db import repo
from funnel.ui.modals import ConfirmModal


class CrudPanel(Vertical):
    model = None          # SQLModel class
    form_cls = None       # FormScreen subclass
    columns: tuple[str, ...] = ()
    entity_name = "item"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None
        self._row_order: list[str] = []

    def compose(self) -> ComposeResult:
        with Horizontal(classes="toolbar"):
            yield Button(f"Add {self.entity_name}", id="crud-add", variant="primary")
            yield Button("Edit", id="crud-edit")
            yield Button("Delete", id="crud-delete", variant="error")
        yield Static("", id="crud-empty", classes="empty-hint")
        yield DataTable(id="crud-table", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#crud-table", DataTable)
        table.cursor_type = "row"
        if self.columns:
            table.add_columns(*self.columns)
        self.refresh_table()

    # --- data ------------------------------------------------------------
    def load_request(self, request_id: Optional[str]) -> None:
        self.request_id = request_id
        self.refresh_table()

    def fetch(self) -> list:
        return repo.list_for_request(self.model, self.request_id)

    def refresh_table(self) -> None:
        table = self.query_one("#crud-table", DataTable)
        table.clear()
        self._row_order = []
        if self.request_id:
            for entity in self.fetch():
                table.add_row(*self.row_cells(entity))
                self._row_order.append(entity.id)
        self._update_empty_hint()

    def _update_empty_hint(self) -> None:
        hint = self.query_one("#crud-empty", Static)
        if not self.request_id:
            hint.update("No request selected — start in the Map tab.")
        elif not self._row_order:
            hint.update(f"No {self.entity_name}s yet — the Map tab can generate "
                        f"them, or press Add {self.entity_name}.")
        hint.display = not self._row_order

    def selected_id(self) -> Optional[str]:
        table = self.query_one("#crud-table", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._row_order):
            return None
        return self._row_order[row]

    # --- actions ---------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id not in ("crud-add", "crud-edit", "crud-delete"):
            return
        event.stop()
        if not self.request_id:
            self.app.notify("Select or create a request first.", severity="warning")
            return
        if event.button.id == "crud-add":
            self._open_form(None)
        elif event.button.id == "crud-edit":
            eid = self.selected_id()
            if eid is None:
                self.app.notify("Select a row to edit.", severity="warning")
            else:
                self._open_form(eid)
        elif event.button.id == "crud-delete":
            eid = self.selected_id()
            if eid is None:
                self.app.notify("Select a row to delete.", severity="warning")
            else:
                def confirmed(yes: bool) -> None:
                    if not yes:
                        return
                    repo.delete(self.model, eid)
                    self.refresh_table()
                    self.notify_changed()

                self.app.push_screen(
                    ConfirmModal(f"Delete this {self.entity_name}?"), confirmed)

    def _open_form(self, entity_id: Optional[str]) -> None:
        existing = repo.get(self.model, entity_id) if entity_id else None

        def done(result: Optional[dict]) -> None:
            if result is None:
                return
            if existing is not None:
                for key, value in result.items():
                    setattr(existing, key, value)
                repo.update(existing)
            else:
                repo.add(self.model(request_id=self.request_id, **result))
            self.refresh_table()
            self.notify_changed()

        self.app.push_screen(self.form_cls(self.request_id, existing), done)

    def notify_changed(self) -> None:
        """Hook so sibling panels (e.g. the export summary) can refresh."""
        refresher = getattr(self.app, "refresh_dependent_panels", None)
        if refresher:
            refresher()

    # --- subclasses override --------------------------------------------
    def row_cells(self, entity) -> tuple:
        raise NotImplementedError
