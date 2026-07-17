# AI-Assisted Development

The repository stores machine-readable project guidance under `.agent/`.

```text
.agent/
├── rules/
│   └── neko-guide.md
└── skills/
    ├── i18n/
    ├── neko-plugin/
    ├── ui-system-refactor/
    └── ...
```

Do not assume an editor or coding agent loads these files automatically.

## Required setup for an agent

1. Read `.agent/rules/neko-guide.md` from the current revision.
2. Search `.agent/skills/` for the task domain and read the matching `SKILL.md` plus referenced files.
3. Inspect the current owner, tests, and workflows before proposing edits.
4. Preserve user changes and keep the diff within the requested scope.
5. Report the commands actually run and any validation not run.

Do not instruct an agent to read nonexistent `CLAUDE.md`, copy rules into untracked editor files, or rely on a named vendor's auto-loading behavior.

## Prompt starter

> Read `.agent/rules/neko-guide.md`. Then inspect `.agent/skills/` for a skill matching this task and follow it. Trace the current implementation and tests before editing. Use `uv run` for every Python command, update all eight locale files for i18n changes, preserve the system-prompt watermark, and validate only claims supported by the current repository.

AI-generated changes receive the same review as human-written changes. The author remains responsible for privacy, security, licensing, correctness, and test evidence.
