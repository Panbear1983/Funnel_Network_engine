"""Deterministic sanity screen for company proposals.

Conservative by design: it only auto-rejects things that are plainly not
companies (countries, blocs, collectives, placeholder phrases) so the human
review gate never has to burn a click on obvious junk. Real-but-uncertain
companies are never rejected here — confidence is the reviewer's signal, not
this filter's. Rejections are staged (status="rejected") with the reason in
the rationale, and remain restorable in the Map tab.
"""

from __future__ import annotations

import re
from typing import Optional

# Countries / regions / blocs that models sometimes emit as "companies".
# Lowercased exact-match set — intentionally not exhaustive; only names that
# have actually shown up or are likely (major economies + blocs + regions).
_PLACES = {
    "china", "usa", "united states", "u.s.", "us", "america", "japan",
    "south korea", "korea", "taiwan", "chinese taipei", "germany", "france",
    "netherlands", "india", "russia", "brazil", "canada", "mexico",
    "united kingdom", "uk", "italy", "spain", "singapore", "switzerland",
    "australia", "vietnam", "thailand", "malaysia", "indonesia",
    "europe", "eu", "european union", "asia", "asia-pacific", "apac",
    "north america", "south america", "latin america", "africa",
    "middle east", "global", "worldwide", "domestic", "international",
}

_JUNK_PATTERNS = [
    (re.compile(r"\(collective", re.I), "collective, not a company"),
    (re.compile(r"\b(industry|sector|market)\)$", re.I), "an industry, not a company"),
    (re.compile(r"^various\b", re.I), "placeholder ('various …')"),
    (re.compile(r"^multiple\b", re.I), "placeholder ('multiple …')"),
    (re.compile(r"^other\b", re.I), "placeholder ('other …')"),
    (re.compile(r"\bet al\b", re.I), "placeholder ('et al')"),
    (re.compile(r"^the .+ (industry|sector|market)$", re.I), "a sector, not a company"),
    (re.compile(r"^(unknown|n/?a|tbd|none)$", re.I), "placeholder name"),
]

_MAX_NAME_LEN = 70


def screen_company(payload: dict) -> Optional[str]:
    """Return a rejection reason when the proposed company is plainly not a
    company; None when it should stand for human review."""
    name = str(payload.get("name") or "").strip()
    if not name:
        return "empty name"
    if len(name) > _MAX_NAME_LEN:
        return f"name too long ({len(name)} chars)"
    if name.lower().strip(".") in _PLACES:
        return "a country/region, not a company"
    for pattern, reason in _JUNK_PATTERNS:
        if pattern.search(name):
            return reason
    return None
