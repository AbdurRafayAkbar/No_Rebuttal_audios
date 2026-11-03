"""Local review page: `python -m nra serve`, then open http://127.0.0.1:8000.

Binds to 127.0.0.1 only. Call text stays on this machine. Standard library only.
"""
from __future__ import annotations

import csv
import io
import json
import logging
from contextlib import contextmanager
from datetime import date, datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import pipeline, store
from .config import Settings

log = logging.getLogger("nra.web")
PAGE = Path(__file__).with_name("web_page.html")
DECISIONS = {"approved", "rejected"}
EXPORT_FIELDS = ["audio_id", "start_local", "turn_no", "customer_text", "bot_state", "response", "q_rule",
                 "r_rule", "confidence", "repeats", "suggested_rebuttal", "decision", "note", "audio_url",
                 "speech_start"]


class Api:
    """Read and review functions. One DB connection per call keeps threads apart."""

    def __init__(self, settings: Settings):
        self.s = settings
        store.open_db(settings.db_path).close()  # create or migrate schema once

    @contextmanager
    def _conn(self):
        conn = store.open_db(self.s.db_path)
        try:
            with conn:  # commit on success, roll back on error
                yield conn
        finally:
            conn.close()

    def _window(self, day: str) -> tuple[str, str]:
        s, e = pipeline.day_range_utc(date.fromisoformat(day), self.s.run_tz, self.s.shift_start, self.s.shift_end)
        return str(s), str(e)

    def _local(self, utc_text: str) -> str:
        try:
            d = datetime.fromisoformat(utc_text).replace(tzinfo=timezone.utc)
        except (TypeError, ValueError):
            return utc_text or ""
        return d.astimezone(ZoneInfo(self.s.run_tz)).strftime("%Y-%m-%d %H:%M:%S")

    def days(self, campaign: str) -> list[dict]:
        out = []
        with self._conn() as conn:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT run_date FROM processed_audios WHERE campaign=? ORDER BY run_date DESC", (campaign,))]
            for d in dates:
                s, e = self._window(d)
                n, = conn.execute(
                    "SELECT COUNT(*) FROM candidates c JOIN processed_audios p ON p.audio_id=c.audio_id"
                    " WHERE c.run_date=? AND c.campaign=? AND c.status='flagged'"
                    " AND p.start_time>=? AND p.start_time<?", (d, campaign, s, e)).fetchone()
                out.append({"date": d, "flagged": n})
        return out

    def day(self, day: str, campaign: str) -> dict:
        s, e = self._window(day)
        with self._conn() as conn:
            stats = {r[0]: r[1] for r in conn.execute(
                "SELECT status, COUNT(*) FROM processed_audios WHERE run_date=? AND campaign=?"
                " AND start_time>=? AND start_time<? GROUP BY status", (day, campaign, s, e))}
            rows = conn.execute(
                "SELECT c.id, c.audio_id, c.turn_no, c.bot_state, c.customer_text, c.intent, c.q_rule, c.response,"
                " c.r_rule, c.confidence, c.speech_start, c.suggested_rebuttal, c.played, c.repeats, c.llm_verdict,"
                " c.status, p.start_time, p.audio_url, p.disposition, r.decision, r.note"
                " FROM candidates c JOIN processed_audios p ON p.audio_id=c.audio_id"
                " LEFT JOIN reviews r ON r.audio_id=c.audio_id AND r.turn_no=c.turn_no"
                " WHERE c.run_date=? AND c.campaign=? AND p.start_time>=? AND p.start_time<?"
                " ORDER BY c.confidence DESC, c.audio_id, c.turn_no", (day, campaign, s, e)).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            v = json.loads(d.pop("llm_verdict")) if r["llm_verdict"] else {}
            d["llm_reason"] = v.get("reason", "")
            if v.get("suggested_rebuttal"):
                d["suggested_rebuttal"] = v["suggested_rebuttal"]
            url = d["audio_url"] or ""
            d["audio_url"] = url if url.startswith(self.s.audio_url_prefix) else ""
            d["start_local"] = self._local(d.pop("start_time"))
            items.append(d)
        stats["dismissed_by_llm"] = sum(1 for i in items if i["status"] == "dismissed_by_llm")
        label = f"{day} {self.s.shift_start} to {self.s.shift_end} {self.s.run_tz}"
        return {"date": day, "campaign": campaign, "window": label, "stats": stats, "items": items}

    def review(self, audio_id: int, turn_no: int, decision: str | None, note: str = "") -> None:
        with self._conn() as conn:
            if decision is None:
                conn.execute("DELETE FROM reviews WHERE audio_id=? AND turn_no=?", (audio_id, turn_no))
            else:
                conn.execute(
                    "INSERT INTO reviews(audio_id, turn_no, decision, note) VALUES (?,?,?,?)"
                    " ON CONFLICT(audio_id, turn_no) DO UPDATE SET decision=excluded.decision,"
                    " note=excluded.note, reviewed_at=datetime('now')", (audio_id, turn_no, decision, note[:500]))

    def export_csv(self, day: str, campaign: str) -> str:
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=EXPORT_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(i for i in self.day(day, campaign)["items"] if i["status"] == "flagged")
        return buf.getvalue()


