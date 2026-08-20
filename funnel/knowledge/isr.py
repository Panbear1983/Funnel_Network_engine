"""Read-only connector to the ISR knowledge base — the sibling
Investment_Strategy_Research_2026 repo's SQLite database of ~1,600 researched
public companies (tickers, countries, industries, tiers, 22-field dossiers,
weekly maintenance screenings with source URLs).

Design rules:
- The ISR repo is never modified; the DB is opened read-only, fully loaded
  into memory, and the connection closed immediately (zero lock exposure to
  the ISR writer, safe to hand across threads).
- `open_default()`/`get_isr()` return None instead of raising when the DB is
  absent, disabled (FUNNEL_ISR_DB=""), or unreadable — callers no-op.
- Never use funnel.research.ingest.slugify on ISR names: it collapses CJK
  text to "x". Matching here keeps CJK intact.

CLI for standalone lookups / smoke-testing:
    python -m funnel.knowledge.isr --lookup 2330
    python -m funnel.knowledge.isr --candidates "GPU,AI server" --limit 10
    python -m funnel.knowledge.isr --screenings 2345.TW --since 2026-06-01
    python -m funnel.knowledge.isr --stats [--json]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional

from funnel import config

# --- normalization tables --------------------------------------------------

# Exchange suffixes actually present in ISR tickers (verified against the live
# DB). Single-letter share classes (BRK.B, HEI.A) are NOT exchange suffixes
# and must not be stripped — ".T" (Tokyo) is the only single-letter exchange.
_KNOWN_SUFFIXES = {
    "TW", "TWO", "T", "KS", "KQ", "DE", "PA", "SH", "SS", "SZ", "L", "HK",
    "AS", "SW", "ST", "MC", "VI", "TO", "OL", "MI", "CO", "BK",
}
# ISR suffix -> Yahoo Finance suffix (everything else is already compatible).
_ISR_TO_YAHOO = {"SH": "SS"}

# Exchange suffix -> human exchange label (for CompanyNode.exchange).
EXCHANGE_LABELS = {
    "TW": "TWSE", "TWO": "TPEx", "T": "TSE", "KS": "KRX", "KQ": "KOSDAQ",
    "DE": "XETRA", "PA": "Euronext Paris", "SS": "SSE", "SH": "SSE",
    "SZ": "SZSE", "L": "LSE", "HK": "HKEX", "AS": "Euronext Amsterdam",
    "MI": "Borsa Italiana", "ST": "Nasdaq Stockholm", "SW": "SIX",
}

# ISR tier labels are dirty ("龍頭股。", prose-suffixed variants, blanks), so
# canonicalization is substring-based. Mapping reimplemented from the ISR
# repo's scripts/research_state.py taxonomy — kept independent on purpose.
_TIER_RULES = (
    ("二線龍頭", "second_leader"),
    ("龍頭", "leader"),
    ("龍體", "leader"),          # known typo of 龍頭
    ("隱形冠軍", "hidden_champion"),
    ("潛力", "potential"),
    ("輔助", "supporting"),
    ("次要供應", "minor_supplier"),
)

_NAME_TICKER_RE = re.compile(
    r"^(?P<name>.+?)\s*[（(](?P<ticker>[A-Za-z0-9.\-]{1,12})[）)]\s*$")
_TICKER_SPLIT_RE = re.compile(r"^(?P<base>.+)\.(?P<sfx>[A-Za-z]{1,3})$")

# Countries appear inconsistently ("USA" and "United States" both occur).
_COUNTRY_ALIASES = {
    "usa": "us", "united states": "us", "u.s.": "us", "america": "us",
    "taiwan": "tw", "台灣": "tw", "台湾": "tw",
    "south korea": "kr", "korea": "kr",
    "united kingdom": "uk", "great britain": "uk",
}


def _norm_country(value) -> str:
    # Tolerant of non-str input: LLM payloads sometimes emit a list/number for
    # a "country" field, and lookup() must degrade rather than crash the caller.
    if not isinstance(value, str):
        value = "" if value is None else str(value)
    v = value.strip().casefold()
    return _COUNTRY_ALIASES.get(v, v)


def _norm_name(value: str) -> str:
    """Casefolded name with punctuation/whitespace stripped; CJK kept intact."""
    return re.sub(r"[^0-9a-z㐀-䶿一-鿿]+", "",
                  (value or "").casefold())


def canonical_tier(raw: str) -> str:
    text = (raw or "").strip()
    for needle, canon in _TIER_RULES:
        if needle in text:
            return canon
    return "unknown"


def split_ticker(raw: str) -> tuple[str, str]:
    """(base, suffix) — suffix stripped only when it is a known exchange
    suffix, so 'BRK.B' stays whole while '2345.TW' -> ('2345', 'TW')."""
    raw = (raw or "").strip()
    m = _TICKER_SPLIT_RE.match(raw)
    if m and m.group("sfx").upper() in _KNOWN_SUFFIXES:
        return m.group("base"), m.group("sfx").upper()
    return raw, ""


def exchange_label(raw_ticker: str) -> str:
    """Human exchange label derived from a ticker's exchange suffix, or ''."""
    _base, sfx = split_ticker(raw_ticker)
    return EXCHANGE_LABELS.get(sfx, "")


