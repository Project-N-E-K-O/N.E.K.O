# AI-Assisted Development

N.E.K.O. ships a `.agent/` directory in the repository root. AI coding assistants that support this convention — such as **Claude Code**, **Cursor**, **Windsurf**, and others — automatically load the rules and skills defined inside, so the AI follows the project's coding standards out of the box.

## Directory structure

```
.agent/
├── rules/
│   └── neko-guide.md        # Core development rules (always loaded)
└── skills/                  # Task-specific skill sets
    ├── i18n/
    ├── frontend-design/
    ├── vrm-physics/
    ├── tts-error-reporting/
    └── ...                  # ~20 skills in total
```

## Rules

The file `.agent/rules/neko-guide.md` is marked `trigger: always_on`, meaning any compatible AI agent will load it at the start of every session. It covers:

- **i18n**: All 6 locale files must be updated together.
- **`uv run`**: Always use `uv run` instead of the system Python.
- **Code symmetry**: Parallel providers must follow symmetric structure.
- **Core layer purity**: No provider-specific logic in `core.py`.
- **No numeric suffixes**: Extract shared logic into methods instead.
- **Electron vs. dev mode**: Frontend changes must work in both environments.

These rules mirror what is in `CLAUDE.md` (for Claude Code specifically) and the project's [Code Style](/contributing/code-style) guide.

## Skills

Each subdirectory under `.agent/skills/` contains a `SKILL.md` that defines:

- **When the skill activates** — e.g., when editing i18n files, working on VRM physics, or building frontend UI.
- **Domain knowledge** — references, patterns, and gotchas specific to that area.
- **Step-by-step workflows** — how to approach common tasks in that domain.

Skills are loaded on demand by the AI agent when the current task matches their trigger conditions.

## For contributors

If you use an AI coding assistant to contribute to N.E.K.O., the `.agent/` directory means it will automatically:

1. Follow the project's code style and architectural rules.
2. Update all 6 i18n locales when touching translation strings.
3. Use `uv run` for any Python execution.
4. Respect the Electron vs. dev mode distinction.

No additional setup is required — just open the project and start coding.

## For other AI tools

The `.agent/` convention is an emerging standard. If your AI tool does not support it natively, you can still point it to `.agent/rules/neko-guide.md` manually as a system prompt or project context file. The content is plain Markdown and tool-agnostic.
