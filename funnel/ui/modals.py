"""Reusable modal dialogs: a text prompt, a yes/no confirm, and a base
FormScreen that entity editors subclass."""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, SelectionList


class TextPromptModal(ModalScreen[Optional[str]]):
    """Single-line text prompt. Dismisses with the entered string, or None."""

    def __init__(self, title: str, placeholder: str = "", initial: str = "") -> None:
        super().__init__()
        self._title = title
        self._placeholder = placeholder
        self._initial = initial

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._title, classes="dialog-title")
            yield Input(value=self._initial, placeholder=self._placeholder, id="prompt-input")
            with Horizontal(classes="form-actions"):
                yield Button("OK", id="ok", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_mount(self) -> None:
        self.query_one(Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ok":
            self.dismiss(self.query_one(Input).value.strip() or None)
        else:
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value.strip() or None)


class ConfirmModal(ModalScreen[bool]):
    """Yes/No confirmation. Dismisses with True/False."""

    def __init__(self, message: str) -> None:
        super().__init__()
        self._message = message

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog"):
            yield Label(self._message, classes="dialog-title")
            with Horizontal(classes="form-actions"):
                yield Button("Yes", id="yes", variant="error")
                yield Button("No", id="no", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")


class FormScreen(ModalScreen[Optional[dict]]):
    """Base modal form. Subclasses provide title, fields, and value collection.

    Dismisses with a dict of field values on save, or None on cancel.
    """

    title_text = "Form"

    def __init__(self, request_id: str, existing=None) -> None:
        super().__init__()
        self.request_id = request_id
        self.existing = existing

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog form-dialog"):
            yield Label(self.title_text, classes="dialog-title")
            with VerticalScroll(classes="form-body"):
                yield from self.build_fields()
            with Horizontal(classes="form-actions"):
                yield Button("Save", id="save", variant="primary")
                yield Button("Cancel", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            data = self.collect()
            if data is not None:
                self.dismiss(data)
        else:
            self.dismiss(None)

    # value helpers for subclasses ---------------------------------------
    def _input_value(self, selector: str) -> str:
        return self.query_one(selector, Input).value.strip()

    def _select_value(self, selector: str) -> str:
        widget = self.query_one(selector, Select)
        return "" if widget.value is Select.NULL else str(widget.value)

    def _multi_value(self, selector: str) -> list[str]:
        return [str(v) for v in self.query_one(selector, SelectionList).selected]

    # subclasses override -------------------------------------------------
    def build_fields(self) -> ComposeResult:  # type: ignore[override]
        return iter(())

    def collect(self) -> Optional[dict]:
        return {}
