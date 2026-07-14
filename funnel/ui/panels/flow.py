"""Map tab — the one-topic golden path.

Type a topic, press Generate: the request is created for you, an auto-brief
fills the intake, smart search + Claude stage the whole graph as proposals.
Click a row to reject/restore it, then press Build: non-rejected proposals are
committed and the tree chart is exported at all three detail levels.

Two buttons, one text field. The old per-entity editors remain available in
the other tabs as the manual path.
"""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Input, Label, Log, Select, Static

from funnel import config
from funnel.db import repo
from funnel.db.models import Proposal, ResearchRequest
from funnel.research import flow as flow_engine
from funnel.research.tiers import TIER_ORDER, TIERS, get_tier, resolve_model
from funnel.ui.fieldutils import truncate

_MARK = {"pending": "✓", "accepted": "✓", "rejected": "✗",
         "committed": "→", "skipped": "–"}


class ProposalDetailModal(ModalScreen[None]):
    """Read-only view of one proposal: payload, rationale, sources."""

    BINDINGS = [Binding("escape", "dismiss_modal", "Close")]

    def __init__(self, proposal) -> None:
        super().__init__()
        self._proposal = proposal

    def compose(self) -> ComposeResult:
        p = self._proposal
        with Vertical(classes="dialog form-dialog"):
            yield Label(f"Proposal — {p.entity_type} · {p.ref_key} · {p.status}",
                        classes="dialog-title")
            with VerticalScroll(classes="form-body"):
                yield Label("Payload", classes="field-label")
                yield Static(json.dumps(p.payload, indent=2, ensure_ascii=False),
                             classes="detail-block")
                if p.rationale:
                    yield Label("Rationale", classes="field-label")
                    yield Static(p.rationale, classes="detail-block")
                if p.sources:
                    yield Label("Sources", classes="field-label")
                    yield Static("\n".join(p.sources), classes="detail-block")
            with Horizontal(classes="form-actions"):
                yield Button("Close", id="detail-close", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)


