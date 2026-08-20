"""Map tab — the one-topic golden path.

Type a topic and choose Build Map. The existing research, proposal safeguards,
map commit, and chart exports run automatically. An optional advanced path
keeps manual proposal review available without crowding the beginner flow.
"""

from __future__ import annotations

import json
import subprocess
import sys
from collections import Counter
from typing import Optional
from urllib.parse import urlparse

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (
    Button, Collapsible, DataTable, Input, Label, Log, Select, Static, Switch,
)

from funnel import config
from funnel.db import repo
from funnel.db.models import (
    CompanyNode,
    Proposal,
    ResearchRequest,
    ResearchRun,
    ResearchRunOutcome,
)
from funnel.knowledge.isr import get_isr
from funnel.intake.questions import (
    RESEARCH_CONTEXT_ID,
    RESEARCH_CONTEXT_SECTION,
    RESEARCH_CONTEXT_TEXT,
)
from funnel.research import flow as flow_engine
from funnel.research.tiers import TIER_ORDER, TIERS, get_tier, resolve_model
from funnel.ui.fieldutils import truncate
from funnel.ui.panels.dossier import (
    DossierScreen,
    resolve_dossier,
    staleness_suffix,
)

_MARK = {"pending": "✓", "accepted": "✓", "rejected": "✗",
         "committed": "→", "skipped": "–"}

_ACTIVE_STAGES = (
    ("Stage 5 of 5 — Building and exporting your map", (
        "automatic building", "building map", "committed ", "enriching companies",
        "tree chart", "report →",
    )),
    ("Stage 4 of 5 — Checking relationships and evidence", (
        "inferring relationships", "extracting evidence", "hunting evidence",
        "checking for contradictions", "relationship", "evidence",
    )),
    ("Stage 3 of 5 — Finding relevant companies", (
        "researching companies", "companies found", "unique companies",
    )),
    ("Stage 2 of 5 — Sketching the value-chain steps", (
        "discovering process stack", "process nodes", "planning smart search",
        "smart queries",
    )),
    ("Stage 1 of 5 — Understanding your topic", (
        "understanding the topic", "auto-brief", "brief built",
    )),
)


def _plain_stage(progress_line: str) -> Optional[str]:
    """Translate internal progress messages into stable beginner-facing stages."""
    normalized = progress_line.casefold()
    for label, markers in _ACTIVE_STAGES:
        if any(marker in normalized for marker in markers):
            return label
    return None


def _outcome_guidance(status: str, *, auto_build: bool, key_name: str) -> str:
    """Return one concise recovery/continuation action for every run outcome."""
    if status == ResearchRunOutcome.COMPLETE.value:
        next_step = (
            "finishing your map automatically."
            if auto_build else
            "review the proposed map items, then choose Finish Reviewed Map."
        )
        return f"[b]Research complete.[/b] Next: {next_step}"
    if status == ResearchRunOutcome.PARTIAL.value:
        next_step = (
            "finishing a map with the usable items found; refine it afterward if needed."
            if auto_build else
            "review the usable items found, then choose Finish Reviewed Map."
        )
        return f"[b]Some research was unavailable, but usable items were saved.[/b] Next: {next_step}"
    if status == ResearchRunOutcome.NEEDS_CREDENTIALS.value:
        hint = config.other_provider_hint()
        return (
            f"[b]Request saved; research credentials are needed.[/b] "
            f"Next: set {key_name} in .env at the repo root (copy .env.example "
            f"if you have no .env), then choose Build Map again."
            + (f" {hint}" if hint else "")
        )
    if status == ResearchRunOutcome.NO_CREDIBLE_RESULTS.value:
        return (
            "[b]No credible map items were found; your request is saved.[/b] "
            "Next: add one detail in the optional context, then choose Build Map again."
        )
    if status == ResearchRunOutcome.FAILED.value:
        return (
            "[b]Research stopped before a usable map was produced; your request is saved.[/b] "
            "Next: choose Build Map to retry, or adjust the topic or optional context first."
        )
    return ""


