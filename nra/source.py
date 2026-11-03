"""Read-only access to the existing call-data MySQL server (D17, D23).

Only SELECT statements run here. Every session is forced READ ONLY and the
setting is verified before any query, so a write fails even if code is wrong.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pymysql

from .config import Settings


@dataclass(frozen=True)
class CallRow:
    id: int
    start_time: datetime  # UTC
    end_time: datetime | None
    campaign_id: str
    audio_url: str
    log_url: str
    disposition: str


def connect(settings: Settings) -> pymysql.connections.Connection:
    conn = pymysql.connect(
        host=settings.db_host,
        port=settings.db_port,
        user=settings.db_user,
        password=settings.db_password,
        database=settings.db_name,
        connect_timeout=15,
        read_timeout=300,
        autocommit=True,
        charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")
        cur.execute("SELECT @@session.transaction_read_only")
        (read_only,) = cur.fetchone()
    if int(read_only) != 1:
        conn.close()
        raise RuntimeError("could not force the source DB session to READ ONLY; aborting")
    return conn


# Only the columns we need. caller_id, lead_id, user_id are never selected.
_CALLS_SQL = """
SELECT id, start_time, end_time, campaign_id, audio_url, log_url, disposition
FROM call_records
WHERE campaign_id IN ({placeholders})
  AND start_time >= %s AND start_time < %s
ORDER BY id
"""


def fetch_calls(settings: Settings, campaign_values: tuple[str, ...],
                start_utc: datetime, end_utc: datetime) -> list[CallRow]:
    sql = _CALLS_SQL.format(placeholders=", ".join(["%s"] * len(campaign_values)))
    conn = connect(settings)
    try:
        with conn.cursor() as cur:
            cur.execute(sql, (*campaign_values, start_utc, end_utc))
            rows = cur.fetchall()
    finally:
        conn.close()
    return [
        CallRow(int(r[0]), r[1], r[2], r[3] or "", r[4] or "", r[5] or "", r[6] or "")
        for r in rows
    ]
