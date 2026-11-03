# PRD — No_Rebuttal_audios

## 1. Problem

Our outbound campaigns (FE, Medicare, MVA) use a voice bot. The bot plays prerecorded clips: script questions and rebuttals. Customers sometimes ask a question or raise an objection that no clip answers. These are **no-rebuttal moments**. Today nobody sees them, so the bot never gets new rebuttal clips, and calls are lost.

## 2. Goal

Every day, automatically analyze all call recordings of that day, find customer questions the bot could not answer, and give the team a review page to confirm them. Confirmed questions become the backlog for new rebuttal clips.

## 3. Users

| User | Need |
|---|---|
| Reviewer (small team) | See flagged questions, play the audio around the moment, approve or reject quickly. |
| Campaign / script owner | See the list of approved no-rebuttal questions per campaign, ranked by frequency, to record new clips. |
| Operator / admin | Know whether the nightly run succeeded, how many calls it processed, and what failed. |

## 4. Scope

### In scope (v1)
1. Nightly job on the audio server that processes all calls of the day. **Rollout: Medicare first**, then FE and MVA.
2. Transcription with Deepgram (mono recordings, diarization on).
3. Matching bot speech to the known prerecorded clip library per campaign.
4. Detection of customer questions/objections without a fitting answer: a rule pre-filter finds candidates, then an **LLM (owner-provided) judges** whether the bot response answers the question.
5. Storage of candidates in `temporary_no_rebuttal_audios`.
6. Web review page: list, filter by campaign/date/status, play audio segment, read transcript context, approve/reject.
7. Approved items copied to `no_rebuttal_audios`. Rejected items stay in the temporary table with `status = 'rejected'`.
8. Daily run summary on the review page: calls processed, failures, questions found per campaign.
9. Reviewer accounts with login; every decision records who made it and when.

### Out of scope (v1)
- Recording new rebuttal clips or changing the bot.
- Real-time (during the call) detection.
- Email / Slack notifications.

## 5. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | The job runs automatically once per day at a configured time (default 23:30 server time) and processes calls whose call date is that day. |
| FR-2 | The job can be run manually for any date range (backfill) from the command line. |
| FR-3 | The job is idempotent: a call already processed successfully is never processed again unless forced. |
| FR-4 | Each call is transcribed with Deepgram (diarization, punctuation, utterances). |
| FR-5 | The system identifies which speaker is the bot by matching utterances to the campaign clip library. |
| FR-6 | A customer utterance is a candidate when it is a question or objection (rules in `rules.md`). |
| FR-7 | A candidate is flagged as no-rebuttal when the bot does not answer it: the bot plays a different rebuttal, a script line, a fallback, a closing, or nothing. The LLM decides whether the bot clip answers the question (rules in `rules.md`). |
| FR-8 | Each flagged moment is stored once in `temporary_no_rebuttal_audios` with campaign, audio reference, timestamps, customer text, bot response text and reason code. |
| FR-9 | Similar questions get the same `question_key` so reviewers can approve a whole group at once. |
| FR-10 | Reviewers log in, filter, play the audio segment (with a few seconds of context), and approve or reject single rows or groups. |
| FR-11 | Approve copies the row into `no_rebuttal_audios`; the temporary row gets `status = 'approved'`. |
| FR-12 | Reject sets `status = 'rejected'` and keeps the row. Rejected `question_key`s lower the priority of future identical flags. |
| FR-13 | The review page shows each run: date, status, calls total/processed/failed, questions found per campaign. |
| FR-14 | Failed calls (missing file, Deepgram error) are logged per call and retried on the next run. |

## 6. Non-functional requirements

- **Volume:** 1,000–10,000 calls per day. The run must finish within 4 hours at 10,000 calls.
- **Cost control:** each call is sent to Deepgram at most once; transcripts are cached.
- **Reliability:** one bad call never stops the run.
- **Security:** Deepgram key and DB credentials only in `.env` (never in git). Review page requires login. Audio files are served only to logged-in users.
- **Auditability:** every approve/reject stores reviewer and timestamp.
- **Platform:** same server as the audio files (OS to confirm, Linux likely); MySQL database.
- **LLM:** owner-provided model; API key only in `.env`. Only candidate turns plus short context are sent, never full calls.

## 7. Success metrics

- 100% of daily calls processed or listed as failed with a reason.
- Reviewer precision: at least 60% of flagged rows approved after rule tuning (measured from approve/reject counts).
- Time to review one row under 30 seconds.

## 8. Open questions

1. ~~Existing MySQL table~~ Answered: `call_records`. Filter by `campaign_id`: Medicare = `001camp`, `001`.
2. ~~Audio location~~ Answered: WAV objects in Hetzner object storage (`audio_url`). Deepgram reads the URL directly.
3. Call language: English only, or also Spanish?
4. Rebuttal clip files per campaign, with names that tell us their type (script / rebuttal / fallback / closing).
5. Server time zone and preferred run time.
6. Port and hostname for the review page inside the office network.
7. ~~LLM provider, model name~~ Answered: `gpt-oss-120b` on SGLang. Open: endpoint URL, API key, rate limits.
8. ~~Server OS~~ Linux (confirmed). Medicare clip audio is in `campaigns/medicare/clips/`. Still open: where the Medicare call recordings are stored.
