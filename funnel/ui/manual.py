"""In-app user manual: a modal MarkdownViewer with a table-of-contents index."""

from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, MarkdownViewer

MANUAL_PATH = Path(__file__).resolve().parents[2] / "docs" / "USER_MANUAL.md"


class ManualScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close_manual", "Close")]

    def compose(self) -> ComposeResult:
        try:
            text = MANUAL_PATH.read_text(encoding="utf-8")
        except OSError:
            text = f"# User Manual\n\nManual file not found:\n\n`{MANUAL_PATH}`"
        with Vertical(classes="dialog manual-dialog"):
            yield Label("User Manual  (Esc to close)", classes="dialog-title")
            yield MarkdownViewer(text, show_table_of_contents=True, id="manual-viewer")
            with Horizontal(classes="form-actions"):
                yield Button("Close", id="close-manual", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-manual":
            self.dismiss(None)

    def action_close_manual(self) -> None:
        self.dismiss(None)