def yahoo_ticker(raw: str, country: str = "") -> str:
    """Yahoo-Finance-compatible form of an ISR ticker: '.SH' -> '.SS', bare
    Taiwan numerics get '.TW'. Empty stays empty."""
    base, sfx = split_ticker(raw)
    if sfx:
        return f"{base}.{_ISR_TO_YAHOO.get(sfx, sfx)}"
    if base.isdigit() and _norm_country(country) == "tw":
        return f"{base}.TW"
    return base


# --- records -----------------------------------------------------------------


@dataclass(frozen=True)
class IsrCompany:
    company_id: int
    name: str          # ticker parens stripped: "台積電 (2330)" -> "台積電"
    raw_name: str
    ticker: str        # as stored ("2345.TW", "2330", "NVDA", "")
    base_ticker: str   # known exchange suffix stripped
    yahoo_ticker: str  # Yahoo-normalized form
    country: str
    industry: str      # ISR prose, verbatim (often CJK)
    tier: str          # canonical: leader/second_leader/hidden_champion/...
    raw_tier: str
    research_status: str
    dossier_updated_at: Optional[str] = None

    @property
    def label(self) -> str:
        return f"{self.name} ({self.ticker})" if self.ticker else self.name


@dataclass(frozen=True)
class IsrScreening:
    week_start: str
    needs_update: bool
    reason: str
    evidence_date: Optional[str] = None
    source_urls: list[str] = field(default_factory=list)


# --- connector ---------------------------------------------------------------


