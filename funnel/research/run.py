"""Headless research runner.

One-topic golden path (same as the Map tab, no TUI):
    python -m funnel.research.run --topic "Chip manufacturing" --tier professional --build

Or against an existing request:
    python -m funnel.research.run --request <id> --tier professional [--commit]
"""

from __future__ import annotations

import argparse
import sys

from funnel.db import repo
from funnel.db.engine import init_db
from funnel.research import flow
from funnel.research.ingest import commit_proposals
from funnel.research.pipeline import run_pipeline


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="funnel.research.run")
    parser.add_argument("--topic", help="run the full flow from a bare topic "
                                        "(creates the request + auto-brief)")
    parser.add_argument("--request", help="ResearchRequest id")
    parser.add_argument("--tier", default="professional",
                        choices=["high_level", "professional", "deep_research"])
    parser.add_argument("--dry-run", action="store_true", help="process stack only, no scraping")
    parser.add_argument("--commit", action="store_true", help="materialize non-rejected proposals")
    parser.add_argument("--build", action="store_true",
                        help="commit AND export the 3 tree charts + report (flow finalize)")
    parser.add_argument("--open", action="store_true",
                        help="after --build, open the professional tree chart (macOS)")
    parser.add_argument("--list", action="store_true", help="list requests and exit")
    args = parser.parse_args(argv)

    init_db()
    if args.list or not (args.request or args.topic):
        for r in repo.list_requests():
            print(r.id, "|", r.title or r.industry_of_interest or "(untitled)")
        if not (args.request or args.topic):
            return 0

    progress = lambda m: print("·", m)  # noqa: E731
    if args.topic:
        request_id = repo.create_request(args.topic).id
        print(f"REQUEST {request_id} '{args.topic}'")
        run = flow.run_research(request_id, args.tier, progress=progress)
    else:
        request_id = args.request
        run = run_pipeline(request_id, args.tier, progress=progress, dry_run=args.dry_run)
    print(f"RUN {run.id} [{run.status}] {run.summary}")

    if args.dry_run:
        return 0
    if run.status != "complete" and not run.status.startswith("complete"):
        # A failed run must not be committed (live run 2 committed a half-run).
        print(f"NOT COMMITTED — run status is '{run.status}'. Fix and re-run.")
        return 1
    if args.build:
        result = flow.finalize(request_id, run.id, progress=progress)
        print("BUILT", result["counts"])
        for resolution, path in result["charts"].items():
            print(f"  tree[{resolution}] {path}")
        print(f"  report {result['report']}")
        if args.open and sys.platform == "darwin":
            import subprocess
            chart = result["charts"].get("professional")
            if chart:
                subprocess.Popen(["open", chart])
    elif args.commit:
        print("COMMITTED", commit_proposals(request_id, run_id=run.id))
    return 0


if __name__ == "__main__":
    sys.exit(main())
