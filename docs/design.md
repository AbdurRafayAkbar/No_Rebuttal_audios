# Design — No_Rebuttal_audios

Database schema, key algorithms and review page design.

## 1. Database schema (MySQL 8, InnoDB, utf8mb4)

The existing `call_records` table (database `call_data`) is **read only**. Columns are mapped in config:

| Config key | `call_records` column | Notes |
|---|---|---|
| `SOURCE_TABLE` | `call_records` | |
| `SOURCE_COL_ID` | `id` | `unique_id` is also unique |
| `SOURCE_COL_CAMPAIGN` | `campaign_id` | Medicare = `001camp`, `001`; FE = `bot_camp`, `BOT_CAMP`; MVA = `009`, `bot2closer`. One campaign can have several values. `character` is only the bot voice persona |
| `SOURCE_COL_CALL_DATE` | `start_time` | UTC |
| `SOURCE_COL_AUDIO_URL` | `audio_url` | WAV in object storage |
| `SOURCE_COL_LOG_URL` | `log_url` | Bot log; present for almost all recent calls. Lists turn states and played clip names. Optional input |
| `SOURCE_COL_DISPOSITION` | `disposition` | e.g. TRANSFERRED, NI, DNC, AM |

Other columns (`caller_id`, `lead_id`, `user_id`) hold customer or agent data: never log them.

```sql
CREATE TABLE campaigns (
  id            SMALLINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  code          VARCHAR(20)  NOT NULL UNIQUE,      -- FE, MEDICARE, MVA
  name          VARCHAR(100) NOT NULL,
  source_value  VARCHAR(255) NOT NULL,             -- comma separated call_records.campaign_id values, e.g. '001camp,001'
  is_active     BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE rebuttal_clips (
  id              INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  campaign_id     SMALLINT UNSIGNED NOT NULL,
  clip_name       VARCHAR(100) NOT NULL,           -- e.g. Rebuttal_Is_This_A_Scam_Answer_V2
  clip_file       VARCHAR(255) NOT NULL,
  clip_type       ENUM('script','rebuttal','fallback','closing','transfer') NOT NULL,
  topic_keywords  VARCHAR(500) NULL,               -- comma separated, for rebuttal relevance check
  transcript      TEXT NOT NULL,
  transcript_norm TEXT NOT NULL,                   -- lowercase, no punctuation
  created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_clip (campaign_id, clip_file),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE processing_runs (
  id               INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  run_date         DATE NOT NULL,                  -- call date analyzed
  trigger_type     ENUM('scheduled','manual') NOT NULL,
  started_at       DATETIME NOT NULL,
  finished_at      DATETIME NULL,
  status           ENUM('running','success','partial','failed') NOT NULL,
  calls_total      INT UNSIGNED NOT NULL DEFAULT 0,
  calls_done       INT UNSIGNED NOT NULL DEFAULT 0,
  calls_failed     INT UNSIGNED NOT NULL DEFAULT 0,
  calls_attention  INT UNSIGNED NOT NULL DEFAULT 0,
  questions_found  INT UNSIGNED NOT NULL DEFAULT 0,
  KEY ix_run_date (run_date)
);

CREATE TABLE processed_audios (
  audio_id        BIGINT UNSIGNED PRIMARY KEY,     -- id from existing call_records table
  campaign_id     SMALLINT UNSIGNED NOT NULL,
  last_run_id     INT UNSIGNED NOT NULL,
  status          ENUM('done','failed','needs_attention') NOT NULL,
  attempts        TINYINT UNSIGNED NOT NULL DEFAULT 1,
  error           TEXT NULL,
  questions_found SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  FOREIGN KEY (last_run_id) REFERENCES processing_runs(id)
);

CREATE TABLE temporary_no_rebuttal_audios (
  id                BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  run_id            INT UNSIGNED NOT NULL,
  audio_id          BIGINT UNSIGNED NOT NULL,
  campaign_id       SMALLINT UNSIGNED NOT NULL,
  call_date         DATE NOT NULL,
  audio_path        VARCHAR(500) NOT NULL,
  start_ms          INT UNSIGNED NOT NULL,         -- customer question start
  end_ms            INT UNSIGNED NOT NULL,         -- end of bot response
  customer_text     TEXT NOT NULL,
  bot_response_text TEXT NULL,
  bot_clip_id       INT UNSIGNED NULL,
  reason_code       VARCHAR(40) NOT NULL,          -- see rules.md R-NR codes
  confidence        DECIMAL(4,3) NOT NULL,
  question_key      CHAR(40) NOT NULL,             -- sha1 of normalized question
  status            ENUM('pending','approved','rejected') NOT NULL DEFAULT 'pending',
  reviewed_by       INT UNSIGNED NULL,
  reviewed_at       DATETIME NULL,
  review_note       VARCHAR(500) NULL,
  llm_reason        VARCHAR(500) NULL,             -- short LLM explanation shown to reviewer
  created_at        DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_moment (audio_id, start_ms),
  KEY ix_queue (status, campaign_id, call_date),
  KEY ix_qkey (question_key),
  FOREIGN KEY (run_id) REFERENCES processing_runs(id),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE no_rebuttal_audios (
  id                BIGINT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  temp_id           BIGINT UNSIGNED NOT NULL UNIQUE,  -- source row in temporary table
  audio_id          BIGINT UNSIGNED NOT NULL,
  campaign_id       SMALLINT UNSIGNED NOT NULL,
  call_date         DATE NOT NULL,
  audio_path        VARCHAR(500) NOT NULL,
  start_ms          INT UNSIGNED NOT NULL,
  end_ms            INT UNSIGNED NOT NULL,
  customer_text     TEXT NOT NULL,
  bot_response_text TEXT NULL,
  question_key      CHAR(40) NOT NULL,
  approved_by       INT UNSIGNED NOT NULL,
  approved_at       DATETIME NOT NULL,
  resolved          BOOLEAN NOT NULL DEFAULT FALSE,  -- set TRUE when a new rebuttal clip covers it
  KEY ix_campaign_key (campaign_id, question_key),
  FOREIGN KEY (temp_id) REFERENCES temporary_no_rebuttal_audios(id),
  FOREIGN KEY (campaign_id) REFERENCES campaigns(id)
);

CREATE TABLE llm_verdicts (
  input_hash   CHAR(40) PRIMARY KEY,              -- sha1 of prompt version + inputs
  model        VARCHAR(100) NOT NULL,
  verdict_json JSON NOT NULL,
  created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE reviewers (
  id            INT UNSIGNED PRIMARY KEY AUTO_INCREMENT,
  username      VARCHAR(50) NOT NULL UNIQUE,
  password_hash VARCHAR(100) NOT NULL,
  is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
```