class FlowPanel(Vertical):
    BINDINGS = [
        Binding("v", "view_details", "Details"),
        Binding("a", "restore_all", "Restore all"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self._rows: list[str] = []
        self._flow_busy = False

    # --- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("Map a value chain", classes="panel-title")
        yield Static(
            "Type a topic and press Generate — the brief, search, and graph are "
            "proposed for you. Click a row to reject it, then Build.",
            classes="flow-hint")
        with Horizontal(classes="toolbar"):
            yield Input(placeholder="Topic of interest — e.g. Chip manufacturing",
                        id="flow-topic")
            yield Select([(TIERS[k].label, k) for k in TIER_ORDER],
                         value="professional", allow_blank=False, id="flow-depth")
            yield Button("Generate", id="flow-generate", variant="primary")
        yield Static("", id="flow-keys")
        yield Log(id="flow-log", classes="research-log")
        yield Static("Review: click/Enter toggles reject · v details · a restore all",
                     id="flow-review-hint", classes="flow-hint")
        yield DataTable(id="flow-table", zebra_stripes=True)
        with Horizontal(classes="toolbar"):
            yield Button("Build map — commit + 3 tree charts",
                         id="flow-build", variant="success")
            yield Static("", id="flow-result")

    def on_mount(self) -> None:
        table = self.query_one("#flow-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(" ", "Type", "Name", "Detail", "Conf", "Status")
        self._refresh_key_status()

    def _refresh_key_status(self) -> None:
        provider = config.llm_provider()
        key_name = "OPENROUTER_API_KEY" if provider == "openrouter" else "ANTHROPIC_API_KEY"
        have_llm = bool(config.llm_api_key())
        have_tavily = bool(config.tavily_api_key())
        tier = get_tier(self._depth())
        fallback = ("the model's :online web plugin" if provider == "openrouter"
                    else "Claude's own web search")
        bits = [
            f"[{'green' if have_llm else 'red'}]●[/] {key_name}",
            f"[{'green' if have_tavily else 'yellow'}]●[/] TAVILY_API_KEY",
            f"{provider} · model [b]{resolve_model(tier)}[/b]",
        ]
        if not have_llm:
            bits.append(f"[red]add {key_name} to .env to enable Generate[/red]")
        elif not have_tavily:
            bits.append(f"[yellow]no Tavily — {fallback} will be used[/yellow]")
        self.query_one("#flow-keys", Static).update("  ".join(bits))
        self.query_one("#flow-generate", Button).disabled = not have_llm or self._flow_busy

    def _depth(self) -> str:
        try:
            return self.query_one("#flow-depth", Select).value
        except Exception:
            return "professional"

    # --- data ----------------------------------------------------------------
    def load_request(self, request_id: Optional[str]) -> None:
        if self._flow_busy:
            self.request_id = request_id
            return
        self.request_id = request_id
        req = repo.get(ResearchRequest, request_id) if request_id else None
        self.query_one("#flow-topic", Input).value = (req.title or "") if req else ""
        latest = repo.latest_run(request_id) if request_id else None
        self.run_id = latest.id if latest else None
        self.query_one("#flow-result", Static).update("")
        self._render_proposals()

    def _detail(self, p) -> str:
        d = p.payload
        if p.entity_type == "process_node":
            return f"layer={d.get('layer', '')}"
        if p.entity_type == "company":
            return f"{d.get('role', '')} {d.get('ticker') or ''}".strip()
        if p.entity_type == "edge":
            return f"{d.get('from_company')} →{d.get('relationship_type')}→ {d.get('to_company')}"
        if p.entity_type == "evidence":
            return truncate(d.get("claim", ""), 40)
        if p.entity_type == "deal":
            return truncate(d.get("product_or_service", ""), 40)
        return ""

    def _proposal_label(self, p) -> str:
        return p.payload.get("name") or p.ref_key

    def _render_proposals(self, keep_cursor: bool = False) -> None:
        table = self.query_one("#flow-table", DataTable)
        cursor = table.cursor_row if keep_cursor else 0
        table.clear()
        self._rows = []
        if not self.request_id or not self.run_id:
            return
        for p in repo.list_proposals(self.request_id, run_id=self.run_id):
            table.add_row(_MARK.get(p.status, "?"), p.entity_type,
                          truncate(self._proposal_label(p), 24), self._detail(p),
                          f"{p.confidence:.2f}", p.status)
            self._rows.append(p.id)
        if keep_cursor and self._rows:
            table.move_cursor(row=min(cursor, len(self._rows) - 1))

    def _selected_proposal(self):
        table = self.query_one("#flow-table", DataTable)
        row = table.cursor_row
        if row is None or row < 0 or row >= len(self._rows):
            return None
        return repo.get(Proposal, self._rows[row])

    # --- review gate -----------------------------------------------------------
    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "flow-table":
            return
        event.stop()
        p = self._selected_proposal()
        if p is None:
            return
        if p.status in ("committed", "skipped"):
            self.app.notify("Already committed — edit it in Graph & Evidence.",
                            severity="warning")
            return
        new = "rejected" if p.status in ("pending", "accepted") else "pending"
        repo.set_proposal_status(p.id, new)
        self._render_proposals(keep_cursor=True)

    def action_view_details(self) -> None:
        p = self._selected_proposal()
        if p is None:
            self.app.notify("Select a proposal row.", severity="warning")
            return
        self.app.push_screen(ProposalDetailModal(p))

    def action_restore_all(self) -> None:
        if not self.request_id or not self.run_id:
            return
        restored = 0
        for p in repo.list_proposals(self.request_id, run_id=self.run_id, status="rejected"):
            repo.set_proposal_status(p.id, "pending")
            restored += 1
        if restored:
            self._render_proposals(keep_cursor=True)
            self.app.notify(f"Restored {restored} rejected proposals.")

    # --- generate ---------------------------------------------------------------
    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "flow-generate":
            event.stop()
            self._start_generate()
        elif event.button.id == "flow-build":
            event.stop()
            self._start_build()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "flow-topic":
            event.stop()
            self._start_generate()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "flow-depth":
            self._refresh_key_status()

    def _ensure_request(self, topic: str) -> str:
        """Reuse the selected request when its title matches the topic;
        otherwise create a new one (the topic bar replaces Ctrl+N)."""
        req = repo.get(ResearchRequest, self.request_id) if self.request_id else None
        if req and (req.title or "").strip().casefold() == topic.casefold():
            return req.id
        req = repo.create_request(topic)
        app = self.app
        sidebar = app.query("Sidebar")
        if sidebar:
            sidebar.first().reload(select_id=req.id)
        app.current_request_id = req.id
        return req.id

    def _set_busy(self, busy: bool) -> None:
        self._flow_busy = busy
        self.query_one("#flow-build", Button).disabled = busy
        self._refresh_key_status()

    def _start_generate(self) -> None:
        if self._flow_busy:
            return
        topic = self.query_one("#flow-topic", Input).value.strip()
        if not topic:
            self.app.notify("Type a topic first.", severity="warning")
            return
        if not config.llm_api_key():
            key_name = ("OPENROUTER_API_KEY" if config.llm_provider() == "openrouter"
                        else "ANTHROPIC_API_KEY")
            self.app.notify(f"{key_name} missing — add it to .env.", severity="error")
            return
        rid = self._ensure_request(topic)
        log = self.query_one("#flow-log", Log)
        log.clear()
        tier = self._depth()
        log.write_line(f"Generating map for '{topic}' ({get_tier(tier).label})…")
        self._set_busy(True)
        self._generate_worker(rid, tier)

    @work(thread=True, exclusive=True)
    def _generate_worker(self, request_id: str, tier_key: str) -> None:
        def progress(line: str) -> None:
            self.app.call_from_thread(self._log_line, line)
        try:
            run = flow_engine.run_research(request_id, tier_key, progress=progress)
        except Exception as exc:
            self.app.call_from_thread(self._step_failed, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._generate_done, run.id, run.status, run.summary)

    def _log_line(self, line: str) -> None:
        self.query_one("#flow-log", Log).write_line(f"· {line}")

    def _generate_done(self, run_id: str, status: str, summary: str) -> None:
        self.run_id = run_id
        self._set_busy(False)
        self._log_line(f"[{status}] {summary}")
        self._render_proposals()
        if status.startswith("complete"):
            self.app.notify(f"{len(self._rows)} proposals staged — review, then Build.")
        else:
            self.app.notify(f"Research {status}: {summary}", severity="error")

    def _step_failed(self, message: str) -> None:
        self._set_busy(False)
        self._log_line(f"FAILED: {message}")
        self.app.notify(f"Failed: {message}", severity="error")

    # --- build -------------------------------------------------------------------
    def _start_build(self) -> None:
        if self._flow_busy:
            return
        if not self.request_id or not self.run_id:
            self.app.notify("Generate a map first.", severity="warning")
            return
        self._set_busy(True)
        self._log_line("Building map…")
        self._build_worker(self.request_id, self.run_id)

    @work(thread=True, exclusive=True)
    def _build_worker(self, request_id: str, run_id: str) -> None:
        def progress(line: str) -> None:
            self.app.call_from_thread(self._log_line, line)
        try:
            result = flow_engine.finalize(request_id, run_id, progress=progress)
        except Exception as exc:
            self.app.call_from_thread(self._step_failed, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._build_done, result)

    def _build_done(self, result: dict) -> None:
        self._set_busy(False)
        # Refresh siblings FIRST — our own load_request clears #flow-result.
        refresher = getattr(self.app, "refresh_dependent_panels", None)
        if refresher:
            refresher()
        self._render_proposals()
        total = sum(result["counts"].values())
        charts = result["charts"]
        self.query_one("#flow-result", Static).update(
            f"[b]{total}[/b] entities committed · trees: "
            + " · ".join(f"[b]{res}[/b]" for res in charts)
            + f" → {config.EXPORT_DIR}")
        self._open_chart(charts)
        self.app.notify("Map built — tree chart opened; all 3 levels exported.")

    def _open_chart(self, charts: dict) -> None:
        """Pop the finished tree chart open (macOS) — the result should be
        seen, not hunted for in a hidden folder."""
        if getattr(self.app, "is_headless", False) or sys.platform != "darwin":
            return
        chart = charts.get("professional") or next(iter(charts.values()), None)
        if chart:
            try:
                subprocess.Popen(["open", chart])
            except OSError:
                pass
