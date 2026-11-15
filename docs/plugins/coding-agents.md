---
title: Build Plugins with Coding Agents
description: Build your first N.E.K.O plugin with Coding Agents such as Codex, DeepSeek Harness, Trae, Claude Code, and Cursor using a one-click neko-plugin CLI setup prompt.
---

# Build Plugins with Coding Agents

You do not need prior Python framework experience to create a N.E.K.O plugin. Pick the coding agent you already use, copy the **Setup Prompt**, and let your agent set up the repository and run the official `neko-plugin` CLI for you.

<CodingAgentOnboarding />

## What your coding agent handles automatically

When you paste the Setup Prompt, your agent follows the same workflow as experienced N.E.K.O plugin authors:

1. **Environment & repository check** — verifies `git` and `uv`, clones or reuses `Project-N-E-K-O/N.E.K.O`, runs `uv sync`, and checks `uv run neko-plugin --help`.
2. **Official CLI scaffolding** — runs `uv run neko-plugin init <plugin_id> --type plugin --name "<Name>"` to create `plugin/plugins/<plugin_id>/` with `plugin.toml`, `config.example.toml`, `__init__.py`, tests, and GitHub workflows.
3. **SDK-compliant implementation** — reads the local documentation under `docs/plugins/` and implements async `@plugin_entry` or `@llm_tool` handlers on `plugin.sdk.plugin`.
4. **Automated verification & packaging** — runs `uv run neko-plugin check`, `lint`, and `pytest` until zero errors remain, and builds a `.neko-plugin` archive whenever you want to import it into Plugin Center.

## Next steps

- Want to see every screen in detail? Open the [Codex & DeepSeek Harness Walkthroughs](/plugins/coding-agent-guide).
- Curious what the generated files mean? Read [Getting Started with Plugin Development](/plugins/plugin-development) or the manual [CLI Quick Start](/plugins/quick-start).
- Ready to share your plugin with everyone? Follow [Publish a Plugin to the Market](/plugins/cli).
