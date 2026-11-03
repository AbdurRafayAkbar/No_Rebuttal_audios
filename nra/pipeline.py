"""Daily run: fetch one day of calls, parse bot logs, detect, judge, store."""
from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import botlog, detect, llm, source, store
from .config import Campaign, Settings

log = logging.getLogger("nra")


def day_range_utc(day: date, tz_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time.min, tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tz)
    to_utc = lambda d: d.astimezone(timezone.utc).replace(tzinfo=None)  # noqa: E731
    return to_utc(start), to_utc(end)


def _analyse(settings: Settings, campaign: Campaign, call: source.CallRow, rebuttals):
    if not call.log_url.startswith("http"):
        return call, None, [], "no_log", None
    try:
        text = botlog.fetch_log(call.log_url, settings.log_url_prefix)
        parsed = botlog.parse_log(text)
        cands = detect.detect(campaign.code, parsed, rebuttals)
        return call, parsed, cands, "done", None
    except Exception as exc:  # noqa: BLE001 - one bad call must not stop the run
        return call, None, [], "failed", f"{type(exc).__name__}: {exc}"[:300]


def run(settings: Settings, campaign: Campaign, day: date, limit: int | None = None,
        workers: int | None = None, force: bool = False, use_llm: bool = True) -> dict:
    conn = store.open_db(settings.db_path)
    run_date = day.isoformat()
    start_utc, end_utc = day_range_utc(day, settings.run_tz)
    calls = source.fetch_calls(settings, campaign.source_values, start_utc, end_utc)
    total = len(calls)
    if not force:
        skip = store.done_ids(conn, [c.id for c in calls])
        calls = [c for c in calls if c.id not in skip]
    if limit:
        calls = calls[:limit]
    log.info("%s %s: %d calls in day, %d to process", campaign.code, run_date, total, len(calls))

    run_id = conn.execute("INSERT INTO processing_runs(run_date, campaign, calls_total) VALUES (?,?,?)",
                          (run_date, campaign.code, len(calls))).lastrowid
    conn.commit()
    rebuttals = detect.load_rebuttals(campaign.clips_dir / "clips_manifest.csv")
    rebuttal_names = [r.name for r in rebuttals]
    judge_on = use_llm and settings.llm_enabled
    counts = {"done": 0, "no_log": 0, "failed": 0, "candidates": 0, "dismissed": 0}

    with ThreadPoolExecutor(max_workers=workers or settings.workers) as pool:
        futures = [pool.submit(_analyse, settings, campaign, c, rebuttals) for c in calls]
        for n, fut in enumerate(as_completed(futures), 1):
            call, parsed, cands, status, error = fut.result()
            counts[status] += 1
            conn.execute(
                "INSERT OR REPLACE INTO processed_audios(audio_id, run_date, campaign, start_time, audio_url,"
                " disposition, turns, status, error) VALUES (?,?,?,?,?,?,?,?,?)",
                (call.id, run_date, campaign.code, str(call.start_time), call.audio_url, call.disposition,
                 len(parsed.turns) if parsed else None, status, error))
            conn.execute("DELETE FROM candidates WHERE audio_id=?", (call.id,))
            for c in cands:
                verdict, cstatus = None, "flagged"
                if judge_on:
                    verdict = store.cached_verdict(conn, llm.PROMPT_VERSION, c.question_key, c.response)
                    if verdict is None:
                        try:
                            verdict = llm.judge(settings, c.customer_text, c.bot_state, c.response, rebuttal_names)
                            store.save_verdict(conn, llm.PROMPT_VERSION, c.question_key, c.response, verdict)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("LLM judge failed for call %s: %s", call.id, type(exc).__name__)
                    if verdict and (not verdict["is_question"] or verdict["answers_question"]):
                        cstatus = "dismissed_by_llm"
                        counts["dismissed"] += 1
                conn.execute(
                    "INSERT INTO candidates(audio_id, turn_no, run_date, campaign, bot_state, bot_audio,"
                    " customer_text, intent, q_rule, response, r_rule, confidence, speech_start, question_key,"
                    " suggested_rebuttal, played, repeats, llm_verdict, status)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (call.id, c.turn_no, run_date, campaign.code, c.bot_state, c.bot_audio, c.customer_text,
                     c.intent, c.q_rule, c.response, c.r_rule, c.confidence, c.speech_start, c.question_key,
                     c.suggested_rebuttal, int(c.played), c.repeats,
                     json.dumps(verdict) if verdict else None, cstatus))
                counts["candidates"] += 1
            if n % 200 == 0:
                conn.commit()
                log.info("  %d/%d calls", n, len(calls))
    conn.execute(
        "UPDATE processing_runs SET finished_at=datetime('now'), calls_done=?, calls_failed=?, candidates=?"
        " WHERE id=?", (counts["done"], counts["failed"], counts["candidates"], run_id))
    conn.commit()
    counts["llm"] = judge_on
    return counts
