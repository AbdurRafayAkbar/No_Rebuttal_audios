# Memory — No_Rebuttal_audios

Project decision log and context. Newest entries at the top of each section.

## Context
- Owner: Abdur Rafay Akbar. Repo: https://github.com/AbdurRafayAkbar/No_Rebuttal_audios
- Campaigns: FE, Medicare, MVA.
- Calls are between a voice bot and a customer. The bot plays **prerecorded clips** (script questions + rebuttals). Every bot turn should match a known clip.
- Volume: 10,000–29,000 calls per day across all clients (measured 2026-11-03). Medicare (`campaign_id` `001camp` + `001`) is about 52,000 calls per week (about 7,400 per day, average 23 s). `character` is the bot voice persona, not the campaign.
- Call data DB: MySQL 8.0.44, database `call_data`, server time zone UTC. Source table is `call_records` (1.34M rows, data from 2026-04-17). About 75% of calls are shorter than 30 s.
- Recordings: WAV files in Hetzner object storage (`audio_url`). Per-call bot logs (`log_url`) exist for almost every recent call (99.8% of Medicare calls, last 7 days). A log lists voice folder, each turn state, the played clip file and the bot STT text.

## Decisions (2026-11-03)
| # | Decision | Why |
|---|---|---|
| D1 | Python 3.12 | Best audio/API ecosystem. |
| D2 | MySQL for all new tables, same server as the existing `call_records` table | Existing data is in MySQL. |
| D3 | Deepgram for transcription, with diarization | Already owned; recordings are **mono mixed**, so speakers must be separated by diarization. |
| D4 | ~~No LLM in v1~~ **Replaced by D11.** | |
| D5 | Bot speech matched against transcribed rebuttal/script clips | Clips are prerecorded, so matching is reliable and also identifies the bot speaker. |
| D6 | Runs on a **Linux** server (OS confirmed 2026-11-03); systemd timer at day end, systemd service for the review page. **Corrected 2026-11-03:** recordings are not on local disk; they are WAV objects in Hetzner object storage, read through `call_records.audio_url` | Original assumption (local files) was wrong. |
| D7 | Results delivered through a **web review page** (FastAPI + HTMX) | Owner choice; small team of reviewers with logins. |
| D8 | Rejected rows stay in `temporary_no_rebuttal_audios` with `status='rejected'` | Feedback for rule tuning; avoid re-flagging. |
| D9 | Approved rows are copied to `no_rebuttal_audios` | Owner requirement. |
| D10 | Similar questions grouped by `question_key` | Reviewers approve groups; script owners see frequency. |
| D11 | **LLM in v1** (owner-provided model). Rules pre-filter candidates; the LLM judges whether the bot clip answers the question | Owner decision. A keyword rule alone cannot tell if a rebuttal fits. |
| D12 | Flag rule: the customer asks a question and the bot plays another rebuttal or any script line instead of answering; the question goes to `temporary_no_rebuttal_audios` | Owner rule. Codes R-NR-WRONG-REBUTTAL and R-NR-IGNORED. |
| D13 | **Medicare first**; FE and MVA later | Owner decision. Medicare script and rebuttals PDF provided. |
| D14 | Server credentials are never stored in the repo or docs | Root password was shared in chat and must be rotated. |
| D15 | Clip audio lives in the repo at `campaigns/<campaign>/clips/{pitches,rebuttals}/`, original file names, byte-identical copies (no re-encode, no rename) | Clip names map 1:1 to manifest `clip_name`; small size (Medicare 6.6 MB). |
| D16 | Docs live in `docs/`; campaign source material in `campaigns/<campaign>/source/` | Clean repo layout. |
| D17 | Source table is `call_records` (read only). Our app uses a separate read-only DB user | Found 2026-11-03. Root credentials must not be used by the app. |
| D18 | Campaign filter is `call_records.campaign_id` (owner decision 2026-11-04). Medicare = `001camp` (primetech) + `001` (futuretell); FE = `bot_camp`, `BOT_CAMP` (fahad); MVA = `009`, `bot2closer` (corvus) | Mapping confirmed from bot logs: voice folders `*/med/*`, `*/fe/*`, `*/mva/*`. Other values (`006`, `EzyTransfer` = vehicle, `1001` = aca/fe, `inbound`) are out of scope. |
| D19 | Deepgram fetches `audio_url` directly (owner decision 2026-11-04); no local download | Simpler; no disk use. Review page streams through a proxy. |
| D20 | The clip files in `campaigns/<campaign>/clips/` are the reference set (owner confirmed correct). The live bot voice folders and the existing `cx_unanswered_asks` feature are not touched; this project is a separate alternative | Owner decision 2026-11-04. Matching uses transcript text, so it does not depend on the bot's own clip file names (`Hello.wav`, `Pitch_AL_afternoon.wav`). |
| D21 | Medicare clip check (2026-11-04): 105 files, all MP3 24 kHz mono 48 kbps, 5.1–16.4 s, no duplicates, no clipping (peak max -1.4 dB), mean level -26.7 to -18.1 dB, leading silence at most 0.57 s | Clips are usable as is; no re-encode needed. |

## Open questions
- Audio objects are reachable without auth (security risk). Owner to decide on a private bucket with signed URLs.
- Clip files for FE and MVA. Medicare has no fallback clips; confirm none exist.
- Call language(s).
- Server time zone, run time, review page port.

## Changelog
- 2026-11-04: Campaign filter by `campaign_id` (D18), Deepgram reads URL directly (D19), existing bot/feature untouched (D20), Medicare clip check (D21).
- 2026-11-03: Call data DB explored (read only). Source table `call_records`; audio in object storage; D6 corrected; D17 added.
- 2026-11-03: Server OS confirmed Linux. Medicare clip audio (45 pitches, 60 rebuttals) added under `campaigns/medicare/clips/`; manifest `clip_file` filled. Docs moved to `docs/`, PDF to `campaigns/medicare/source/`.
- 2026-11-03: Initial system design docs created (PRD, Architecture, design, rules, Task, Memory).
- 2026-11-03: LLM added to v1, Medicare-first scope, Medicare clip catalog and seed manifest.
