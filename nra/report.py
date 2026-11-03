"""Daily report: HTML page (with audio players) and CSV, under DATA_DIR/reports."""
from __future__ import annotations

import csv
import html
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

CSV_FIELDS = ["audio_id", "start_time", "turn_no", "customer_text", "bot_state", "response",
              "q_rule", "r_rule", "confidence", "repeats", "suggested_rebuttal", "llm_reason",
              "audio_url", "speech_start"]


def _rows(conn: sqlite3.Connection, run_date: str, campaign: str) -> list[dict]:
    q = """
    SELECT c.*, p.start_time, p.audio_url FROM candidates c
    JOIN processed_audios p ON p.audio_id = c.audio_id
    WHERE c.run_date=? AND c.campaign=? AND c.status='flagged'
    ORDER BY c.confidence DESC, c.audio_id, c.turn_no
    """
    out = []
    for r in conn.execute(q, (run_date, campaign)):
        d = dict(r)
        v = json.loads(d["llm_verdict"]) if d["llm_verdict"] else {}
        d["llm_reason"] = v.get("reason", "")
        if v.get("suggested_rebuttal"):
            d["suggested_rebuttal"] = v["suggested_rebuttal"]
        out.append(d)
    return out


def _stats(conn, run_date: str, campaign: str) -> dict:
    s = {r[0]: r[1] for r in conn.execute(
        "SELECT status, COUNT(*) FROM processed_audios WHERE run_date=? AND campaign=? GROUP BY status",
        (run_date, campaign))}
    s["dismissed_by_llm"] = conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE run_date=? AND campaign=? AND status='dismissed_by_llm'",
        (run_date, campaign)).fetchone()[0]
    return s


def write_reports(conn, out_dir: Path, run_date: str, campaign: str) -> tuple[Path, Path, int]:
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = _rows(conn, run_date, campaign)
    stats = _stats(conn, run_date, campaign)
    base = out_dir / f"{run_date}_{campaign.lower()}"

    csv_path = base.with_suffix(".csv")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)

    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        groups[r["suggested_rebuttal"] or "No matching rebuttal (new question?)"].append(r)
    by_rule: dict[str, int] = defaultdict(int)
    for r in rows:
        by_rule[r["r_rule"]] += 1

    e = html.escape
    parts = [f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unanswered questions {e(run_date)}</title>
<style>
:root{{--bg:#fafaf7;--fg:#1d1d1b;--mut:#6b6b66;--line:#deddd6;--card:#fff;--hi:#b4441f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#161615;--fg:#ecebe6;--mut:#9a9992;--line:#33332f;--card:#1f1f1d;--hi:#f08a62}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:24px 16px}}
h1{{font-size:22px;margin:0 0 4px}} h2{{font-size:17px;margin:28px 0 8px}}
.mut{{color:var(--mut)}} .stats{{display:flex;flex-wrap:wrap;gap:8px;margin:16px 0}}
.stat{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 12px}}
.stat b{{display:block;font-size:20px}}
.row{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0}}
.q{{font-size:16px}} .q::before{{content:"Customer: ";color:var(--mut)}}
.meta{{color:var(--mut);font-size:13px;margin-top:4px;word-break:break-word}}
.tag{{color:var(--hi);font-weight:600}} audio{{width:100%;max-width:420px;margin-top:6px}}
</style></head><body><main>
<h1>Unanswered customer questions</h1>
<div class="mut">{e(campaign)} · calls on {e(run_date)}</div>
<div class="stats">
<div class="stat"><b>{stats.get('done', 0)}</b>calls analysed</div>
<div class="stat"><b>{stats.get('no_log', 0)}</b>no bot log</div>
<div class="stat"><b>{stats.get('failed', 0)}</b>failed</div>
<div class="stat"><b>{len(rows)}</b>flagged turns</div>
<div class="stat"><b>{stats['dismissed_by_llm']}</b>dismissed by LLM</div>
</div>
<div class="mut">{' · '.join(f'{e(k)}: {v}' for k, v in sorted(by_rule.items()))}</div>
"""]
    for name, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        parts.append(f"<h2>{e(name)} <span class='mut'>({len(items)})</span></h2>")
        for r in items:
            src = f"{r['audio_url']}#t={max(0.0, float(r['speech_start'] or 0)):.1f}"
            reason = f" · LLM: {e(r['llm_reason'])}" if r["llm_reason"] else ""
            parts.append(
                f"<div class='row'><div class='q'>{e(r['customer_text'])}</div>"
                f"<div class='meta'><span class='tag'>{e(r['r_rule'])}</span> · conf {r['confidence']:.2f}"
                f" · bot was in {e(r['bot_state'] or '')}, next: {e(r['response'] or '')}"
                f" · asked {r['repeats']}x · call {r['audio_id']} turn {r['turn_no']} · {e(str(r['start_time']))} UTC{reason}</div>"
                f"<audio controls preload='none' src='{e(src, quote=True)}'></audio></div>")
    parts.append("</main></body></html>")
    html_path = base.with_suffix(".html")
    html_path.write_text("".join(parts), encoding="utf-8")
    return html_path, csv_path, len(rows)
