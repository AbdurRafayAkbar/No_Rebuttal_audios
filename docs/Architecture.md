# Architecture — No_Rebuttal_audios

## 1. Overview

Two processes on a Linux server, reading the existing `call_data` MySQL server (`SELECT` only, no changes there) and writing to a separate app database that holds our own tables (D23):

1. **Nightly analyzer** (`nra run-daily`) — started by a systemd timer (or cron).
2. **Review web app** (`nra serve`) — FastAPI app running as a systemd service.

```
                    ┌───────────────────────────── Linux audio server ────────────────────────────┐
                    │                                                                              │
 systemd timer ──►  │  nra run-daily                                                               │
   (23:30)          │   1. select today's calls  ◄──── MySQL: call_records (existing, read-only)   │
                    │   2. fetch audio  ◄──── Hetzner object storage (audio_url, WAV, HTTPS)       │
                    │   3. Deepgram STT  ───────────────────────────────►  Deepgram API (HTTPS)    │
                    │   4. cache transcript JSON ──► disk: data/transcripts/<audio_id>.json        │
                    │   5. bot speaker + clip matching  ◄── app DB: rebuttal_clips                 │
                    │   6. question rules + LLM judge  ─────────────────►  gpt-oss-120b (SGLang)   │
                    │   7. write candidates ──► app DB: temporary_no_rebuttal_audios               │
                    │   8. write run stats   ──► app DB: processing_runs, processed_audios         │
                    │                                                                              │
 Reviewer browser ─►│  nra serve (FastAPI + Jinja2 + HTMX, port 8080)                              │
   (LAN, login)     │   - list / filter / group candidates                                         │
                    │   - stream audio segment via proxy from object storage (HTTP range)          │
                    │   - approve ──► no_rebuttal_audios ; reject ──► status='rejected'            │
                    │   - run dashboard                                                            │
                    └──────────────────────────────────────────────────────────────────────────────┘
```

## 2. Technology

| Concern | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Best ecosystem for audio + APIs; runs fine on Linux. |
| STT | Deepgram `nova-3`, pre-recorded API | Already owned. Diarization for mono files. |
| DB access | SQLAlchemy 2.x + PyMySQL | Mature MySQL support. |
| Migrations | Alembic | Versioned schema for our new tables only. |
| Fuzzy matching | rapidfuzz | Fast text similarity for clip matching and question grouping. |
| LLM judge | Self-hosted `gpt-oss-120b` (SGLang, OpenAI-compatible API, 32k context) via `openai` SDK, JSON output | Decides whether the bot clip answers the customer question. |
| Web | FastAPI + Jinja2 + HTMX | Server-rendered, no JS build step, easy to maintain. |
| Auth | Session cookie, bcrypt password hashes | Small team, LAN only. |
| Config | pydantic-settings + `.env` | Secrets out of git. |
| Scheduling | systemd timer (cron as fallback) | Native on the Linux server. |
| Web service | systemd service running `uvicorn` | Keeps the review app running after reboot. |
| Tests | pytest | Unit tests for rules with fixture transcripts. |

## 3. Components (Python package `nra`)

```
nra/
  config.py          settings from .env (DB URL, Deepgram key, audio root, campaign map)
  db/
    models.py        SQLAlchemy models for our tables
    source.py        read-only adapter for the existing call_records table (column mapping in config)
  ingest/
    selector.py      pick calls for a date that are not yet processed
    transcriber.py   Deepgram client, async, concurrency limit, retries, JSON cache
  analysis/
    clips.py         load clip library, normalize text, match utterance -> clip
    speakers.py      decide which diarized speaker is the bot
    turns.py         merge utterances into ordered bot/customer turns
    detector.py      question/objection rules + rebuttal check -> candidates
    grouping.py      normalize question text -> question_key
  pipeline.py        orchestrates one call end to end; one run over many calls
  web/
    app.py, routes/, templates/, static/
  cli.py             nra run-daily | run --from --to | import-clips | serve | create-user
```

## 4. Data flow per call

### 4a. MVP (built, log-first, see Memory D24)