**Approve is one transaction:** insert into `no_rebuttal_audios`, update temporary row to `approved` with reviewer and time. Approve of a group runs the same for every pending row with the selected `question_key` and campaign.

## 2. Medicare clip catalog

Source: `campaigns/medicare/source/Medicare_Insurance_Script_and_Rebuttals.pdf` (bot persona "Della"). Manifest: `campaigns/medicare/clips_manifest.csv` (105 rows). Audio files: `campaigns/medicare/clips/pitches/` (45) and `campaigns/medicare/clips/rebuttals/` (60), MP3, 24 kHz, mono, about 8–12 s each. `clip_file` is relative to `campaigns/medicare/`. The files are byte-identical copies of the originals; never edit or re-encode them.

- 15 pitch steps x 3 versions, in script order: Introduction_Pitch, Age_Ask, Medicare_Status, Medicare_Parts_Confirmation, Current_Plan, Coverage_Satisfaction, Monthly_Premium, Prescription_Coverage, Doctor_Network, Dental_Vision_Hearing, Additional_Coverage, Out_Of_Pocket_Costs, Benefits_Review, Licensed_Agent_Transfer, Closing_Pitch.
- 20 rebuttals x 3 answers (`Rebuttal_*_Answer_V1..V3`).
- Type mapping: Licensed_Agent_Transfer = `transfer`, Closing_Pitch = `closing`, other pitches = `script`, `Rebuttal_*` = `rebuttal`. Fallback clips ("sorry, I didn't catch that") are added if they exist.

## 3. Algorithms

### 2.1 Text normalization
Lowercase, strip punctuation, collapse whitespace, expand common contractions ("what's" → "what is"). Used for clips, utterances and question keys.

### 2.2 Clip matching
For a bot utterance `u` and campaign clips `C`:
`score(u, c) = rapidfuzz.fuzz.token_set_ratio(norm(u), norm(c.transcript)) / 100`.
Best clip with `score ≥ CLIP_MATCH_THRESHOLD` (default 0.80) → matched. Long utterances may hold two clips back to back; use `partial_ratio` on sliding windows when the utterance is more than 1.5× the best clip length.

### 2.3 Bot speaker identification
For each diarized speaker: `match_share = matched utterances / total utterances`. Bot = speaker with highest `match_share`, required ≥ 0.5. All other speakers are customer. Diarization errors (bot and customer merged into one speaker) are handled by also checking every utterance against clips: a matched utterance is always bot, regardless of speaker label.

### 2.4 Question key
`question_key = sha1(campaign_code + ":" + norm(question_core))`, where `question_core` removes filler words ("um", "uh", "like", "so", "okay"). Near duplicates: on insert, if an existing pending/approved key in the same campaign has `token_set_ratio ≥ 0.90`, reuse that key.

## 4. Review page

Server-rendered pages, HTMX for in-place actions.

| Page | Content |
|---|---|
| `/login` | Username + password. |
| `/` Queue | Filters: campaign, date range, status (default pending), reason code. Grouped view by `question_key` showing count, sample text, campaigns. Expand group → rows. |
| Row card | Customer text (bold), bot response text and matched clip name, reason code, confidence, LLM reason, audio player that starts at `start_ms - 3s` and stops at `end_ms + 2s`, buttons **Approve**, **Reject**, note field. Keyboard: `A` approve, `R` reject, `J/K` next/prev. |
| `/approved` | `no_rebuttal_audios` grouped by campaign + question_key, sorted by count. Export CSV. Mark resolved. |
| `/runs` | Table of `processing_runs`: date, status, totals, per-campaign questions found. Link to failed calls with error text. |
| `/admin` | Reviewer accounts, clip library per campaign (admin only). |

Audio endpoint: `GET /audio/{temp_id}` checks login, resolves path from the DB row (never from the request), and streams with HTTP range support so the player can seek.
