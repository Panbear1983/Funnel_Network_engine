"""Focused offline regression for optional, local post-map feedback.

Run from a source tree that does not contain a .env:
`python tools/smoke_feedback.py`
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import traceback

os.environ["FUNNEL_HOME"] = tempfile.mkdtemp(prefix="funnel_feedback_")
# Keep the offline test deterministic: never auto-probe a real ISR sibling DB.
os.environ.setdefault("FUNNEL_ISR_DB", "")

from textual.widgets import Button, Input, Select  # noqa: E402

from funnel.db import repo  # noqa: E402
from funnel.db.models import (  # noqa: E402
    EvidenceItem,
    MapFeedback,
    ResearchRun,
    ResearchRunOutcome,
)
from funnel.ui.app import FunnelApp  # noqa: E402
from funnel.ui.panels.flow import FlowPanel  # noqa: E402
from funnel.ui.sidebar import Sidebar  # noqa: E402


async def run() -> None:
    app = FunnelApp()
    async with app.run_test(size=(140, 44)) as pilot:
        await pilot.pause()
        request = repo.create_request("Offline feedback fixture")
        run = repo.add(ResearchRun(
            request_id=request.id,
            status=ResearchRunOutcome.COMPLETE.value,
            summary="offline fixture complete",
        ))
        app.query_one(Sidebar).reload(select_id=request.id)
        await pilot.pause()

        panel = app.query_one(FlowPanel)
        feedback = panel.query_one("#flow-feedback")
        assert not feedback.display, "feedback appeared before a map completed"
        assert repo.get_map_feedback(run.id) is None

        result = {
            "counts": {"process_node": 1, "company": 2, "edge": 1},
            "charts": {
                "high": "/tmp/high.png",
                "professional": "/tmp/professional.png",
                "deep": "/tmp/deep.png",
            },
        }
        panel._build_done(result)
        await pilot.pause()
        assert feedback.display, "successful map completion did not offer feedback"
        assert "4 new" in str(panel.query_one("#flow-result").render())
        assert repo.get_map_feedback(run.id) is None, "feedback was not opt-in"

        # Skip is non-blocking and creates no local row.
        await pilot.click("#flow-feedback-skip")
        await pilot.pause()
        assert not feedback.display
        assert repo.get_map_feedback(run.id) is None

        # Saving requires an explicit usefulness choice and stores the note only
        # in the dedicated feedback table.
        panel._show_feedback()
        panel.query_one("#flow-feedback-note", Input).value = "Clear first map."
        panel.query_one("#flow-feedback-save", Button).press()
        await pilot.pause()
        assert feedback.display, "missing choice should leave the optional card open"
        assert repo.get_map_feedback(run.id) is None

        panel.query_one("#flow-feedback-useful", Select).value = "yes"
        panel.query_one("#flow-feedback-save", Button).press()
        await pilot.pause()
        saved = repo.get_map_feedback(run.id)
        assert saved is not None and saved.useful is True
        assert saved.note == "Clear first map."
        assert not feedback.display
        assert repo.count_for_request(MapFeedback, request.id) == 1
        assert repo.count_for_request(EvidenceItem, request.id) == 0

        # Repeat saves update the run's single row and defensively cap notes.
        updated = repo.save_map_feedback(
            request.id, run.id, useful=False, note=" x" * 400)
        assert updated.useful is False
        assert len(updated.note) == 500
        assert repo.count_for_request(MapFeedback, request.id) == 1

        # Feedback follows the request's existing local cascade-delete behavior.
        repo.delete_request(request.id)
        assert repo.count_for_request(MapFeedback, request.id) == 0

        print("FEEDBACK_OK optional + local-only + separate from evidence")


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
