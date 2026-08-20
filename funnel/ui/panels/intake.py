"""Advanced intake workspace: the guided questionnaire.

The form is built once; selecting a request repopulates the widgets from saved
answers. Saving writes IntakeAnswer rows and maps the relevant answers back
onto the ResearchRequest.
"""

from __future__ import annotations

from typing import Optional

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widget import Widget
from textual.widgets import (
    Button,
    Collapsible,
    Input,
    Label,
    Select,
    SelectionList,
    Static,
    Switch,
)

from funnel.db import repo
from funnel.db.models import ResearchRequest
from funnel.intake.questions import Question, SECTIONS


# Required by the automated Map flow; everything else refines it.
REQUIRED_QIDS = ("industry", "target_output")
# Stored but not consumed by any pipeline/chart yet (roadmap Phase 7+).
DORMANT_QIDS = {
    "metrics", "est_revenue_purity", "est_market_share", "flag_concentration",
    "flag_risk", "optimize_for", "show", "confidence_badges", "deal_badges",
    "formats",
}


class IntakePanel(Vertical):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None
        self._snapshot: Optional[dict[str, str]] = None

    # --- layout ----------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("Refine the research brief", classes="panel-title")
        yield Static("Select a request to begin.", id="intake-hint")
        with VerticalScroll(id="intake-form"):
            yield Static(
                "The Map tab fills this automatically from your topic — refine "
                "anything here and Save. Only fields marked * are required; "
                "sections 3–7 are optional.", classes="flow-hint")
            for index, section in enumerate(SECTIONS):
                with Collapsible(title=section.title, collapsed=index >= 2):
                    live = [q for q in section.questions
                            if q.id not in DORMANT_QIDS]
                    planned = [q for q in section.questions
                               if q.id in DORMANT_QIDS]
                    for q in live:
                        yield Label(self._label_for(q), classes="field-label")
                        yield self._widget_for(q)
                    if planned:
                        # Planned-but-unwired settings render as plain text so
                        # they never look like live controls that do nothing.
                        names = "; ".join(q.text.rstrip("?") for q in planned)
                        yield Static(f"[dim]Planned (not wired yet): {names}[/dim]",
                                     classes="field-label")
        # Keep the primary action outside the scrollable questionnaire so it
        # remains reachable even when most optional sections are expanded.
        with Horizontal(classes="toolbar intake-actions"):
            yield Button("Save intake", id="save-intake", variant="primary")

    @staticmethod
    def _label_for(q: Question) -> str:
        text = q.text
        if q.id in REQUIRED_QIDS:
            text += " *"
        return text

    def _widget_for(self, q: Question) -> Widget:
        wid = f"q-{q.id}"
        if q.kind in ("text", "longtext"):
            return Input(id=wid)
        if q.kind == "yesno":
            return Switch(id=wid)
        if q.kind == "choice":
            return Select(q.choices, prompt="Select…", id=wid)
        if q.kind == "multi":
            return SelectionList(*q.choices, id=wid, classes="multi-select")
        raise ValueError(f"unknown question kind: {q.kind}")

    def on_mount(self) -> None:
        self._set_visible(self.request_id is not None)

    def _set_visible(self, visible: bool) -> None:
        self.query_one("#intake-form").display = visible
        hint = self.query_one("#intake-hint", Static)
        hint.update("" if visible else "Select a request to begin.")

    # --- data ------------------------------------------------------------
    def _current_values(self) -> dict[str, str]:
        return {q.id: self._collect_value(q)
                for section in SECTIONS for q in section.questions
                if q.id not in DORMANT_QIDS}

    def _is_dirty(self) -> bool:
        return self._snapshot is not None and self._current_values() != self._snapshot

    def load_request(self, request_id: Optional[str]) -> None:
        # Guard unsaved edits: same request + edits in progress → keep them
        # (this is what used to wipe the form whenever another panel saved).
        if request_id == self.request_id and self._is_dirty():
            return
        if request_id != self.request_id and self._is_dirty():
            self.app.notify("Unsaved intake edits were discarded.", severity="warning")
        self.request_id = request_id
        self._set_visible(request_id is not None)
        if not request_id:
            self._snapshot = None
            return
        answers = repo.get_intake_answers(request_id)
        for section in SECTIONS:
            for q in section.questions:
                if q.id in DORMANT_QIDS:
                    continue
                answer = answers.get(q.id)
                self._set_value(q, answer.answer_value if answer else None)
        self._snapshot = self._current_values()

    def _set_value(self, q: Question, stored: Optional[str]) -> None:
        widget = self.query_one(f"#q-{q.id}")
        if q.kind in ("text", "longtext"):
            widget.value = stored or ""
        elif q.kind == "yesno":
            widget.value = stored == "yes"
        elif q.kind == "choice":
            valid = [value for _, value in q.choices]
            if stored in valid:
                widget.value = stored
            else:
                widget.clear()
        elif q.kind == "multi":
            widget.deselect_all()
            for value in self._split(stored):
                widget.select(value)

    def _collect_value(self, q: Question) -> str:
        widget = self.query_one(f"#q-{q.id}")
        if q.kind in ("text", "longtext"):
            return widget.value.strip()
        if q.kind == "yesno":
            return "yes" if widget.value else "no"
        if q.kind == "choice":
            return "" if widget.value is Select.NULL else str(widget.value)
        if q.kind == "multi":
            return ", ".join(str(v) for v in widget.selected)
        return ""

    @staticmethod
    def _split(value: Optional[str]) -> list[str]:
        if not value:
            return []
        return [part.strip() for part in value.split(",") if part.strip()]

    # --- save ------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save-intake":
            event.stop()
            self.save()

    def save(self) -> None:
        if not self.request_id:
            self.app.notify("Select a request first.", severity="warning")
            return
        req = repo.get(ResearchRequest, self.request_id)
        if req is None:
            return
        for section in SECTIONS:
            for q in section.questions:
                if q.id in DORMANT_QIDS:
                    continue
                value = self._collect_value(q)
                repo.save_intake_answer(self.request_id, section.title, q.id, q.text, value)
                self._apply_mapping(req, q, value)
        repo.update(req)
        self._snapshot = self._current_values()
        self.app.notify("Intake saved.")
        request_updated = getattr(self.app, "request_updated", None)
        if request_updated:
            request_updated()

    def _apply_mapping(self, req: ResearchRequest, q: Question, value: str) -> None:
        if not q.maps_to:
            return
        if q.kind == "yesno":
            setattr(req, q.maps_to, value == "yes")
        elif q.maps_list:
            setattr(req, q.maps_to, self._split(value))
        elif value:
            setattr(req, q.maps_to, value)
