---
title: "Agent Walkthroughs: Codex & DeepSeek Harness"
description: Step-by-step visual walkthroughs showing beginners how to create, validate, and run a N.E.K.O plugin from an empty folder with OpenAI Codex or DeepSeek Harness.
---

# Agent Walkthroughs: Codex & DeepSeek Harness

This page walks through building a complete **Water Reminder (`water_reminder`)** plugin from an empty folder using two representative coding agents: **OpenAI Codex** (desktop app / CLI) and **DeepSeek Harness (`dsh`)** (open-source local browser UI).

> Using Trae, Claude Code, Cursor, Qoder, CodeBuddy, or VS Code? Grab your agent's tailored steps and one-click prompt on [Build Plugins with Coding Agents](/plugins/coding-agents) — the conversation flow after pasting the prompt is identical.

<CodingAgentWalkthrough />

## Keep iterating in plain language

Once your first plugin runs, stay in the same agent session and ask for incremental upgrades in plain language:

- **Add user-configurable settings**: *"Add a configurable `interval_minutes` number setting in `plugin.toml` and `config.example.toml` so users can adjust it in Plugin Manager, then re-run `neko-plugin check`."*
- **Add an interactive panel**: *"Read `docs/plugins/hosted-ui.md` and add a simple Hosted TSX panel showing how many glasses of water I logged today, then run `check-hosted-tsx` and `neko-plugin check`."*
- **Prepare for the Plugin Market**: *"Run `uv run neko-plugin precheck water_reminder` to make sure all Market CI gates pass, then walk me through `docs/plugins/cli.md` to publish it."*
