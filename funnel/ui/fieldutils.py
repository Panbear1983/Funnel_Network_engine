"""Small helpers shared by the entity forms."""

from __future__ import annotations

from typing import Optional

from textual.widgets import Select


def split_list(value: str) -> list[str]:
    return [part.strip() for part in (value or "").split(",") if part.strip()]


def join_list(items) -> str:
    return ", ".join(items or [])


def parse_float(value: str, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_optional_float(value: str) -> Optional[float]:
    value = (value or "").strip()
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def truncate(text: Optional[str], length: int = 40) -> str:
    text = text or ""
    return text if len(text) <= length else text[: length - 1] + "…"


def build_select(
    options: list[tuple[str, str]],
    current: Optional[str] = None,
    *,
    id: str,
    allow_blank: bool = True,
    default: Optional[str] = None,
) -> Select:
    """Construct a Select, preselecting `current` (or `default`) when valid.

    Falls back to blank (or the first option, when blanks are not allowed).
    """
    valid = {value for _, value in options}
    chosen = current if current in valid else (default if default in valid else None)
    if chosen is not None:
        return Select(options, value=chosen, id=id, allow_blank=allow_blank)
    if not allow_blank and options:
        return Select(options, value=options[0][1], id=id, allow_blank=False)
    return Select(options, id=id, allow_blank=True)
