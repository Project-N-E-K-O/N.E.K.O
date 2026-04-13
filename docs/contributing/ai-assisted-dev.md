# AI-Assisted Development

N.E.K.O. provides built-in configuration for AI coding assistants. The project currently ships with configurations for two platforms, and you can adapt them for any other AI tool.

## Supported platforms

| Platform | Config location | Auto-loaded? |
|----------|----------------|--------------|
| **Claude Code** | `.agent/rules/`, `.agent/skills/`, `CLAUDE.md` | Yes — Claude Code natively reads `.agent/` and `CLAUDE.md` |
| **Cursor** | `.cursor/skills/` | Yes — Cursor natively reads `.cursor/` |
| **Other tools** | — | Manual setup required (see below) |

## `.agent/` directory (Anthropic convention)

`.agent/` follows the [Anthropic agent convention](https://docs.anthropic.com/en/docs/claude-code). **Claude Code** automatically loads these files; other tools do not.

```
.agent/
├── rules/
│   └── neko-guide.md        # Core development rules (always_on trigger)
└── skills/                  # ~20 task-specific skill sets
    ├── i18n/
    ├── frontend-design/
    ├── vrm-physics/
    └── ...
```

### Rules

`.agent/rules/neko-guide.md` is marked `trigger: always_on`, meaning Claude Code loads it at every session start. It covers:

- **i18n**: All 6 locale files must be updated together.
- **`uv run`**: Always use `uv run` instead of the system Python.
- **Code symmetry**: Parallel providers must follow symmetric structure.
- **Core layer purity**: No provider-specific logic in `core.py`.
- **No numeric suffixes**: Extract shared logic into methods instead.
- **Electron vs. dev mode**: Frontend changes must work in both environments.

### Skills

Each subdirectory under `.agent/skills/` contains a `SKILL.md` defining trigger conditions, domain knowledge, and step-by-step workflows. They are loaded on demand when the current task matches.

## Adapting for other AI tools

If your AI coding assistant does **not** natively support `.agent/`, follow these steps to bring the project rules into your tool:

1. **Read the core rules** — Open `.agent/rules/neko-guide.md` and copy its content into your tool's project-level prompt or rules file. For example:
   - **Windsurf**: Paste into `.windsurfrules`
   - **GitHub Copilot**: Paste into `.github/copilot-instructions.md`
   - **Other tools**: Use whatever "project context" or "system prompt" mechanism your tool provides.

2. **Browse the skills** — Look through `.agent/skills/` for domains relevant to your work. Each `SKILL.md` is self-contained Markdown — you can feed it to your AI tool as additional context when working in that area.

3. **Reference `CLAUDE.md`** — This file in the repo root contains a concise summary of the most critical rules. It is a good starting point if you only want to import one file.

The content is plain Markdown and tool-agnostic — the rules themselves apply regardless of which AI assistant you use.

## Key rules summary

Regardless of which AI tool you use, these are the non-negotiable project rules:

1. **i18n completeness** — Touching any translation string requires updating all 6 locale files (`en`, `ja`, `ko`, `zh-CN`, `zh-TW`, `ru`).
2. **`uv run` for Python** — Never use the system Python directly.
3. **Code symmetry** — If one provider has a pattern, all providers must follow it symmetrically.
4. **Core layer purity** — `core.py` must not contain provider-specific imports or logic.
5. **Dual-mode awareness** — Frontend changes must work in both the dev server (single window) and Electron (multi-window) environments.
