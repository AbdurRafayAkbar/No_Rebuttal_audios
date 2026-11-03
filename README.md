# No_Rebuttal_audios

Finds calls where a customer asked a question and the voice bot did not answer it, and sends them to a web review page.

## Folder layout

```
.
├── README.md
├── .gitignore
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
