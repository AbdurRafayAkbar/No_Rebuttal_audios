import http.client
import json
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest

from nra import store, web
from nra.config import load_settings


@pytest.fixture()
def server(tmp_path):
    s = replace(load_settings(), data_dir=tmp_path)
    conn = store.open_db(s.db_path)
    url = s.audio_url_prefix + "x.wav"
    # 2026-11-03 shift in PKT is 13:00 to 23:00 UTC; second call falls outside it.
    conn.execute("INSERT INTO processed_audios(audio_id,run_date,campaign,start_time,audio_url,status)"
                 " VALUES (1,'2026-11-03','MEDICARE','2026-11-03 14:00:00',?,'done')", (url,))
    conn.execute("INSERT INTO processed_audios(audio_id,run_date,campaign,start_time,audio_url,status)"
                 " VALUES (2,'2026-11-03','MEDICARE','2026-11-04 01:00:00','https://evil.example/a.wav','done')")
    for aid in (1, 2):
        conn.execute("INSERT INTO candidates(audio_id,turn_no,run_date,campaign,customer_text,r_rule,confidence,"
                     "speech_start,question_key) VALUES (?,2,'2026-11-03','MEDICARE','how much is it',"
                     "'R-NR-IGNORED',0.8,3.0,'k')", (aid,))
    conn.commit()
    conn.close()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), web.make_handler(web.Api(s), 0))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = web.make_handler(web.Api(s), port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield port
    httpd.shutdown()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port)
    h = {"Host": f"127.0.0.1:{port}", **(headers or {})}
    c.request(method, path, body=json.dumps(body) if body is not None else None, headers=h)
    r = c.getresponse()
    return r.status, r.read()


def test_day_filters_shift_window_and_review_roundtrip(server):
    st, body = _req(server, "GET", "/api/day?date=2026-11-03")
    d = json.loads(body)
    assert st == 200 and [i["audio_id"] for i in d["items"]] == [1]
    assert d["items"][0]["start_local"] == "2026-11-03 19:00:00"
    st, _ = _req(server, "POST", "/api/review", {"audio_id": 1, "turn_no": 2, "decision": "approved"},
                 {"Content-Type": "application/json"})
    assert st == 200
    d = json.loads(_req(server, "GET", "/api/day?date=2026-11-03")[1])
    assert d["items"][0]["decision"] == "approved"
    assert b"approved" in _req(server, "GET", "/api/export.csv?date=2026-11-03")[1]


def test_rejects_bad_requests(server):
    assert _req(server, "GET", "/api/day?date=2026-11-03", headers={"Host": "evil.example"})[0] == 403
    assert _req(server, "POST", "/api/review", {"audio_id": 1, "turn_no": 2, "decision": "approved"})[0] == 415
    assert _req(server, "POST", "/api/review", {"audio_id": 1, "turn_no": 2, "decision": "drop"},
                {"Content-Type": "application/json"})[0] == 400
    assert _req(server, "GET", "/api/day?date=x")[0] == 400
    assert _req(server, "GET", "/")[0] == 200
