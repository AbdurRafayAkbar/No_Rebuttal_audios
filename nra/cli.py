"""Command line: `python -m nra run|report [--date YYYY-MM-DD]`."""
from __future__ import annotations

import argparse
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from . import pipeline, report, store
from .config import load_settings


def _yesterday(tz: str) -> date:
    return datetime.now(ZoneInfo(tz)).date() - timedelta(days=1)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="nra", description="Find customer questions the bot did not answer.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "report"):
        p = sub.add_parser(name)
        p.add_argument("--date", type=date.fromisoformat, help="call date in RUN_TZ (default: yesterday)")
        p.add_argument("--campaign", default="MEDICARE")
    r = sub.choices["run"]
    r.add_argument("--limit", type=int, help="process at most N calls (for testing)")
    r.add_argument("--workers", type=int)
    r.add_argument("--force", action="store_true", help="re-process calls already done")
    r.add_argument("--no-llm", action="store_true", help="skip the LLM judge even if configured")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings()
    campaign = settings.campaigns[args.campaign.upper()]
    day = args.date or _yesterday(settings.run_tz)

    if args.cmd == "run":
        counts = pipeline.run(settings, campaign, day, limit=args.limit, workers=args.workers,
                              force=args.force, use_llm=not args.no_llm)
        print(f"processed: {counts['done']} done, {counts['no_log']} no log, {counts['failed']} failed; "
              f"{counts['candidates']} candidates, {counts['dismissed']} dismissed by LLM "
              f"(LLM {'on' if counts['llm'] else 'off'})")
    conn = store.open_db(settings.db_path)
    html_path, csv_path, n = report.write_reports(conn, settings.data_dir / "reports", day.isoformat(), campaign.code)
    print(f"report: {n} flagged -> {html_path}\n        {csv_path}")
    return 0
