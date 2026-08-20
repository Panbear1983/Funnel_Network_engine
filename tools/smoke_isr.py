"""Offline test for the ISR knowledge connector (no network, no real ISR DB).
Builds a fixture DB covering the real data quirks, then exercises every
lookup/normalization path plus the disabled/graceful-degradation paths.
Run: `python tools/smoke_isr.py`."""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

_TMP = tempfile.mkdtemp(prefix="funnel_isr_")
os.environ.setdefault("FUNNEL_HOME", _TMP)

from isr_fixture import build_fixture  # noqa: E402

FIXTURE = build_fixture(_TMP)
os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)

from funnel import config  # noqa: E402
from funnel.knowledge import isr as isr_mod  # noqa: E402
from funnel.knowledge.isr import (  # noqa: E402
    IsrKnowledge, canonical_tier, get_isr, main, split_ticker, yahoo_ticker,
)


def run() -> None:
    # --- pure normalization helpers -------------------------------------
    assert split_ticker("2345.TW") == ("2345", "TW")
    assert split_ticker("BRK.B") == ("BRK.B", "")     # share class, not exchange
    assert split_ticker("NVDA") == ("NVDA", "")
    assert yahoo_ticker("600584.SH") == "600584.SS"   # .SH -> Yahoo .SS
    assert yahoo_ticker("2330", "Taiwan") == "2330.TW"
    assert yahoo_ticker("2330", "台灣") == "2330.TW"
    assert yahoo_ticker("2330", "Japan") == "2330"
    assert canonical_tier("龍頭股。") == "leader"
    assert canonical_tier("二線龍頭") == "second_leader"
    assert canonical_tier("龍頭股，全球第二大電子代工巨頭") == "leader"
    assert canonical_tier("") == "unknown"

    # --- loading ----------------------------------------------------------
    assert config.isr_db_path() == FIXTURE
    isr = IsrKnowledge.open_default()
    assert isr is not None
    stats = isr.stats()
    assert stats["companies"] == 8, stats            # 9 rows minus 1 excluded
    assert stats["with_ticker"] == 7, stats
    assert stats["dossiers"] == 4, stats
    assert stats["screenings"] == 3, stats
    assert stats["dossier_updated_max"] == "2026-08-10", stats

    # --- ticker lookups ----------------------------------------------------
    accton = isr.lookup("2345.TW")
    assert accton and accton.name == "Accton", accton
    assert isr.lookup("2345") is accton              # base form
    assert isr.lookup("2345.tw") is accton           # case-insensitive
    tsmc = isr.lookup("2330")
    assert tsmc and tsmc.name == "台積電", tsmc
    assert tsmc.yahoo_ticker == "2330.TW"
    assert isr.lookup("2330.TW") is tsmc             # yahoo form finds bare row
    jcet = isr.lookup("600584.SS")                   # yahoo .SS finds .SH row
    assert jcet and jcet.ticker == "600584.SH", jcet
    assert isr.lookup("NVDA").country == "USA"

    # --- name lookups -------------------------------------------------------
    assert isr.lookup("Accton") is accton
    assert isr.lookup("accton") is accton
    assert isr.lookup("台積電") is tsmc               # CJK intact
    assert isr.lookup("台積電 (2330)") is tsmc        # raw name form
    assert isr.lookup("Mystery Co").tier == "leader"  # dirty tier canonicalized
    assert isr.lookup("Excluded Corp") is None        # excluded rows dropped
    assert isr.lookup("Review Co") is not None        # kept for lookup
    assert isr.lookup("No Such Company") is None
    assert isr.lookup("") is None

    # Non-str inputs from unvalidated LLM payloads must not crash — a bad
    # country/name hint just fails to disambiguate. (Regression: a list/int
    # country used to raise AttributeError out of the pipeline grounding and
    # abort the whole run.)
    assert isr.lookup("2330", country=["USA"]).name == "台積電"  # exact still wins
    assert isr.lookup("2345", country=123) is accton
    assert isr.lookup(["not", "a", "str"]) is None

    # --- lookup ambiguity + country disambiguation ---------------------------
    tw = isr.lookup("1234", country="Taiwan")
    jp = isr.lookup("1234", country="Japan")
    assert tw and tw.country == "Taiwan", tw
    assert jp and jp.country == "Japan", jp
    assert tw is not jp
    assert isr.lookup("1234") is None                 # ambiguous base, no hint
    assert isr.lookup("1234.TW") is tw                # full ticker unambiguous
    assert isr.lookup("1234", country="Freedonia") is None  # hint matches neither

    # --- candidates ----------------------------------------------------------
    hits = isr.candidates(["networking", "switches"])
    assert hits and hits[0] is accton, hits           # dossier prose matched
    names = {c.name for c in isr.candidates(["半導體"])}
    assert "台積電" in names, names
    assert "Review Co" not in names                   # needs_ticker_review excluded
    assert isr.candidates([]) == []
    # ASCII terms match whole words only; CJK matches by substring.
    assert accton in isr.candidates(["networking"])
    assert accton not in isr.candidates(["net"])      # 'net' != word in 'networking'
    assert accton in isr.candidates(["網路"])          # CJK substring hits industry

    # --- theme browsing (offline seeding) ------------------------------------
    inds = dict(isr.industries())
    assert inds.get("半導體業") == 3, inds             # 2330 + NVDA + JCET
    assert inds.get("機械") == 2, inds                 # the two dual-listed rows
    theme = isr.by_theme(industry="半導體業")
    assert {c.name for c in theme} == {"台積電", "NVIDIA", "JCET"}, [c.name for c in theme]
    assert theme[0].tier == "leader"                  # leaders ranked first
    assert len(isr.by_theme(tier="potential", industry="機械")) == 2
    assert isr.by_theme(industry="does-not-exist") == []
    assert all(c.research_status != "needs_ticker_review"
               for c in isr.by_theme())               # review rows excluded

    # --- dossier / screenings -------------------------------------------------
    dossier = isr.dossier("2330")
    assert dossier and "晶圓代工" in dossier["Core Business"], dossier
    assert isr.dossier("Mystery Co") is None
    screens = isr.screenings(accton)                  # needs_update only
    assert len(screens) == 1 and screens[0].needs_update, screens
    assert screens[0].source_urls == ["https://example.com/accton-order"]
    all_screens = isr.screenings("2345.TW", needs_update_only=False)
    assert len(all_screens) == 2, all_screens
    assert all_screens[0].week_start == "2026-08-04"  # newest first
    assert isr.screenings(accton, since="2026-08-05") == []
    assert isr.screenings("NVDA") == []

    # --- immutable=1 connect fallback (production WAL path) --------------------
    # Force the mode=ro open to fail so the immutable=1 branch of _connect runs;
    # the real ISR DB hits this when a live writer holds the -wal.
    import sqlite3
    real_connect = sqlite3.connect
    seen_uris: list[str] = []

    def _failing_connect(database, *a, **kw):
        seen_uris.append(str(database))
        if "immutable=1" not in str(database):
            raise sqlite3.OperationalError("unable to open database file")
        return real_connect(database, *a, **kw)

    sqlite3.connect = _failing_connect
    try:
        via_fallback = IsrKnowledge(FIXTURE)
    finally:
        sqlite3.connect = real_connect
    assert via_fallback.lookup("2330") is not None    # loaded via immutable read
    assert any("immutable=1" not in u for u in seen_uris)   # tried mode=ro first
    assert any("immutable=1" in u for u in seen_uris)       # then fell back

    # --- cached accessor + WAL-aware invalidation ------------------------------
    assert get_isr() is get_isr()                     # same snapshot reused
    first = get_isr()
    st = FIXTURE.stat()
    os.utime(FIXTURE, (st.st_atime, st.st_mtime + 10))  # a new ISR write
    assert get_isr() is not first                     # cache invalidated by mtime

    # --- CLI ---------------------------------------------------------------------
    assert main(["--stats"]) == 0
    assert main(["--lookup", "2330"]) == 0
    assert main(["--lookup", "definitely-not-a-company"]) == 1
    assert main(["--candidates", "networking,switches"]) == 0
    assert main(["--screenings", "2345.TW", "--since", "2026-08-01"]) == 0

    # --- disabled / degradation paths ---------------------------------------------
    os.environ["FUNNEL_ISR_DB"] = ""
    isr_mod._cache = None
    assert config.isr_db_path() is None
    assert IsrKnowledge.open_default() is None
    assert get_isr() is None
    assert main(["--stats"]) == 2                     # clear disabled exit code

    os.environ["FUNNEL_ISR_DB"] = "/nonexistent/isr.sqlite3"
    assert config.isr_db_path() is None
    assert IsrKnowledge.open_default() is None

    os.environ["FUNNEL_ISR_DB"] = str(FIXTURE)        # restore for reruns
    print("ISR_OK", {k: stats[k] for k in ("companies", "dossiers", "screenings")})


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
