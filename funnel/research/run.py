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
from funnel.db.models import ResearchRunOutcome
from funnel.db.engine import init_db
from funnel.services import build as flow
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
    if args.request or args.topic:
        # Reap only when about to run — a bare --list must not touch runs the
        # TUI may have in flight.
        reaped = repo.reap_running_runs()
        if reaped:
            print(f"· {reaped} interrupted run(s) marked failed (orphaned by a previous quit).")
    if args.list or not (args.request or args.topic):
        for r in repo.list_requests():
            print(r.id, "|", r.title or r.industry_of_interest or "(untitled)")
        if not (args.request or args.topic):
            return 0

    progress = lambda m: print("·", m)  # noqa: E731
    from funnel.research.llm import UsageTracker
    usage = UsageTracker()  # cost visibility: summary gains "· MmSSs · N LLM calls"
    if args.topic:
        request_id = repo.create_request(args.topic).id
        print(f"REQUEST {request_id} '{args.topic}'")
        run = flow.run_research(request_id, args.tier, progress=progress, usage=usage)
    else:
        request_id = args.request
        run = run_pipeline(request_id, args.tier, progress=progress,
                           dry_run=args.dry_run, usage=usage)
    print(f"RUN {run.id} [{run.status}] {run.summary}")

    if args.dry_run:
        return 0
    if run.status not in {
        ResearchRunOutcome.COMPLETE.value,
        ResearchRunOutcome.PARTIAL.value,
    }:
        # Non-buildable runs must not be committed (live run 2 committed a half-run).
        print(f"NOT COMMITTED — run status is '{run.status}'. Fix and re-run.")
        if run.status == ResearchRunOutcome.NEEDS_CREDENTIALS.value:
            from funnel import config
            hint = config.other_provider_hint()
            print(f"  set {config.llm_key_name()} in .env at the repo root "
                  f"(copy .env.example if needed)"
                  + (f" {hint}" if hint else ""))
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
