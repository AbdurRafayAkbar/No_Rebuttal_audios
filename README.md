# No_Rebuttal_audios

Finds calls where a customer asked a question and the voice bot did not answer it, and sends them to a web review page.

## Folder layout

```
.
├── README.md
├── .gitignore
├── pyproject.toml
├── nra/                  daily pipeline (Python package)
│   ├── config.py         settings from .env, campaign list
│   ├── source.py         read-only SELECT on call_records
│   ├── botlog.py         fetch + parse bot log per call
│   ├── detect.py         question / non-answer rules
│   ├── llm.py            optional LLM judge (OpenAI-compatible)
│   ├── store.py          app DB (SQLite, data/nra.db)
│   ├── report.py         daily HTML + CSV report
│   ├── pipeline.py       one day end to end
│   └── cli.py            `python -m nra ...`
├── prompts/judge_v1.txt  LLM judge prompt
├── tests/                pytest
├── data/                 app DB and reports (gitignored)
├── docs/
│   ├── PRD.md            product requirements
│   ├── Architecture.md   components, data flow, deployment (Linux)
│   ├── design.md         DB schema, clip catalog, algorithms
│   ├── rules.md          detection rules and engineering rules
│   ├── Task.md           task list
│   └── Memory.md         decision log
└── campaigns/
    └── medicare/
        ├── clips_manifest.csv      clip_name, clip_file, clip_type, topic_keywords
        ├── source/
        │   └── Medicare_Insurance_Script_and_Rebuttals.pdf
        └── clips/
            ├── pitches/            45 script/transfer/closing clips (15 steps x 3 versions)
            └── rebuttals/          60 rebuttal clips (20 rebuttals x 3 versions)
```

`clip_file` in the manifest is relative to the campaign folder. Clip audio files are original copies: do not rename, edit or re-encode them.

FE and MVA campaigns will follow the same layout under `campaigns/fe/` and `campaigns/mva/`.

## Daily run

Setup once:

```
python -m venv .venv
.venv/bin/pip install -e .[dev]     # Windows: .venv/Scripts/pip
cp .env.example .env                 # fill DB_*, optional LLM_ENDPOINT
```

Run one shift. A shift is `--date` 18:00 to next day 04:00 Pakistan time (`RUN_TZ`, `SHIFT_START`, `SHIFT_END`). Default date: yesterday in Pakistan time.

```
python -m nra run --date 2026-11-03
python -m nra run --date 2026-11-03 --limit 50 --no-llm   # quick test
python -m nra report --date 2026-11-03                    # rebuild report only
```

Output: `data/reports/<date>_medicare.html` (grouped by suggested rebuttal, audio player starts at the question) and `.csv`.
Calls already done are skipped; use `--force` to redo them.

The pipeline only runs `SELECT` on the call-data server, in a `READ ONLY` session. Bot logs are read in memory and not saved; only the customer turn text, state and URLs are stored.

Tests: `python -m pytest`
