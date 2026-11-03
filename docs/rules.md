# Rules — No_Rebuttal_audios

Two kinds of rules: **detection rules** (business logic the analyzer applies) and **engineering rules** (how we build and change this project).

## Part A — Detection rules

All thresholds live in config so they can be tuned without code changes.

### A1. Customer candidate (question or objection)

A customer turn is a **candidate** when any rule matches:

| Code | Rule |
|---|---|
| Q-PUNCT | Deepgram punctuation ends a sentence in the turn with `?`. |
| Q-WH | Turn starts with or contains a question opener: what, why, how, who, where, when, which, is it, is this, are you, can you, could you, do you, does it, will it, would it. |
| Q-OBJ | Turn contains an objection phrase from the campaign objection list (e.g. "how did you get my number", "I already have", "I can't afford", "is this a scam", "I'm not interested because"). |
| Q-INTENT | Deepgram intent detection returns an intent with confidence ≥ 0.6 and the intent is in the "question/concern" list. |

A turn is **ignored** when:
- It has fewer than 3 words and no `?` (e.g. "yes", "okay", "who?" stays because of `?`).
- It answers the previous bot script question (bot clip type `script` asked a yes/no or data question and the customer text is a short answer: yes/no/number/name/date).
- It is pure greeting or goodbye ("hello", "bye", "thank you").

### A2. Bot response check

**Core rule (owner):** when the customer asks a question and the bot does not answer that question, but plays another rebuttal or any other script line, the question goes into `temporary_no_rebuttal_audios`.

Two steps:
1. **Clip match** gives the type of the bot response (table below).
2. **LLM judge** runs when the bot played a `rebuttal` clip. Input: customer question, matched clip name and clip text, 2 turns of context. Output JSON: `{"answers_question": bool, "is_question": bool, "confidence": 0-1, "reason": "..."}`. `answers_question = false` gives R-NR-WRONG-REBUTTAL. `is_question = false` drops the candidate.
   The LLM also checks candidates the rules are unsure about (for example Q-WH only, no `?`) to confirm they are real questions.

Take the **first bot turn after the candidate** (within `RESPONSE_WINDOW_S`, default 8 s).

| Code | Condition | Result |
|---|---|---|
| R-OK | Bot turn matches a `rebuttal` clip AND the LLM says it answers the question. | Answered. Not flagged. |
| R-NR-FALLBACK | Bot turn matches a `fallback` clip ("Sorry, I didn't catch that", "Can you repeat"). | **Flag.** |
| R-NR-IGNORED | Bot turn matches a `script` clip (bot continued the script and skipped the question). | **Flag.** |
| R-NR-WRONG-REBUTTAL | Bot turn matches a `rebuttal` clip, but the LLM says it does not answer the question. | **Flag.** |
| R-NR-OFFTOPIC | Fallback when the LLM is unavailable: the rebuttal clip has none of its topic keywords in the customer text. | **Flag** (confidence 0.5). |
| R-NR-HANGUP | Call ends or bot plays a `closing` clip after the candidate and no rebuttal before it. | **Flag.** |
| R-NR-SILENCE | No bot turn within the response window. | **Flag.** |
| R-TRANSFER | Bot plays a `transfer` clip. | Not flagged (call moved to a human). |
| R-UNKNOWN | Bot turn matches no clip. | Not flagged; counted in run stats for clip library gaps. |

Confidence defaults: FALLBACK 0.9, IGNORED 0.8, HANGUP 0.8, SILENCE 0.7, OFFTOPIC 0.5. WRONG-REBUTTAL uses the LLM confidence.

### A3. Repeats inside one call

If the customer asks the same question again (same `question_key`) in the same call, store only the first occurrence, but raise confidence by 0.1 (max 1.0): repetition means the customer really did not get an answer.

### A4. Learning from reviews

- A `question_key` rejected 3 or more times in a campaign is auto-suppressed: new rows with that key are stored with `status='rejected'` and `review_note='auto-suppressed'`. Admin can un-suppress.
- A `question_key` already in `no_rebuttal_audios` with `resolved = FALSE` still creates new temporary rows (frequency counts matter) but the queue shows them collapsed under the existing group.

## Part B — Engineering rules

1. **Never write to the existing `call_records` table** or move/delete audio objects. Read only, with a read-only DB user.
2. **Secrets only in `.env`.** `.env` is in `.gitignore`. Commit `.env.example` with empty values.
3. **Schema changes only through Alembic migrations.** No manual `ALTER TABLE` on production.
4. **One call failure never stops a run.** Catch per call, log, record in `processed_audios`.
5. **Deepgram is called at most once per audio file.** Always check the transcript cache first.
6. **Thresholds and word lists are config, not code.** Per-campaign overrides allowed.
7. **Every detection rule has a unit test** with a fixture transcript (positive and negative case).
8. **Audio URLs come from the DB row, never from user input,** and must start with the configured `AUDIO_URL_PREFIX` (object storage bucket).
9. **Approve/reject are DB transactions** and record reviewer id and timestamp.
10. **Branches and commits:** work on feature branches, `main` stays deployable. Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
11. **Update `Memory.md`** whenever a decision is made or changed, and `Task.md` when a task is done.
12. **LLM:** key only in `.env`. Send only the candidate turn, the bot response and 2 context turns. Cache every verdict by a hash of its input; never call twice for the same input. Prompt text lives in `prompts/` and is versioned in git.
13. **Server access:** never store server passwords in the repo, docs or logs. Use SSH keys and a non-root user.
14. **Logging:** structured lines with `run_id` and `audio_id`. No customer PII (phone numbers, names) in log files; transcripts live only in the DB and the transcript cache folder.
