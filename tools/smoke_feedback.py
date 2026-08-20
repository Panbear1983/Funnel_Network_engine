"""Repo-level regression for local map feedback storage.

The in-app feedback prompt was removed (it was write-only ceremony for a
single user), but the MapFeedback table and repo helpers are kept so stored
rows stay readable and scripts can still record feedback. This smoke pins
that storage contract: save, update-not-duplicate, note cap, isolation from
evidence.

Run from a source tree that does not contain a .env:
`python tools/smoke_feedback.py`
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback

os.environ["FUNNEL_HOME"] = tempfile.mkdtemp(prefix="funnel_feedback_")
# Keep the offline test deterministic: never auto-probe a real ISR sibling DB.
os.environ.setdefault("FUNNEL_ISR_DB", "")

from funnel.db import repo  # noqa: E402
from funnel.db.engine import init_db  # noqa: E402
from funnel.db.models import EvidenceItem, MapFeedback, ResearchRun  # noqa: E402


def run() -> None:
    init_db()
    request = repo.create_request("Feedback storage")
    run_row = repo.add(ResearchRun(request_id=request.id, tier="professional",
                                   model="m", status="complete"))

    # Save, then re-save: one row per run, updated in place.
    first = repo.save_map_feedback(request.id, run_row.id, useful=False,
                                   note="first impression")
    second = repo.save_map_feedback(request.id, run_row.id, useful=True,
                                    note="Clear first map.")
    assert first.id == second.id, "feedback duplicated instead of updated"
    stored = repo.get_map_feedback(run_row.id)
    assert stored is not None and stored.useful is True
    assert stored.note == "Clear first map."

    # Notes are defensively capped at 500 chars even from scripts.
    long = repo.save_map_feedback(request.id, run_row.id, useful=True,
                                  note="x" * 2000)
    assert len(long.note) <= 500, len(long.note)

    # Feedback is product telemetry, never research evidence.
    assert repo.count_for_request(EvidenceItem, request.id) == 0
    assert isinstance(stored, MapFeedback)

    # The UI no longer mounts a feedback form (prompt removed on purpose).
    import funnel.ui.panels.flow as flow_panel
    src_ok = not hasattr(flow_panel.FlowPanel, "_save_feedback")
    assert src_ok, "feedback prompt resurfaced in FlowPanel"

    print("FEEDBACK_OK repo save/update/cap + evidence isolation + prompt removed")


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
