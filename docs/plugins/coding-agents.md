---
title: Build Plugins with Coding Agents
description: Build your first N.E.K.O plugin with Coding Agents such as Codex, DeepSeek Harness, Trae, Claude Code, and Cursor using a one-click guided Setup Prompt.
---

# Build Plugins with Coding Agents

You do not need prior Python framework experience to create a N.E.K.O plugin. Pick the coding agent you already use, copy the **Setup Prompt**, and let the AI set up the repository and drive the official `neko-plugin` tooling for you.

<CodingAgentOnboarding />

## What your coding agent handles automatically

When you paste the Setup Prompt, your agent follows the same workflow as experienced N.E.K.O plugin authors:

1. **Environment & repository check** — verifies `git` and `uv` (installing them itself when missing), clones or reuses `Project-N-E-K-O/N.E.K.O`, syncs dependencies, and confirms the `neko-plugin` tooling is ready.
2. **Official scaffolding** — uses the official `neko-plugin` scaffold to create `plugin/plugins/<plugin_id>/` with `plugin.toml`, `config.example.toml`, `__init__.py`, tests, and GitHub workflows.
3. **SDK-compliant implementation** — reads the local documentation under `docs/plugins/` and implements async `@plugin_entry` or `@llm_tool` handlers on `plugin.sdk.plugin`.
4. **Automated verification & packaging** — runs the plugin checks, lint, and tests until zero errors remain, and builds a `.neko-plugin` archive whenever you want to import it into Plugin Center.

## Next steps

- Want to see every screen in detail? Open the [Codex & DeepSeek Harness Walkthroughs](/plugins/coding-agent-guide).
- Curious what the generated files mean? Read [Getting Started with Plugin Development](/plugins/plugin-development) or the manual [CLI Quick Start](/plugins/quick-start).
- Ready to share your plugin with everyone? Follow [Publish a Plugin to the Market](/plugins/cli).