1. **Select.** `nra.source` runs one parameterized `SELECT id, start_time, end_time, campaign_id, audio_url, log_url, disposition` on `call_records` for one shift (`SHIFT_START` on the date to `SHIFT_END` next day in `RUN_TZ`, default 18:00 to 04:00 Asia/Karachi, converted to UTC) and `campaign_id IN (campaign values)`. Session is `READ ONLY`. Ids already `done` in the app DB are skipped.
2. **Fetch log.** `nra.botlog` downloads the call `log_url` (prefix-checked, max 5 MB) into memory. The log is not saved.
3. **Parse.** Per turn: bot state + audio, customer STT text and timing, intent, next state or disposition.
4. **Detect.** `nra.detect` applies the Q-* and R-* rules (rules.md) and suggests a rebuttal from the manifest keywords.
5. **Judge (optional).** If `LLM_ENDPOINT` is set, `nra.llm` asks the LLM per candidate; verdicts are cached by question key.
6. **Persist + report.** SQLite `data/nra.db` (`processed_audios`, `candidates`, `llm_verdicts`, `processing_runs`); `nra.report` writes the daily HTML + CSV.

### 4b. Full design (audio transcription, later)

1. **Select.** `selector` reads the existing `call_records` table for the target date and excludes ids in `processed_audios` with `status='done'`.
2. **Transcribe.** If `data/transcripts/<audio_id>.json` exists, reuse it. Otherwise send the call `audio_url` to Deepgram (Deepgram fetches the WAV directly; no local download) with `model=nova-3, diarize=true, punctuate=true, smart_format=true, utterances=true`. Save the raw JSON.
3. **Identify bot.** For each diarized speaker, compute the share of its utterances that match a clip (similarity ≥ threshold). The speaker with the highest match share is the bot. If no speaker passes the minimum share, mark the call `needs_attention` and skip detection.
4. **Build turns.** Merge consecutive utterances of the same speaker. Each bot turn gets `clip_id` + `clip_type` (or `unknown`).
5. **Detect.** Apply `rules.md` to each customer turn and the bot turn that follows.
6. **Persist.** Insert candidates into `temporary_no_rebuttal_audios` (unique on `audio_id + start_ms`). Mark the call `done` in `processed_audios`.

## 5. Concurrency and scale

- `asyncio` with a semaphore (default 20 concurrent Deepgram requests, configurable).
- Deepgram calls retry 3 times with exponential backoff on 429/5xx.
- Each call is processed in its own try/except; failures go to `processed_audios.status='failed'` with the error text and are retried on the next run (max 3 attempts).
- At 10,000 calls × ~3 minutes average audio, Deepgram processes far faster than real time; the expected bottleneck is upload bandwidth. The concurrency setting is the tuning knob.

## 6. Clip library

- Reviewer/admin runs `nra import-clips --campaign FE --folder <path>` once per campaign (and again when clips change).
- Each clip is transcribed with Deepgram; text stored in `rebuttal_clips`.
- Clip type comes from a CSV manifest (`clip_file, clip_type, topic_keywords`) so the rules know which clips are rebuttals, fallbacks, script steps or closings.

## 7. Deployment

Server OS: **Linux** (confirmed 2026-11-03). Run everything as a non-root user `nra`.

1. Install Python 3.12; clone the repo to `/opt/no_rebuttal_audios`; create venv `/opt/no_rebuttal_audios/.venv`.
2. `pip install -e .`; copy `.env.example` to `.env` (mode 600) and fill in values.
3. `alembic upgrade head` creates our tables (never touches the existing `call_records` table).
4. `nra import-clips --campaign medicare --folder campaigns/medicare/clips --manifest campaigns/medicare/clips_manifest.csv`.
5. `nra create-user <name>` for each reviewer.
6. systemd timer `nra-daily.timer` (daily 23:30, server time) starting `nra-daily.service`, which runs `.venv/bin/nra run-daily`.
7. systemd service `nra-web.service` running `.venv/bin/nra serve --port 8080`, `Restart=always`.
8. Logs: `logs/run-YYYY-MM-DD.log` (rotating, kept 30 days), plus `journalctl -u nra-web`.

## 8. LLM classifier (v1)

`detector.py` exposes a `Classifier` protocol: `classify(customer_turn, bot_turn, context) -> Decision`.
- `LLMClassifier` (default in v1): sends the customer question, the matched clip name and text, and 2 context turns to the owner LLM. Expects JSON (`answers_question`, `is_question`, `confidence`, `reason`).
- `RuleClassifier`: fallback when the LLM is down or not configured (topic keyword check).
- `nra/llm/client.py` wraps the provider behind one interface, so the provider changes by config only. Verdicts are cached in `llm_verdicts` by input hash.
- Only candidates reach the LLM (rule pre-filter), so volume is a few LLM calls per call, not one per utterance.
