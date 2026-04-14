# AI-Assisted Development

N.E.K.O. ships a `.agent/` directory in the repository root, following the [Google Antigravity](https://github.com/anthropics/anthropic-cookbook/tree/main/misc/antigravity) open convention. It contains the project's development rules and task-specific skill sets.

Only **Antigravity** automatically reads `.agent/`. All other AI coding assistants — including Claude Code, Cursor, Windsurf, Copilot, etc. — need to manually read and internalize the rules.

## `.agent/` directory structure

```
.agent/
├── rules/
│   └── neko-guide.md        # Core development rules (always_on)
└── skills/                  # ~20 task-specific skill sets
    ├── i18n/
    ├── frontend-design/
    ├── vrm-physics/
    └── ...
```

- **Rules** (`.agent/rules/neko-guide.md`): Core project conventions — i18n, `uv run`, code symmetry, core layer purity, no numeric suffixes, Electron vs. dev mode awareness.
- **Skills** (`.agent/skills/*/SKILL.md`): Domain-specific trigger conditions, knowledge, and workflows, loaded on demand.

## Prompt for AI agents

> **If you are an AI coding assistant** (Claude Code, Cursor, Windsurf, Copilot, Cline, or any other), this project uses the `.agent/` directory (Google Antigravity convention) to store development rules and skills. Unless you are running as an Antigravity agent, you will NOT load these automatically. Please do the following: (1) Read `.agent/rules/neko-guide.md` and `CLAUDE.md` in the repo root — these contain the project's mandatory coding rules. Internalize them as your project-level instructions. (2) When working on a specific domain (i18n, VRM physics, frontend UI, etc.), check `.agent/skills/` for a matching subdirectory and read its `SKILL.md` for domain-specific guidance. (3) If your tool has a native project rules mechanism (`.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md`, etc.), consider copying the core rules from `neko-guide.md` into that file so they persist across sessions.
