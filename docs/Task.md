# Tasks — No_Rebuttal_audios

Status: `[ ]` todo, `[~]` in progress, `[x]` done.

## Phase 0 — Inputs from the business (blocking)
- [x] Medicare script and rebuttal text (PDF). Seed manifest `campaigns/medicare/clips_manifest.csv`.
- [~] Server access: IP received. OS confirmed **Linux**. Confirm audio root folder, file format. Rotate root password; create a non-root SSH user with a key.
- [ ] LLM provider, model, endpoint, API key (into `.env` only).
- [x] MySQL connection details received (in `.env` only). Source table is `call_records`. Medicare filter: `campaign_id IN ('001camp','001')`.
- [ ] Create a read-only DB user for the app; rotate the root password.
- [~] Rebuttal clip audio files: Medicare done (`campaigns/medicare/clips/`, 105 files, manifest filled); FE, MVA pending.
- [ ] Clip manifest CSV per campaign: `clip_file, clip_type, topic_keywords`.
- [ ] Bot script per campaign (order of script questions).
- [ ] 20 sample calls per campaign, including some known no-rebuttal calls, for testing.
- [ ] Objection phrase list per campaign (start list in rules.md, extend with business).
- [ ] Confirm call language(s), server time zone, run time, review page port.

## Phase 1 — Project setup
- [ ] git init, `.gitignore`, `.env.example`, `pyproject.toml`, README; push to GitHub `main`.
- [ ] Package skeleton `nra/` with config loading.
- [ ] Alembic setup; migration 001 with all new tables from design.md.
- [ ] Source adapter for existing `call_records` table (configurable column mapping).

## Phase 2 — Transcription
- [ ] Deepgram async client with concurrency limit, retries, transcript cache.
- [ ] `nra transcribe --audio-id` debug command.
- [ ] Measure Deepgram time and accuracy on sample calls.

## Phase 3 — Clip library
- [ ] `nra import-clips --campaign --folder --manifest`.
- [ ] Clip matcher + unit tests.
- [ ] Bot speaker identification + unit tests on sample calls.

## Phase 4 — Detection
- [ ] Turn builder.
- [ ] Candidate rules (Q-*) + tests.
- [ ] Response rules (R-*) + tests.
- [ ] LLM client, `LLMClassifier`, prompt in `prompts/`, verdict cache, tests with a mocked LLM.
- [ ] Question key + near-duplicate grouping.
- [ ] Pipeline: one call end to end, persist candidates.
- [ ] `nra run --from --to` and `nra run-daily` with processing_runs stats.
- [ ] Dry run on sample calls; tune thresholds with business.

## Phase 5 — Review page
- [ ] FastAPI app, login, reviewer accounts (`nra create-user`).
- [ ] Queue page with filters and grouping.
- [ ] Audio streaming endpoint (range requests, path safety).
- [ ] Approve / reject (single + group), keyboard shortcuts.
- [ ] Approved list with CSV export and resolved flag.
- [ ] Runs dashboard and failed-calls view.
- [ ] Admin: users, clip library view, un-suppress keys.

## Phase 6 — Deployment
- [ ] Install on Linux server (`/opt/no_rebuttal_audios`, user `nra`), venv, `.env`, migrations.
- [ ] systemd timer for `nra run-daily`.
- [ ] systemd service for review page.
- [ ] Log rotation, 30-day retention.
- [ ] Runbook in README (rerun a day, add a reviewer, add a clip).

## Phase 7 — Tuning (first 2 weeks live)
- [ ] Daily check of approve/reject ratio per reason code.
- [ ] Adjust thresholds and word lists.
- [ ] Compare LLM verdicts with reviewer decisions; tune the prompt.
