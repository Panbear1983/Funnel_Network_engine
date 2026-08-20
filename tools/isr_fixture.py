"""Build a small fixture copy of the ISR knowledge DB for offline smokes.
Schema mirrors Investment_Strategy_Research_2026/scripts/research_state.py
(verified against the live DB). Rows cover the real-world quirks: suffixed and
bare tickers, CJK names with embedded tickers, the .SH exchange, dirty tier
labels, blank tickers, excluded and needs_ticker_review statuses."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

_DOSSIER_KEYS = (
    "Country", "Timeframe", "Sub-Sector", "Industry", "Tier",
    "Company", "Capital/Market Cap", "Core Business", "Clients & Orders",
    "Technical Moat", "Revenue Breakdown", "Gross Margin Profile",
    "Key Competitors", "12M Catalysts", "Key Investment Risks",
    "CEO & Management", "Core Patents & IP", "3-Year M&A",
    "CapEx & Expansion", "Geopolitical Exposure", "M&A Potential",
    "Recent Insider Trading",
)


def _dossier(**overrides: str) -> str:
    record = {k: overrides.get(k, f"{k} placeholder") for k in _DOSSIER_KEYS}
    return json.dumps(record, ensure_ascii=False)


# (id, ticker, name, country, industry, tier, status, record_json or None)
_COMPANIES = [
    (1, "2345.TW", "Accton (2345.TW)", "Taiwan", "網路通訊設備", "龍頭股",
     "deep_researched",
     _dossier(**{
         "Core Business": "白牌網路交換器 (white-box networking switches) for "
                          "hyperscale data centers, 800G Ethernet",
         "Clients & Orders": "Meta、Amazon 等 CSP 客戶",
         "Key Competitors": "Cisco, Arista, Celestica",
     })),
    (2, "2330", "台積電 (2330)", "Taiwan", "半導體業", "龍頭股",
     "deep_researched",
     _dossier(**{
         "Core Business": "晶圓代工 (wafer foundry), advanced nodes, CoWoS",
         "Clients & Orders": "NVIDIA、AMD、Apple",
     })),
    (3, "NVDA", "NVIDIA (NVDA)", "USA", "半導體業", "龍頭股",
     "deep_researched",
     _dossier(**{"Core Business": "GPU and AI accelerator platforms"})),
    (4, "600584.SH", "JCET (600584.SH)", "China", "半導體業", "潛力股",
     "deep_researched",
     _dossier(**{"Core Business": "OSAT packaging and test"})),
    (5, "", "Mystery Co", "USA", "", "龍頭股。", "deep_researched", None),
    (6, "9999.T", "Excluded Corp (9999.T)", "Japan", "電子", "輔助公司",
     "excluded", None),
    (7, "123456", "Review Co (123456)", "China", "半導體業", "潛力股",
     "needs_ticker_review", None),
    # Two rows sharing base ticker '1234' across markets — exercises lookup()
    # country disambiguation (base-only lookup is ambiguous without a hint).
    (8, "1234.TW", "雙掛牌台廠 (1234.TW)", "Taiwan", "機械", "潛力股",
     "deep_researched", None),
    (9, "1234.T", "Dual-listed JP (1234.T)", "Japan", "機械", "潛力股",
     "deep_researched", None),
]

# (company_id, week_start, provider, needs_update, reason, evidence_date, urls)
_SCREENINGS = [
    (1, "2026-08-04", "fixture", 1, "Accton 取得新一代 800G 交換器大單",
     "2026-08-01", '["https://example.com/accton-order"]'),
    (1, "2026-07-28", "fixture", 0, "無重大更新", None, "[]"),
    (2, "2026-08-04", "fixture", 1, "台積電上修資本支出展望",
     "2026-08-03", '["https://example.com/tsmc-capex"]'),
]


def build_fixture(directory: "str | Path") -> Path:
    """Create the fixture DB inside `directory` and return its path."""
    path = Path(directory) / "isr_fixture.sqlite3"
    if path.exists():
        path.unlink()
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")  # mirror the real DB
        conn.executescript("""
            CREATE TABLE companies (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                company_name TEXT NOT NULL,
                country TEXT NOT NULL DEFAULT '',
                industry TEXT NOT NULL DEFAULT '',
                tier TEXT NOT NULL DEFAULT '',
                research_status TEXT NOT NULL DEFAULT 'pending_deep_research',
                maintenance_status TEXT NOT NULL DEFAULT 'not_due',
                source TEXT NOT NULL DEFAULT 'new',
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT,
                last_error_class TEXT,
                last_error TEXT,
                deep_researched_at TEXT,
                last_screened_at TEXT,
                last_maintained_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE company_research (
                company_id      INTEGER PRIMARY KEY
                                REFERENCES companies(id) ON DELETE CASCADE,
                ticker          TEXT,
                company_name    TEXT NOT NULL,
                record_json     TEXT NOT NULL,
                complete        INTEGER NOT NULL DEFAULT 0,
                source          TEXT NOT NULL DEFAULT 'apply',
                source_batch_id INTEGER,
                updated_at      TEXT NOT NULL
            );
            CREATE TABLE maintenance_screenings (
                id INTEGER PRIMARY KEY,
                company_id INTEGER NOT NULL REFERENCES companies(id)
                    ON DELETE CASCADE,
                week_start TEXT NOT NULL,
                provider TEXT NOT NULL,
                needs_update INTEGER NOT NULL,
                reason TEXT NOT NULL DEFAULT '',
                evidence_date TEXT,
                created_at TEXT NOT NULL,
                source_urls_json TEXT NOT NULL DEFAULT '[]',
                UNIQUE(company_id, week_start)
            );
        """)
        for cid, ticker, name, country, industry, tier, status, record in _COMPANIES:
            conn.execute(
                """INSERT INTO companies (id, ticker, company_name, country,
                       industry, tier, research_status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, '2026-08-01', '2026-08-10')""",
                (cid, ticker, name, country, industry, tier, status))
            if record is not None:
                conn.execute(
                    """INSERT INTO company_research (company_id, ticker,
                           company_name, record_json, complete, updated_at)
                       VALUES (?, ?, ?, ?, 1, '2026-08-10')""",
                    (cid, ticker, name, record))
        for cid, week, provider, needs, reason, ev_date, urls in _SCREENINGS:
            conn.execute(
                """INSERT INTO maintenance_screenings (company_id, week_start,
                       provider, needs_update, reason, evidence_date,
                       created_at, source_urls_json)
                   VALUES (?, ?, ?, ?, ?, ?, '2026-08-10', ?)""",
                (cid, week, provider, needs, reason, ev_date, urls))
        conn.commit()
    finally:
        conn.close()
    return path