def _plural(count: int, singular: str, plural: Optional[str] = None) -> str:
    return f"{count} {singular if count == 1 else (plural or singular + 's')}"


def _source_urls(proposal: Proposal) -> set[str]:
    """Return only inspectable links already stored on a proposal."""
    values = list(proposal.sources or [])
    if proposal.entity_type == "evidence":
        values.append(proposal.payload.get("source_url", ""))
    return {
        value.strip()
        for value in values
        if isinstance(value, str)
        and value.strip().casefold().startswith(("http://", "https://"))
    }


def _unavailable_stages(run: ResearchRun) -> list[str]:
    """Extract stage names from stored skip log lines without exposing errors."""
    stages: list[str] = []

    def remember(value: str) -> None:
        stage = value.split(":", 1)[0].strip()
        if stage and stage not in stages:
            stages.append(stage)

    for line in run.log or []:
        if not isinstance(line, str) or not line.casefold().startswith("skipped "):
            continue
        remember(line[8:])
    # Initial failures such as auto-brief are stored in the terminal summary,
    # not as individual log entries.
    summary = run.summary or ""
    for marker in (". Skipped: ", ". Unavailable: "):
        if marker in summary:
            for value in summary.split(marker, 1)[1].split(";"):
                remember(value)
    return stages


def _coverage_summary(run: ResearchRun, proposals: list[Proposal]) -> str:
    """Explain stored run coverage and provenance without doing new research."""
    usable = [p for p in proposals if p.status not in {"rejected", "skipped"}]
    counts = Counter(p.entity_type for p in usable)
    found_parts = [
        _plural(counts["process_node"], "process step"),
        _plural(counts["company"], "company", "companies"),
        _plural(counts["edge"], "relationship"),
    ]
    if counts["deal"]:
        found_parts.append(_plural(counts["deal"], "deal"))

    evidence = [p for p in usable if p.entity_type == "evidence"]
    sourced_evidence = [p for p in evidence if _source_urls(p)]
    all_urls = set().union(*(_source_urls(p) for p in usable)) if usable else set()
    sites = sorted({
        urlparse(url).netloc.casefold().removeprefix("www.")
        for url in all_urls
        if urlparse(url).netloc
    })
    site_hint = f" ({', '.join(sites[:3])}{'…' if len(sites) > 3 else ''})" if sites else ""

    company_refs = {p.ref_key for p in usable if p.entity_type == "company"}
    sourced_company_refs: set[str] = set()
    for proposal in usable:
        if proposal.entity_type == "company" and _source_urls(proposal):
            sourced_company_refs.add(proposal.ref_key)
    for proposal in sourced_evidence:
        sourced_company_refs.update(proposal.payload.get("related_companies", []))
    sourced_company_refs.intersection_update(company_refs)

    evidence_pairs = [
        set(p.payload.get("related_companies", []))
        for p in sourced_evidence
    ]
    edges = [p for p in usable if p.entity_type == "edge"]
    covered_edges = 0
    for edge in edges:
        pair = {
            edge.payload.get("from_company", ""),
            edge.payload.get("to_company", ""),
        }
        if _source_urls(edge) or (
            "" not in pair and any(pair <= evidence_pair for evidence_pair in evidence_pairs)
        ):
            covered_edges += 1

    if evidence:
        provenance = (
            f"{len(sourced_evidence)} of {len(evidence)} saved evidence items include "
            f"a source link; {_plural(len(all_urls), 'unique link')}{site_hint}. "
            f"{len(sourced_company_refs)} of {counts['company']} companies and "
            f"{covered_edges} of {len(edges)} relationships are linked to sourced evidence."
        )
    else:
        provenance = (
            "No evidence items with inspectable source links were saved for this run."
        )

    uncertainties: list[str] = []
    unverified = [p for p in usable if p.entity_type == "evidence"
                  and p.payload.get("provenance") == "model_knowledge"]
    if unverified:
        uncertainties.append(
            f"{_plural(len(unverified), 'evidence item')} "
            f"{'is' if len(unverified) == 1 else 'are'} model-recalled "
            f"(no source was fetched; links unverified)"
        )
    if not evidence:
        uncertainties.append("the run saved no source-backed evidence")
    missing_evidence_links = len(evidence) - len(sourced_evidence)
    if missing_evidence_links:
        uncertainties.append(
            f"{_plural(missing_evidence_links, 'evidence item')} "
            f"{'has' if missing_evidence_links == 1 else 'have'} no source link"
        )
    uncovered_edges = len(edges) - covered_edges
    if uncovered_edges:
        uncertainties.append(
            f"{_plural(uncovered_edges, 'relationship')} "
            f"{'lacks' if uncovered_edges == 1 else 'lack'} saved source-backed evidence"
        )
    unavailable = _unavailable_stages(run)
    if unavailable:
        shown = ", ".join(unavailable[:2])
        if len(unavailable) > 2:
            shown += f", and {len(unavailable) - 2} more"
        uncertainties.append(f"coverage is incomplete because {shown} was unavailable")
    elif run.status == ResearchRunOutcome.PARTIAL.value:
        uncertainties.append("the run is partial because some research was unavailable")
    if not uncertainties:
        uncertainties.append(
            "no stored source gap was flagged, but the saved evidence still needs review"
        )

    return (
        "[b]Research coverage[/b]\n"
        f"Found: {', '.join(found_parts[:-1])}, and {found_parts[-1]}.\n"
        f"Sources: {provenance}\n"
        f"Uncertain: {'; '.join(uncertainties)}.\n"
        "Inspect: expand Advanced review and press [b]v[/b] on an item; after "
        "building, open Advanced → Evidence (F2) for claims, links, and quotes."
    )


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
        Binding("d", "show_dossier", "Dossier"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.add_class("request-aware")
        self.request_id: Optional[str] = None
        self.run_id: Optional[str] = None
        self._rows: list[str] = []
        self._flow_busy = False
        self._dossier_company_id: Optional[str] = None
        self._isr_industry_options: Optional[list] = None

    # --- layout ------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Static("Build a value-chain map", classes="panel-title")
        yield Static(
            "Enter a topic and choose Build Map. The app will research, build, "
            "and export the best map it can; timing and source coverage depend "
            "on the topic.",
            classes="flow-hint")
        yield Input(placeholder="Topic of interest — e.g. Chip manufacturing",
                    id="flow-topic")
        yield Input(placeholder=RESEARCH_CONTEXT_TEXT, id="flow-context")
        with Horizontal(classes="toolbar flow-build-controls"):
            yield Static("Depth", classes="flow-control-label")
            yield Select([(TIERS[k].label, k) for k in TIER_ORDER],
                         value="professional", allow_blank=False, id="flow-depth")
            yield Button("Build Map", id="flow-generate", variant="primary")
        # Offline path: seed a map straight from the ISR knowledge base (no
        # LLM, no API key). Hidden entirely when ISR is absent.
        with Horizontal(id="isr-seed-bar", classes="toolbar"):
            yield Static("ISR seed", classes="flow-control-label")
            yield Select([], prompt="Any industry", allow_blank=True,
                         id="isr-industry")
            yield Select(
                [("Leaders", "leader"), ("Second-tier", "second_leader"),
                 ("Hidden champions", "hidden_champion"), ("Potential", "potential"),
                 ("Supporting", "supporting"), ("Minor suppliers", "minor_supplier")],
                prompt="Any tier", allow_blank=True, id="isr-tier")
            yield Button("Seed from ISR (no LLM)", id="isr-seed-btn")
        with Vertical(id="flow-feedback"):
            yield Static(
                "[b]Optional novice-pilot feedback[/b]\n"
                "Was this map useful? Your response stays in the local app "
                "database and is never treated as research evidence.",
                classes="flow-feedback-hint",
            )
            with Horizontal(classes="flow-feedback-fields"):
                yield Select(
                    [("Yes — useful", "yes"), ("No — not useful", "no")],
                    prompt="Was this map useful?",
                    id="flow-feedback-useful",
                )
                yield Input(
                    placeholder="Short note (optional, 500 characters)",
                    max_length=500,
                    id="flow-feedback-note",
                )
            with Horizontal(classes="toolbar flow-feedback-actions"):
                yield Button("Save locally", id="flow-feedback-save",
                             variant="primary")
                yield Button("Skip", id="flow-feedback-skip")
        yield Static("", id="flow-keys")
        # ISR dossier of the headline company — placed high so it is never
        # clipped below the log, with a visible button (the 'd' key is stolen
        # by a focused topic Input). Hidden until a build produces a match.
        with Horizontal(id="flow-dossier-bar", classes="toolbar"):
            yield Static("", id="flow-dossier")
            yield Button("View dossier", id="flow-dossier-btn")
        yield Static("", id="flow-stage")
        yield Log(id="flow-log", classes="research-log")
        yield Static("", id="flow-coverage")
        with Collapsible(
            title="Advanced (optional): review map items before finishing",
            collapsed=True,
            id="flow-advanced-review",
        ):
            yield Static(
                "Turn automatic building off before Build Map to pause after "
                "research and review every proposed item.",
                classes="flow-hint",
            )
            with Horizontal(classes="toolbar"):
                yield Switch(value=True, id="auto-build")
                yield Static("build automatically", classes="switch-label")
            yield Static("Review map items: click/Enter excludes or restores · "
                         "v details · a restore all",
                         id="flow-review-hint", classes="flow-hint")
            yield DataTable(id="flow-table", zebra_stripes=True)
            with Horizontal(classes="toolbar"):
                yield Button("Finish Reviewed Map",
                             id="flow-build", variant="success")
        yield Static("", id="flow-result")

    def on_mount(self) -> None:
        table = self.query_one("#flow-table", DataTable)
        table.cursor_type = "row"
        table.add_columns(" ", "Type", "Name", "Detail", "Conf", "Status")
        self._hide_feedback()
        self.query_one("#flow-dossier-bar").display = False
        self._populate_isr_seed()
        self._refresh_key_status()

    def _populate_isr_seed(self) -> None:
        """Show + fill the ISR-seed controls when the knowledge base is
        present; hide them entirely otherwise (Map parity when ISR is off)."""
        bar = self.query_one("#isr-seed-bar")
        isr = get_isr()
        if isr is None:
            bar.display = False
            return
        bar.display = True
        options = [(f"{ind} ({n})", ind) for ind, n in isr.industries(limit=30)]
        # load_request (hence this method) runs on every dependent-panel
        # refresh; set_options resets the Select to blank, so only rebuild when
        # the option set actually changed, and restore the user's choice.
        if options == self._isr_industry_options:
            return
        self._isr_industry_options = options
        sel = self.query_one("#isr-industry", Select)
        current = sel.value
        sel.set_options(options)
        if current is not Select.NULL and any(v == current for _, v in options):
            sel.value = current

    def _refresh_key_status(self) -> None:
        provider = config.llm_provider()
        key_name = config.llm_key_name()
        have_llm = bool(config.llm_api_key())
        have_tavily = bool(config.tavily_api_key())
        tier = get_tier(self._depth())
        fallback = {"openrouter": "the model's :online web plugin",
                    "anthropic": "Claude's own web search"}.get(
                        provider, "no model web search — Tavily recommended")
        isr = get_isr()
        isr_bit = (f"[green]●[/] ISR {isr.stats()['companies']:,} co."
                   if isr is not None else "[dim]○ ISR off[/]")
        bits = [
            f"[{'green' if have_llm else 'red'}]●[/] {key_name}",
            f"[{'green' if have_tavily else 'yellow'}]●[/] TAVILY_API_KEY",
            isr_bit,
            f"{provider} · model [b]{resolve_model(tier)}[/b]",
        ]
        if not have_llm:
            hint = config.other_provider_hint()
            bits.append(
                f"[red]set {key_name} in .env to enable research[/red]"
                + (f" [yellow]{hint}[/yellow]" if hint else ""))
        elif not have_tavily:
            bits.append(f"[yellow]no Tavily — {fallback} will be used[/yellow]")
        self.query_one("#flow-keys", Static).update("  ".join(bits))
        self.query_one("#flow-generate", Button).disabled = self._flow_busy

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
        answers = repo.get_intake_answers(request_id) if request_id else {}
        context = answers.get(RESEARCH_CONTEXT_ID)
        self.query_one("#flow-context", Input).value = (
            context.answer_value if context else "")
        latest = repo.latest_run(request_id) if request_id else None
        self.run_id = latest.id if latest else None
        self.query_one("#flow-result", Static).update("")
        self._hide_feedback()
        self._render_proposals()
        self._render_coverage()
        self._update_dossier_line()
        self._populate_isr_seed()

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
        proposals = repo.list_proposals(self.request_id, run_id=self.run_id)
        # Doubtful first: auto/manual rejections, then low-confidence, then
        # the rest — so the rows worth a human glance lead the table.
        proposals.sort(key=lambda p: (
            0 if p.status == "rejected" else (1 if p.confidence < 0.4 else 2)))
        for p in proposals:
            table.add_row(_MARK.get(p.status, "?"), p.entity_type,
                          truncate(self._proposal_label(p), 24), self._detail(p),
                          f"{p.confidence:.2f}", p.status)
            self._rows.append(p.id)
        if keep_cursor and self._rows:
            table.move_cursor(row=min(cursor, len(self._rows) - 1))

    def _render_coverage(self) -> None:
        target = self.query_one("#flow-coverage", Static)
        if not self.request_id or not self.run_id:
            target.update("")
            return
        run = repo.get(ResearchRun, self.run_id)
        if run is None:
            target.update("")
            return
        proposals = repo.list_proposals(self.request_id, run_id=self.run_id)
        target.update(_coverage_summary(run, proposals))

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
            self.app.notify(
                "Already committed — edit it in the Advanced tab (F2).",
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
        elif event.button.id == "flow-feedback-save":
            event.stop()
            self._save_feedback()
        elif event.button.id == "flow-feedback-skip":
            event.stop()
            self._hide_feedback()
        elif event.button.id == "flow-dossier-btn":
            event.stop()
            self.action_show_dossier()
        elif event.button.id == "isr-seed-btn":
            event.stop()
            self._start_isr_seed()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id in ("flow-topic", "flow-context"):
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

    def _save_research_context(self, request_id: str, value: str) -> None:
        """Persist a supplied Map note without creating a required blank answer."""
        existing = repo.get_intake_answers(request_id).get(RESEARCH_CONTEXT_ID)
        if not value and existing is None:
            return
        repo.save_intake_answer(
            request_id,
            RESEARCH_CONTEXT_SECTION,
            RESEARCH_CONTEXT_ID,
            RESEARCH_CONTEXT_TEXT,
            value,
        )

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
        research_context = self.query_one("#flow-context", Input).value.strip()
        rid = self._ensure_request(topic)
        self._save_research_context(rid, research_context)
        log = self.query_one("#flow-log", Log)
        log.clear()
        self.query_one("#flow-coverage", Static).update("")
        self.query_one("#flow-result", Static).update("")
        self._hide_feedback()
        tier = self._depth()
        log.write_line(f"Building map for '{topic}' ({get_tier(tier).label})…")
        self._set_busy(True)
        self._set_active_stage("Stage 1 of 5 — Understanding your topic")
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
        if self._flow_busy:
            stage = _plain_stage(line)
            if stage:
                self._set_active_stage(stage)
        self.query_one("#flow-log", Log).write_line(f"· {line}")

    def _set_active_stage(self, stage: Optional[str]) -> None:
        self.query_one("#flow-stage", Static).update(
            f"[b]{stage}[/b]" if stage else "")

    def _generate_done(self, run_id: str, status: str, summary: str) -> None:
        self.run_id = run_id
        self._set_busy(False)
        self._set_active_stage(None)
        self._log_line(f"[{status}] {summary}")
        self._render_proposals()
        self._render_coverage()
        auto_build = self.query_one("#auto-build", Switch).value
        key_name = config.llm_key_name()
        guidance = _outcome_guidance(
            status, auto_build=auto_build, key_name=key_name)
        if guidance:
            self.query_one("#flow-result", Static).update(guidance)
        if status == ResearchRunOutcome.NEEDS_CREDENTIALS.value:
            self.app.notify(
                f"Research credentials are needed; the request was saved.",
                severity="warning",
            )
            return
        if status == ResearchRunOutcome.NO_CREDIBLE_RESULTS.value:
            self.app.notify("No credible results yet; refine and try again.",
                            severity="warning")
            return
        if status == ResearchRunOutcome.FAILED.value:
            self.app.notify(f"Research failed: {summary}", severity="error")
            return
        if status not in {
            ResearchRunOutcome.COMPLETE.value,
            ResearchRunOutcome.PARTIAL.value,
        }:
            self.app.notify(f"Unknown research outcome '{status}': {summary}",
                            severity="error")
            return
        if status == ResearchRunOutcome.PARTIAL.value:
            self.app.notify(
                "Partial research saved — building the usable map items found.",
                severity="warning",
            )
        if self._rows and auto_build:
            self._log_line("automatic building on — finishing map…")
            self._start_build()
        elif self._rows:
            self.app.notify(
                f"{len(self._rows)} map items ready — review, then Finish Reviewed Map.")
        else:
            self.app.notify("No buildable map items were staged.", severity="warning")

    def _step_failed(self, message: str) -> None:
        self._set_busy(False)
        self._set_active_stage(None)
        self._hide_feedback()
        self._log_line(f"FAILED: {message}")
        self.query_one("#flow-result", Static).update(
            "[b]This run stopped unexpectedly.[/b] "
            "Next: choose Build Map to retry.")
        self.app.notify(f"Failed: {message}", severity="error")

    # --- build -------------------------------------------------------------------
    def _start_build(self) -> None:
        if self._flow_busy:
            return
        if not self.request_id or not self.run_id:
            self.app.notify("Choose Build Map first.", severity="warning")
            return
        self._set_busy(True)
        self._set_active_stage("Stage 5 of 5 — Building and exporting your map")
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
        self._set_active_stage(None)
        # Refresh siblings FIRST — our own load_request clears #flow-result.
        refresher = getattr(self.app, "refresh_dependent_panels", None)
        if refresher:
            refresher()
        self._render_proposals()
        counts = result["counts"]
        merged = counts.get("merged", 0)
        new_total = sum(v for k, v in counts.items() if k not in ("merged", "enriched"))
        charts = result["charts"]
        self.query_one("#flow-result", Static).update(
            f"[b]{new_total}[/b] new" + (f" · [b]{merged}[/b] merged" if merged else "")
            + " · trees: " + " · ".join(f"[b]{res}[/b]" for res in charts)
            + f" → {config.EXPORT_DIR}")
        self._update_dossier_line()
        self._open_chart(charts)
        self._show_feedback()
        self.app.notify("Map built — view it in the Tree tab; charts also "
                        "exported and opened in Preview.")

    # --- ISR dossier line ---------------------------------------------------
    def _update_dossier_line(self) -> None:
        """Surface the ISR dossier of the request's headline company (highest
        confidence with an ISR match). Empty — and invisible — when the ISR
        knowledge base is absent, so the Map looks exactly as before."""
        line = self.query_one("#flow-dossier", Static)
        bar = self.query_one("#flow-dossier-bar")
        self._dossier_company_id = None
        isr = get_isr()
        if isr is None or not self.request_id:
            line.update("")
            bar.display = False
            return
        companies = repo.list_for_request(CompanyNode, self.request_id)
        for c in sorted(companies, key=lambda c: c.confidence_score,
                        reverse=True):
            hit = None
            if c.ticker:
                hit = isr.lookup(c.ticker, country=c.country)
            if hit is None and c.name:
                hit = isr.lookup(c.name, country=c.country)
            if hit is not None:
                self._dossier_company_id = c.id
                updated = (hit.dossier_updated_at or "unknown")[:10]
                line.update(f"dossier: [b]{hit.label}[/b] · ISR updated "
                            f"{updated}{staleness_suffix(hit.dossier_updated_at)}")
                bar.display = True
                return
        line.update("")
        bar.display = False

    # --- offline ISR seed ---------------------------------------------------
    def _select_opt(self, selector: str) -> Optional[str]:
        # Select.NULL is the blank sentinel in this Textual version (matches
        # FormScreen._select_value); Select.BLANK would miss it.
        widget = self.query_one(selector, Select)
        return None if widget.value is Select.NULL else (str(widget.value) or None)

    def _start_isr_seed(self) -> None:
        if self._flow_busy:
            return
        if get_isr() is None:
            self.app.notify("ISR knowledge base is unavailable.", severity="warning")
            return
        industry = self._select_opt("#isr-industry")
        tier = self._select_opt("#isr-tier")
        topic = (self.query_one("#flow-topic", Input).value.strip()
                 or industry or "ISR seed")
        request_id = self._ensure_request(topic)
        self._set_busy(True)
        self._set_active_stage("Seeding a map from ISR (no LLM)…")
        self._log_line("Seeding from the ISR knowledge base…")
        self._isr_seed_worker(request_id, industry, tier)

    @work(thread=True, exclusive=True)
    def _isr_seed_worker(self, request_id: str, industry: Optional[str],
                         tier: Optional[str]) -> None:
        from funnel.knowledge.isr_seed import run_isr_seed

        def progress(line: str) -> None:
            self.app.call_from_thread(self._log_line, line)
        try:
            result = run_isr_seed(request_id, industry=industry, tier=tier,
                                  progress=progress)
        except Exception as exc:
            self.app.call_from_thread(
                self._step_failed, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._isr_seed_done, result)

    def _isr_seed_done(self, result: dict) -> None:
        self._set_busy(False)
        self._set_active_stage(None)
        status = result.get("status")
        if status != "ok":
            msg = {"empty": "No ISR companies match that theme.",
                   "no_isr": "ISR knowledge base is unavailable."}.get(
                       status, f"ISR seed: {status}.")
            self.app.notify(msg, severity="warning")
            return
        refresher = getattr(self.app, "refresh_dependent_panels", None)
        if refresher:
            refresher()
        self._render_proposals()
        self._update_dossier_line()
        charts = result.get("charts", {})
        nodes = result.get("process_node", 0)
        companies = result.get("company", 0)
        self.query_one("#flow-result", Static).update(
            f"[b]{nodes}[/b] nodes · [b]{companies}[/b] companies seeded from "
            "ISR (offline, no LLM)"
            + (" · trees: " + " · ".join(f"[b]{r}[/b]" for r in charts)
               if charts else ""))
        if charts:
            self._open_chart(charts)
        self.app.notify(f"Seeded {companies} companies from ISR (no LLM) — "
                        "view it in the Tree tab.")

    def action_show_dossier(self) -> None:
        if not self._dossier_company_id:
            return
        company = repo.get(CompanyNode, self._dossier_company_id)
        bundle = resolve_dossier(company) if company else None
        if bundle is None:
            self.app.notify("No ISR dossier available.", severity="information")
            return
        self.app.push_screen(DossierScreen(company, bundle))

    # --- optional local pilot feedback -----------------------------------------
    def _show_feedback(self) -> None:
        useful = self.query_one("#flow-feedback-useful", Select)
        useful.clear()
        self.query_one("#flow-feedback-note", Input).value = ""
        feedback = self.query_one("#flow-feedback")
        feedback.display = True

    def _hide_feedback(self) -> None:
        feedback = self.query_one("#flow-feedback")
        feedback.display = False

    def _save_feedback(self) -> None:
        if not self.request_id or not self.run_id:
            self._hide_feedback()
            return
        useful_value = self.query_one("#flow-feedback-useful", Select).value
        if useful_value not in {"yes", "no"}:
            self.app.notify(
                "Choose whether the map was useful, or choose Skip.",
                severity="warning",
            )
            return
        note = self.query_one("#flow-feedback-note", Input).value
        repo.save_map_feedback(
            self.request_id,
            self.run_id,
            useful=useful_value == "yes",
            note=note,
        )
        self._hide_feedback()
        self.app.notify("Feedback saved locally. Thank you.")

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