class IsrKnowledge:
    """In-memory snapshot of the ISR database. Construction reads everything
    and closes the connection; instances are immutable afterwards and safe to
    share across threads."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.db_mtime = self.path.stat().st_mtime
        self._companies: list[IsrCompany] = []
        self._dossiers: dict[int, dict] = {}
        self._screenings: dict[int, list[IsrScreening]] = {}
        self._by_ticker: dict[str, list[IsrCompany]] = {}
        self._by_base: dict[str, list[IsrCompany]] = {}
        self._by_yahoo: dict[str, list[IsrCompany]] = {}
        self._by_name: dict[str, list[IsrCompany]] = {}
        self._blobs: dict[int, str] = {}  # casefolded searchable text
        self._load()

    # -- loading --------------------------------------------------------------

    @staticmethod
    def _connect(path: Path) -> sqlite3.Connection:
        """Read-only open. The ISR DB is WAL-mode; a plain mode=ro open fails
        when the -wal/-shm sidecars are absent and cannot be created, so fall
        back to immutable=1 (a snapshot read — fine here because we load once
        and close; a concurrent mid-commit write could at worst be missed,
        never corrupted)."""
        uri = f"file:{path}?mode=ro"
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=5)
            conn.execute("SELECT 1 FROM sqlite_master LIMIT 1")
        except sqlite3.OperationalError:
            conn = sqlite3.connect(uri + "&immutable=1", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _load(self) -> None:
        conn = self._connect(self.path)
        try:
            rows = conn.execute(
                """SELECT c.id, c.ticker, c.company_name, c.country,
                          c.industry, c.tier, c.research_status,
                          r.record_json, r.updated_at AS dossier_updated_at
                   FROM companies c
                   LEFT JOIN company_research r ON r.company_id = c.id
                   WHERE c.research_status != 'excluded'""").fetchall()
            screens = conn.execute(
                """SELECT company_id, week_start, needs_update, reason,
                          evidence_date, source_urls_json
                   FROM maintenance_screenings
                   ORDER BY week_start DESC""").fetchall()
        finally:
            conn.close()

        for row in rows:
            raw_name = (row["company_name"] or "").strip()
            ticker = (row["ticker"] or "").strip()
            m = _NAME_TICKER_RE.match(raw_name)
            name = m.group("name").strip() if m else raw_name
            base, _sfx = split_ticker(ticker)
            company = IsrCompany(
                company_id=row["id"],
                name=name,
                raw_name=raw_name,
                ticker=ticker,
                base_ticker=base,
                yahoo_ticker=yahoo_ticker(ticker, row["country"] or ""),
                country=(row["country"] or "").strip(),
                industry=(row["industry"] or "").strip(),
                tier=canonical_tier(row["tier"] or ""),
                raw_tier=(row["tier"] or "").strip(),
                research_status=(row["research_status"] or "").strip(),
                dossier_updated_at=row["dossier_updated_at"],
            )
            self._companies.append(company)
            if row["record_json"]:
                try:
                    dossier = json.loads(row["record_json"])
                    if isinstance(dossier, dict):
                        self._dossiers[company.company_id] = dossier
                except (TypeError, ValueError):
                    pass

            for index, key in (
                (self._by_ticker, ticker.upper()),
                (self._by_base, base.upper()),
                (self._by_yahoo, company.yahoo_ticker.upper()),
                (self._by_name, _norm_name(name)),
                (self._by_name, _norm_name(raw_name)),
            ):
                if key:
                    bucket = index.setdefault(key, [])
                    if company not in bucket:
                        bucket.append(company)

            dossier = self._dossiers.get(company.company_id, {})
            blob_bits = [name, raw_name, ticker, company.country,
                         company.industry, company.raw_tier]
            for k in ("Sub-Sector", "Core Business", "Clients & Orders",
                      "Technical Moat", "Key Competitors"):
                blob_bits.append(str(dossier.get(k, "")))
            self._blobs[company.company_id] = " ".join(blob_bits).casefold()

        for row in screens:
            try:
                urls = json.loads(row["source_urls_json"] or "[]")
            except (TypeError, ValueError):
                urls = []
            self._screenings.setdefault(row["company_id"], []).append(
                IsrScreening(
                    week_start=row["week_start"],
                    needs_update=bool(row["needs_update"]),
                    reason=(row["reason"] or "").strip(),
                    evidence_date=row["evidence_date"],
                    source_urls=[u for u in urls if isinstance(u, str)],
                ))

    @classmethod
    def open_default(cls) -> Optional["IsrKnowledge"]:
        """Connector for the configured DB, or None when disabled/absent/
        unreadable. Never raises."""
        path = config.isr_db_path()
        if path is None:
            return None
        try:
            return cls(path)
        except Exception:
            return None

    # -- queries --------------------------------------------------------------

    def lookup(self, name_or_ticker: str,
               country: Optional[str] = None) -> Optional[IsrCompany]:
        """Match a company by ticker (raw, Yahoo, or base form) or by name
        (exact-normalized; CJK intact). Ambiguous ticker hits resolve via the
        country hint or return None. Both args are coerced to str first: callers
        pass unvalidated LLM payload fields, and a lookup must never crash the
        run (a bad hint just fails to match)."""
        query = str(name_or_ticker or "").strip()
        if not query:
            return None
        upper = query.upper()
        for index, key in (
            (self._by_ticker, upper),
            (self._by_yahoo, yahoo_ticker(upper, country or "").upper()),
            (self._by_yahoo, upper),
            (self._by_base, split_ticker(upper)[0]),
            (self._by_name, _norm_name(query)),
        ):
            hit = self._resolve(index.get(key, []), country)
            if hit:
                return hit
        return None

    def lookup_company(self, name: Optional[str], ticker: Optional[str],
                       country: Optional[str] = None) -> Optional[IsrCompany]:
        """The canonical ticker-first-then-name lookup every caller needs
        (grounding, enrichment, dossier, mining) — one implementation."""
        hit = self.lookup(ticker, country=country) if ticker else None
        if hit is None and name:
            hit = self.lookup(name, country=country)
        return hit

    @staticmethod
    def _resolve(bucket: list[IsrCompany],
                 country: Optional[str]) -> Optional[IsrCompany]:
        if len(bucket) == 1:
            return bucket[0]
        if len(bucket) > 1 and country:
            want = _norm_country(country)
            matches = [c for c in bucket if _norm_country(c.country) == want]
            if len(matches) == 1:
                return matches[0]
        return None

    def candidates(self, keywords: Iterable[str],
                   limit: int = 8) -> list[IsrCompany]:
        """Companies whose name/industry/dossier prose mentions the given
        keywords, best matches first. Advisory prompt-grounding only — never
        authoritative. needs_ticker_review rows are excluded."""
        terms = [t.strip().casefold() for t in keywords if t and t.strip()]
        if not terms:
            return []
        # ASCII terms match on word boundaries ("cell" must not hit
        # "excellent"); CJK terms match by substring (no word boundaries).
        matchers = [
            re.compile(rf"\b{re.escape(t)}\b").search if t.isascii()
            else re.compile(re.escape(t)).search
            for t in terms
        ]
        _tier_rank = {"leader": 0, "second_leader": 1, "hidden_champion": 2,
                      "potential": 3, "supporting": 4, "minor_supplier": 5,
                      "unknown": 6}
        scored = []
        for c in self._companies:
            if c.research_status == "needs_ticker_review":
                continue
            blob = self._blobs.get(c.company_id, "")
            score = sum(1 for m in matchers if m(blob))
            if score:
                scored.append((-score, _tier_rank.get(c.tier, 6), c.name, c))
        scored.sort(key=lambda item: item[:3])
        return [c for *_rank, c in scored[:limit]]

    def _key_id(self, key: "str | IsrCompany") -> Optional[int]:
        if isinstance(key, IsrCompany):
            return key.company_id
        hit = self.lookup(key)
        return hit.company_id if hit else None

    def dossier(self, key: "str | IsrCompany") -> Optional[dict]:
        """The parsed 22-field record_json for a company, or None."""
        company_id = self._key_id(key)
        return self._dossiers.get(company_id) if company_id else None

    def screenings(self, key: "str | IsrCompany", *,
                   needs_update_only: bool = True, limit: int = 5,
                   since: Optional[str] = None) -> list[IsrScreening]:
        """Maintenance screenings for a company, newest first."""
        company_id = self._key_id(key)
        if not company_id:
            return []
        rows = self._screenings.get(company_id, [])
        if needs_update_only:
            rows = [s for s in rows if s.needs_update]
        if since:
            rows = [s for s in rows if s.week_start >= since]
        return rows[:limit]

    def stats(self) -> dict:
        dates = [c.dossier_updated_at for c in self._companies
                 if c.dossier_updated_at]
        return {
            "path": str(self.path),
            "db_mtime": self.db_mtime,
            "companies": len(self._companies),
            "with_ticker": sum(1 for c in self._companies if c.ticker),
            "dossiers": len(self._dossiers),
            "screenings": sum(len(v) for v in self._screenings.values()),
            "dossier_updated_min": min(dates) if dates else None,
            "dossier_updated_max": max(dates) if dates else None,
        }

    # -- theme browsing (offline seeding) -------------------------------------

    def industries(self, limit: int = 40) -> list[tuple[str, int]]:
        """Distinct industries present, most-populated first — for a 'seed from
        ISR theme' picker. needs_ticker_review rows excluded."""
        counts: dict[str, int] = {}
        for c in self._companies:
            if c.research_status == "needs_ticker_review":
                continue
            ind = c.industry.strip()
            if ind:
                counts[ind] = counts.get(ind, 0) + 1
        return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:limit]

    def by_theme(self, *, industry: Optional[str] = None,
                 tier: Optional[str] = None, country: Optional[str] = None,
                 sub_sector: Optional[str] = None,
                 limit: int = 50) -> list[IsrCompany]:
        """Companies matching a theme filter, ranked leaders-first then by name.
        Every filter is optional (substring for industry/sub_sector, exact for
        canonical tier, normalized for country). needs_ticker_review excluded."""
        want_country = _norm_country(country) if country else None
        ind = (industry or "").strip()
        ss = (sub_sector or "").strip()
        rank = {"leader": 0, "second_leader": 1, "hidden_champion": 2,
                "potential": 3, "supporting": 4, "minor_supplier": 5, "unknown": 6}
        out: list[IsrCompany] = []
        for c in self._companies:
            if c.research_status == "needs_ticker_review":
                continue
            if ind and ind not in c.industry:
                continue
            if tier and c.tier != tier:
                continue
            if want_country and _norm_country(c.country) != want_country:
                continue
            if ss:
                sub = str(self._dossiers.get(c.company_id, {}).get("Sub-Sector", ""))
                if ss not in sub:
                    continue
            out.append(c)
        out.sort(key=lambda c: (rank.get(c.tier, 6), c.name))
        return out[:limit]


