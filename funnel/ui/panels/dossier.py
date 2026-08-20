"""ISR company dossier view — renders the 22-field research record kept in
the sibling ISR knowledge base for a committed company. Resolution prefers a
live connector lookup and falls back to the copy stored by enrichment under
`CompanyNode.financials["isr"]`, so exports/offline sessions still work."""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from funnel.db.models import CompanyNode
from funnel.knowledge.isr import get_isr

# The 22 record_json keys in reading order (identity -> business -> outlook).
DOSSIER_FIELDS: tuple[str, ...] = (
    "Company", "Country", "Sub-Sector", "Industry", "Tier", "Timeframe",
    "Capital/Market Cap", "Core Business", "Clients & Orders",
    "Technical Moat", "Key Competitors", "Revenue Breakdown",
    "Gross Margin Profile", "12M Catalysts", "Key Investment Risks",
    "CEO & Management", "Core Patents & IP", "3-Year M&A",
    "CapEx & Expansion", "Geopolitical Exposure", "M&A Potential",
    "Recent Insider Trading",
)


def resolve_dossier(company: CompanyNode) -> Optional[dict]:
    """Dossier bundle for a company, or None when the ISR knows nothing.
    {'label', 'fields', 'updated_at', 'screenings', 'live'}"""
    isr = get_isr()
    if isr is not None:
        hit = None
        if company.ticker:
            hit = isr.lookup(company.ticker, country=company.country)
        if hit is None and company.name:
            hit = isr.lookup(company.name, country=company.country)
        if hit is not None:
            fields = isr.dossier(hit)
            if fields:
                return {
                    "label": hit.label,
                    "fields": fields,
                    "updated_at": hit.dossier_updated_at,
                    "screenings": isr.screenings(hit, needs_update_only=False,
                                                 limit=3),
                    "live": True,
                }
    stored = (company.financials or {}).get("isr") or {}
    if stored.get("dossier"):
        ticker = stored.get("ticker", "")
        return {
            "label": f"{company.name} ({ticker})" if ticker else company.name,
            "fields": stored["dossier"],
            "updated_at": stored.get("dossier_updated_at"),
            "screenings": [],
            "live": False,
        }
    return None


def staleness_age_days(updated_at: Optional[str]) -> Optional[int]:
    """Age in days of an ISR dossier date (YYYY-MM-DD...), or None when
    missing/unparseable."""
    if not updated_at:
        return None
    try:
        stamp = date.fromisoformat(updated_at[:10])
    except ValueError:
        return None
    return (datetime.now().date() - stamp).days


def staleness_line(updated_at: Optional[str]) -> str:
    """Rich-markup header line: yellow past 30 days, red past 90."""
    if not updated_at:
        return "[dim]ISR dossier — update date unknown[/dim]"
    age = staleness_age_days(updated_at)
    if age is None:
        return f"[dim]ISR dossier — updated {updated_at}[/dim]"
    text = f"ISR dossier — updated {updated_at[:10]} ({age}d ago)"
    if age > 90:
        return f"[red]{text} — stale[/red]"
    if age > 30:
        return f"[yellow]{text} — aging[/yellow]"
    return f"[green]{text}[/green]"


def staleness_suffix(updated_at: Optional[str]) -> str:
    """Compact colored age tag for one-line contexts (e.g. the Map dossier
    line): '' when the date is missing/unparseable, else ' (Nd ago[ — aging/
    stale])' colored green/yellow/red."""
    age = staleness_age_days(updated_at)
    if age is None:
        return ""
    if age > 90:
        return f" [red]({age}d ago — stale)[/red]"
    if age > 30:
        return f" [yellow]({age}d ago — aging)[/yellow]"
    return f" [green]({age}d ago)[/green]"


class DossierScreen(ModalScreen[None]):
    """Read-only modal rendering one company's ISR dossier."""

    BINDINGS = [Binding("escape", "dismiss_modal", "Close")]

    def __init__(self, company: CompanyNode, bundle: dict) -> None:
        super().__init__()
        self._company = company
        self._bundle = bundle

    def compose(self) -> ComposeResult:
        b = self._bundle
        with Vertical(classes="dialog form-dialog"):
            yield Label(f"ISR dossier — {b['label']}", classes="dialog-title")
            with VerticalScroll(classes="form-body"):
                yield Static(staleness_line(b["updated_at"])
                             + ("" if b["live"] else "  [dim](stored copy)[/dim]"))
                fields = b["fields"]
                for key in DOSSIER_FIELDS:
                    value = str(fields.get(key, "") or "").strip()
                    if not value:
                        continue
                    yield Label(key, classes="field-label")
                    yield Static(value, classes="detail-block")
                if b["screenings"]:
                    yield Label("Latest maintenance screenings",
                                classes="field-label")
                    lines = []
                    for s in b["screenings"]:
                        flag = "!" if s.needs_update else "·"
                        lines.append(f"{flag} {s.week_start}  {s.reason}")
                    yield Static("\n".join(lines), classes="detail-block")
            with Horizontal(classes="form-actions"):
                yield Button("Close", id="dossier-close", variant="primary")
                # Phase 5: mine supplier/customer/competitor edges from this
                # dossier's prose. Only when the live connector is present.
                if get_isr() is not None:
                    yield Button("Mine ISR relationships", id="dossier-mine")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "dossier-mine":
            event.stop()
            self._start_mine()
            return
        self.dismiss(None)

    def action_dismiss_modal(self) -> None:
        self.dismiss(None)

    # --- Phase 5: mine relationships from this dossier -----------------------
    def _start_mine(self) -> None:
        try:
            btn = self.query_one("#dossier-mine", Button)
            btn.disabled = True
            btn.label = "Mining…"
        except Exception:
            pass
        self.app.notify("Mining ISR relationships…")
        self._mine_worker()

    @work(thread=True, exclusive=True)
    def _mine_worker(self) -> None:
        from funnel.knowledge.isr_edges import run_isr_edge_mining
        result = run_isr_edge_mining(
            self._company.request_id, self._company.id)
        self.app.call_from_thread(self._mine_done, result)

    def _mine_done(self, result: dict) -> None:
        status = result.get("status")
        if status == "ok":
            self.app.notify(
                f"ISR mining: +{result.get('edge', 0)} edges, "
                f"+{result.get('company', 0)} companies.")
            refresher = getattr(self.app, "refresh_dependent_panels", None)
            if refresher:
                refresher()
            self.dismiss(None)
            return
        messages = {
            "needs_credentials": ("An API key is required to mine relationships.",
                                  "warning"),
            "no_dossier": ("No ISR dossier to mine for this company.", "warning"),
            "no_isr": ("ISR knowledge base is unavailable.", "warning"),
            "failed": (f"ISR mining failed: {result.get('error', '')[:80]}",
                       "error"),
        }
        text, severity = messages.get(status, (f"ISR mining: {status}.", "warning"))
        self.app.notify(text, severity=severity)
        try:
            btn = self.query_one("#dossier-mine", Button)
            btn.disabled = False
            btn.label = "Mine ISR relationships"
        except Exception:
            pass
