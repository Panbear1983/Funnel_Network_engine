"""Open exported charts in the OS viewer (macOS Preview). One place instead of
three copies — the result should be seen, not hunted for in a hidden folder."""

from __future__ import annotations

import subprocess
import sys


def open_chart(charts: dict, *, headless: bool = False) -> None:
    """Pop the professional-resolution chart (or any) open on macOS. Silent
    no-op when headless, non-darwin, or nothing was exported."""
    if headless or sys.platform != "darwin":
        return
    chart = charts.get("professional") or next(iter(charts.values()), None)
    if not chart:
        return
    try:
        subprocess.Popen(["open", str(chart)])
    except OSError:
        pass