# --- cached accessor for the TUI ---------------------------------------------

_cache: tuple[tuple, Optional[IsrKnowledge]] | None = None
# get_isr() is now called from Textual worker threads (seed/mine) as well as
# the main thread, so guard the module-global cache.
_cache_lock = threading.Lock()


def _snapshot_key(path: Path) -> tuple:
    """Cache key that changes when the DB **or its -wal sidecar** changes.
    Under WAL, commits land in the -wal without bumping the main file's mtime
    until a checkpoint, so keying on the main mtime alone would keep serving a
    stale snapshot after the ISR writer commits new rows."""
    parts: list = [str(path)]
    for p in (path, path.with_name(path.name + "-wal")):
        try:
            st = p.stat()
            parts.append((st.st_mtime, st.st_size))
        except OSError:
            parts.append(None)
    return tuple(parts)


def get_isr() -> Optional[IsrKnowledge]:
    """Cached connector keyed by (path, main+wal stat): repeated panel loads
    reuse one snapshot, a new ISR write (even an uncheckpointed WAL commit)
    invalidates it. Pipeline workers should call IsrKnowledge.open_default()
    instead for a per-run instance."""
    global _cache
    path = config.isr_db_path()
    if path is None or not path.exists():
        with _cache_lock:
            _cache = None
        return None
    key = _snapshot_key(path)
    with _cache_lock:
        if _cache and _cache[0] == key:
            return _cache[1]
    # Build outside the lock (avoid holding it during I/O); a concurrent
    # double-build is harmless — same data, last writer wins.
    isr = IsrKnowledge.open_default()
    with _cache_lock:
        _cache = (key, isr)
        return isr


