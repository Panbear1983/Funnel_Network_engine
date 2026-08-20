"""Shared background-job runner for panels and screens.

Every long operation in the app used to hand-roll the same triad: a
@work(thread=True) method + a progress closure marshalled with
call_from_thread + a try/except routing to a *_failed handler. This mixin is
that triad, once. Hosts may define `_job_progress(line)` and
`_job_failed(message)`; jobs are functions of one argument (a progress
callable) returning the result handed to `on_done`.

NOTE: never override an inherited Textual message handler with a super() call
in hosts of this mixin — see the deadlock note in funnel/ui/crud.py.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from textual import work

Job = Callable[[Callable[[str], None]], Any]


class JobRunnerMixin:
    """Host classes must be Textual DOM nodes (Widget/Screen)."""

    @work(thread=True, exclusive=True)
    def _run_job(self, fn: Job, on_done: Callable[[Any], None],
                 on_error: Optional[Callable[[str], None]] = None) -> None:
        def progress(line: str) -> None:
            handler = getattr(self, "_job_progress", None)
            if handler is not None:
                self.app.call_from_thread(handler, line)

        try:
            result = fn(progress)
        except Exception as exc:  # jobs must never take the app down
            message = f"{type(exc).__name__}: {exc}"
            handler = on_error or getattr(self, "_job_failed", None)
            if handler is not None:
                self.app.call_from_thread(handler, message)
            else:
                self.app.call_from_thread(
                    self.app.notify, f"Failed: {message}", severity="error")
            return
        self.app.call_from_thread(on_done, result)
