"""Fetch and parse the production bot's per-call log (sd-logs bucket).

The log already holds the turn structure: which clip the bot played, what the
bot's STT heard, the intent it chose and the next action. We parse only the
lines we need. The log also holds the caller number; we never keep the raw log.
"""
from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

_LINE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]\s+\[(\w+)\]\s+(.*)$")
_TURN = re.compile(r"\bTurn (\d+) \| state=(\S+) \| audio=(\S+)")
_STT = re.compile(r"\bSTT result \| text=(?:'(.*)'|\"(.*)\") \| confidence=([\d.]+) \| stt_duration=([\d.]+)s")
_INTENT = re.compile(r"^\[server\] \[[^\]]+\] intent='([^']*)' state=(\S+)")
_LOGIC = re.compile(r"\bcall_logic response \| action=(\S+) \| next_state=(\S*) \| disposition=(\S*)")
_ENDED = re.compile(r"\bCall ended \| disposition=(\S*) \| turns=(\d+) \| duration=([\d.]+)s")
_GEO = re.compile(r"\bGeo pitch override \| original=\S+ geo=(\S+)")

MAX_LOG_BYTES = 5_000_000


@dataclass
class Turn:
    no: int
    state: str
    audio: str
    t_start: float  # seconds from call start
    customer_text: str = ""
    stt_confidence: float = 0.0
    stt_duration: float = 0.0
    t_stt: float | None = None  # when STT finished, seconds from call start
    no_speech: bool = False
    interrupted: bool = False
    intent: str = ""
    action: str = ""  # next_state | dispose | ""
    next_state: str = ""
    disposition: str = ""

    @property
    def speech_start(self) -> float:
        """Best guess for where the customer's words start in the recording."""
        if self.t_stt is None:
            return self.t_start
        return max(self.t_start, self.t_stt - self.stt_duration - 1.0)


@dataclass
class CallLog:
    turns: list[Turn] = field(default_factory=list)
    end_disposition: str = ""
    duration: float | None = None
    ended: bool = False


def fetch_log(url: str, allowed_prefix: str, timeout: float = 30.0) -> str:
    if not url.startswith(allowed_prefix):
        raise ValueError("log_url outside the allowed prefix")
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        data = resp.read(MAX_LOG_BYTES)
    return data.decode("utf-8", "replace")


def parse_log(text: str) -> CallLog:
    log = CallLog()
    t0: datetime | None = None
    cur: Turn | None = None
    pending_geo: str | None = None

    for raw in text.splitlines():
        if "wait_for_silence vars" in raw:
            continue
        m = _LINE.match(raw)
        if not m:
            continue
        ts = datetime.fromisoformat(m.group(1))
        level, body = m.group(2), m.group(3)
        if t0 is None:
            t0 = ts
        offset = (ts - t0).total_seconds()

        if level == "DEBUG":
            continue  # "deepgram intent=" / "keyword intent=" are not the final intent

        if (g := _GEO.search(body)):
            pending_geo = g.group(1)
            continue
        if (t := _TURN.search(body)):
            cur = Turn(no=int(t.group(1)), state=t.group(2), audio=t.group(3), t_start=offset)
            if pending_geo and cur.audio == pending_geo:
                pending_geo = None
            log.turns.append(cur)
            continue
        if cur is None:
            continue
        if "Playback interrupted" in body:
            cur.interrupted = True
        elif "No speech detected by VAD" in body:
            cur.no_speech = True
        elif (s := _STT.search(body)):
            text_ = s.group(1) if s.group(1) is not None else (s.group(2) or "")
            cur.customer_text = "" if cur.no_speech else text_.strip()
            cur.stt_confidence = float(s.group(3))
            cur.stt_duration = float(s.group(4))
            cur.t_stt = offset
        elif (i := _INTENT.match(body)):
            cur.intent = i.group(1)
        elif (c := _LOGIC.search(body)):
            cur.action, cur.next_state, cur.disposition = c.group(1), c.group(2), c.group(3)
        elif (e := _ENDED.search(body)):
            log.ended = True
            log.end_disposition = e.group(1)
            log.duration = float(e.group(3))
    return log