# --- CLI ----------------------------------------------------------------------


def _print_company(isr: IsrKnowledge, company: IsrCompany,
                   as_json: bool) -> None:
    if as_json:
        payload = asdict(company)
        payload["dossier"] = isr.dossier(company)
        payload["screenings"] = [asdict(s) for s in isr.screenings(company)]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(f"{company.label}  [{company.country} · {company.industry} · "
          f"{company.tier}]")
    print(f"  ticker: raw={company.ticker or '—'} "
          f"base={company.base_ticker or '—'} yahoo={company.yahoo_ticker or '—'}")
    print(f"  dossier updated: {company.dossier_updated_at or '—'}")
    dossier = isr.dossier(company) or {}
    core = str(dossier.get("Core Business", "")).strip()
    if core:
        print(f"  core business: {core[:200]}")
    screens = isr.screenings(company, needs_update_only=False, limit=3)
    for s in screens:
        flag = "!" if s.needs_update else " "
        print(f"  {flag} {s.week_start}  {s.reason[:120]}")


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m funnel.knowledge.isr",
        description="Standalone read-only lookups against the ISR knowledge DB.")
    parser.add_argument("--lookup", metavar="NAME_OR_TICKER")
    parser.add_argument("--country", help="disambiguation hint for --lookup")
    parser.add_argument("--candidates", metavar="KEYWORDS",
                        help="comma-separated keywords")
    parser.add_argument("--screenings", metavar="NAME_OR_TICKER")
    parser.add_argument("--since", help="week_start floor for --screenings")
    parser.add_argument("--limit", type=int, default=8)
    parser.add_argument("--stats", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    isr = IsrKnowledge.open_default()
    if isr is None:
        print("ISR knowledge base unavailable (set FUNNEL_ISR_DB or place the "
              "Investment_Strategy_Research_2026 repo beside this one).",
              file=sys.stderr)
        return 2

    if args.stats:
        stats = isr.stats()
        if args.as_json:
            print(json.dumps(stats, ensure_ascii=False, indent=2))
        else:
            for k, v in stats.items():
                print(f"{k}: {v}")
        return 0
    if args.lookup:
        company = isr.lookup(args.lookup, country=args.country)
        if company is None:
            print(f"No ISR match for {args.lookup!r}.", file=sys.stderr)
            return 1
        _print_company(isr, company, args.as_json)
        return 0
    if args.candidates:
        found = isr.candidates(args.candidates.split(","), limit=args.limit)
        if args.as_json:
            print(json.dumps([asdict(c) for c in found],
                             ensure_ascii=False, indent=2))
        else:
            for c in found:
                print(f"{c.label}  [{c.country} · {c.industry} · {c.tier}]")
        return 0
    if args.screenings:
        rows = isr.screenings(args.screenings, needs_update_only=False,
                              limit=args.limit, since=args.since)
        if args.as_json:
            print(json.dumps([asdict(s) for s in rows],
                             ensure_ascii=False, indent=2))
        else:
            for s in rows:
                flag = "!" if s.needs_update else " "
                print(f"{flag} {s.week_start}  {s.reason[:160]}")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