def make_handler(api: Api, port: int):
    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "nra"

        def log_message(self, fmt, *args):  # request lines only; no bodies
            log.debug(fmt, *args)

        def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200):
            self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

        def _error(self, status: int, msg: str):
            self._json({"error": msg}, status)

        def _host_ok(self) -> bool:
            return self.headers.get("Host", "") in allowed_hosts  # blocks DNS rebinding

        def _params(self):
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            campaign = q.get("campaign", "MEDICARE").upper()
            if campaign not in api.s.campaigns:
                raise ValueError("unknown campaign")
            if "date" in q:
                date.fromisoformat(q["date"])
            return u.path, q, api.s.campaigns[campaign].code

        def do_GET(self):  # noqa: N802
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "bad host")
            try:
                path, q, campaign = self._params()
            except ValueError as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            if path == "/":
                csp = ("default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'unsafe-inline';"
                       f" media-src {api.s.audio_url_prefix}; frame-ancestors 'none'")
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8",
                                  {"Content-Security-Policy": csp})
            if path == "/api/days":
                return self._json({"campaigns": sorted(api.s.campaigns), "days": api.days(campaign),
                                   "shift": f"{api.s.shift_start} to {api.s.shift_end} {api.s.run_tz}"})
            if path == "/api/day" and "date" in q:
                return self._json(api.day(q["date"], campaign))
            if path == "/api/export.csv" and "date" in q:
                name = f"{q['date']}_{campaign.lower()}_reviewed.csv"
                return self._send(200, api.export_csv(q["date"], campaign).encode("utf-8-sig"),
                                  "text/csv; charset=utf-8", {"Content-Disposition": f'attachment; filename="{name}"'})
            return self._error(HTTPStatus.NOT_FOUND, "not found")

        def do_POST(self):  # noqa: N802
            if not self._host_ok():
                return self._error(HTTPStatus.FORBIDDEN, "bad host")
            origin = self.headers.get("Origin")
            if origin and origin.split("://", 1)[-1] not in allowed_hosts:
                return self._error(HTTPStatus.FORBIDDEN, "bad origin")
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._error(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "json only")
            if urlparse(self.path).path != "/api/review":
                return self._error(HTTPStatus.NOT_FOUND, "not found")
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n > 4096:
                    raise ValueError("too big")
                body = json.loads(self.rfile.read(n) or b"{}")
                decision = body.get("decision")
                if decision is not None and decision not in DECISIONS:
                    raise ValueError("decision must be approved, rejected or null")
                api.review(int(body["audio_id"]), int(body["turn_no"]), decision, str(body.get("note") or ""))
            except (ValueError, KeyError, TypeError) as exc:
                return self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return self._json({"ok": True})

    return Handler


def serve(settings: Settings, port: int = 8000) -> None:
    api = Api(settings)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(api, port))
    print(f"Review page: http://127.0.0.1:{port}/  (Ctrl+C to stop)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
