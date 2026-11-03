"""App database (SQLite file under DATA_DIR). Never on the call-data server (D23)."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE IF NOT EXISTS processing_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_date TEXT NOT NULL,
    campaign TEXT NOT NULL,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    calls_total INTEGER DEFAULT 0,
    calls_done INTEGER DEFAULT 0,
    calls_failed INTEGER DEFAULT 0,
    candidates INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS processed_audios (
    audio_id INTEGER PRIMARY KEY,          -- call_records.id
    run_date TEXT NOT NULL,
    campaign TEXT NOT NULL,
    start_time TEXT,
    audio_url TEXT,
    disposition TEXT,
    turns INTEGER,
    status TEXT NOT NULL,                  -- done | no_log | failed
    error TEXT,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_processed_date ON processed_audios(run_date, campaign);
CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audio_id INTEGER NOT NULL REFERENCES processed_audios(audio_id),
    turn_no INTEGER NOT NULL,
    run_date TEXT NOT NULL,
    campaign TEXT NOT NULL,
    bot_state TEXT, bot_audio TEXT, customer_text TEXT, intent TEXT,
    q_rule TEXT, response TEXT, r_rule TEXT, confidence REAL,
    speech_start REAL, question_key TEXT, suggested_rebuttal TEXT,
    played INTEGER, repeats INTEGER,
    llm_verdict TEXT,                      -- JSON or NULL
    status TEXT NOT NULL DEFAULT 'flagged',  -- flagged | dismissed_by_llm
    UNIQUE (audio_id, turn_no)
);
CREATE INDEX IF NOT EXISTS ix_candidates_date ON candidates(run_date, campaign);
CREATE TABLE IF NOT EXISTS llm_verdicts (
    prompt_version TEXT NOT NULL,
    question_key TEXT NOT NULL,
    response TEXT NOT NULL,
    verdict TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (prompt_version, question_key, response)
);
-- v2: reviewer decisions. Kept apart from candidates so a re-run does not wipe them.
CREATE TABLE IF NOT EXISTS reviews (
    audio_id INTEGER NOT NULL,
    turn_no INTEGER NOT NULL,
    decision TEXT NOT NULL,                -- approved (real miss) | rejected (false flag)
    note TEXT,
    reviewed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (audio_id, turn_no)
);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(_SCHEMA)
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        conn.commit()
    return conn


def done_ids(conn: sqlite3.Connection, ids: list[int]) -> set[int]:
    out: set[int] = set()
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        q = f"SELECT audio_id FROM processed_audios WHERE status='done' AND audio_id IN ({','.join('?' * len(chunk))})"
        out.update(r[0] for r in conn.execute(q, chunk))
    return out


def cached_verdict(conn, prompt_version: str, key: str, response: str) -> dict | None:
    row = conn.execute(
        "SELECT verdict FROM llm_verdicts WHERE prompt_version=? AND question_key=? AND response=?",
        (prompt_version, key, response)).fetchone()
    return json.loads(row[0]) if row else None


def save_verdict(conn, prompt_version: str, key: str, response: str, verdict: dict) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO llm_verdicts(prompt_version, question_key, response, verdict) VALUES (?,?,?,?)",
        (prompt_version, key, response, json.dumps(verdict)))
