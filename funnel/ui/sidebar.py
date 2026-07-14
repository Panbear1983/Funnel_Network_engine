"""Left-hand list of research requests, with New / Delete."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Label, ListItem, ListView

from funnel.db import repo
from funnel.ui.manual import ManualScreen
from funnel.ui.modals import ConfirmModal, TextPromptModal


class Sidebar(Vertical):
    class RequestSelected(Message):
        def __init__(self, request_id: Optional[str]) -> None:
            self.request_id = request_id
            super().__init__()

    def compose(self) -> ComposeResult:
        yield Label("Research Requests", classes="sidebar-title")
        yield ListView(id="req-list")
        with Horizontal(classes="sidebar-actions"):
            yield Button("New", id="new-req", variant="primary")
            yield Button("Delete", id="del-req", variant="error")
        yield Button("Manual (F1)", id="open-manual", classes="sidebar-manual")

    def on_mount(self) -> None:
        self._ids: list[str] = []

    # --- data ------------------------------------------------------------
    def reload(self, select_id: Optional[str] = None) -> None:
        listview = self.query_one("#req-list", ListView)
        listview.clear()
        self._ids = []
        for req in repo.list_requests():
            label = req.title or req.industry_of_interest or req.id
            listview.append(ListItem(Label(label)))
            self._ids.append(req.id)
        if select_id and select_id in self._ids:
            listview.index = self._ids.index(select_id)
        elif self._ids:
            listview.index = 0

    def current_id(self) -> Optional[str]:
        listview = self.query_one("#req-list", ListView)
        idx = listview.index
        if idx is not None and 0 <= idx < len(self._ids):
            return self._ids[idx]
        return None

    # --- events ----------------------------------------------------------
    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        idx = event.list_view.index
        if idx is not None and 0 <= idx < len(self._ids):
            self.post_message(self.RequestSelected(self._ids[idx]))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "new-req":
            event.stop()
            self.new_request()
        elif event.button.id == "del-req":
            event.stop()
            self.delete_current()
        elif event.button.id == "open-manual":
            event.stop()
            self.app.push_screen(ManualScreen())

    # --- operations ------------------------------------------------------
    def new_request(self) -> None:
        def done(title: Optional[str]) -> None:
            if not title:
                return
            req = repo.create_request(title)
            self.reload(select_id=req.id)
            self.post_message(self.RequestSelected(req.id))

        self.app.push_screen(
            TextPromptModal("New research request", placeholder="e.g. Silicon photonics"),
            done,
        )

    def delete_current(self) -> None:
        rid = self.current_id()
        if not rid:
            self.app.notify("No request selected.", severity="warning")
            return

        def done(confirmed: bool) -> None:
            if not confirmed:
                return
            repo.delete_request(rid)
            self.reload()
            self.post_message(self.RequestSelected(self.current_id()))

        self.app.push_screen(
            ConfirmModal("Delete this request and all of its data?"), done
        )
