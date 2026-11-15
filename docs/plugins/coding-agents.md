---
title: Build Plugins with Coding Agents
description: Build your first N.E.K.O plugin without writing code — create a project in Plugin studio, send one message to any AI coding agent, then check, reload, and start it with buttons.
---

# Build Plugins with Coding Agents

You do not need to write code. N.E.K.O scaffolds the plugin project with instructions for the AI (AGENTS.md), the AI writes the code and runs everything itself, and you just click buttons.

**What you can build**: for example, a plugin where the catgirl reminds you to drink water, a tool she can call in chat, or a small panel showing today's tasks — if you can describe it in one sentence, you can build it.

## 1. Get two things ready

- **N.E.K.O installed**: the desktop app or the [Steam version](https://store.steampowered.com/app/4099310/__NEKO/).
- **One AI coding agent**: pick one below, install it, and sign in. If unsure, open its official tutorial first.

<CodingAgentOnboarding section="agent" />

## 2. Click New plugin in Plugin studio

Open the N.E.K.O plugin manager, go to **Plugin studio** in the sidebar, and click **New plugin**: enter a name and ID, pick a parent folder, click **Preview project** to see the standard files it creates (AGENTS.md, CLAUDE.md, and more), then click **Create plugin**. **Remember where this project folder is** — you need it next.

![Creating a plugin in Plugin studio and previewing the standard files](/images/plugins/onboarding/neko-studio-new-en.png)

## 3. Open the folder in your agent

Following the hints in your selected agent's card above, open the new plugin folder in that app and allow it to edit files and run actions.

## 4. Copy this message and send it to the AI

<CodingAgentOnboarding section="prompt" />

The AI reads AGENTS.md, writes the code, and runs the checks itself. Click Allow whenever it asks to run something; it will ask you in plain words if it gets stuck.

## 5. See it work in the studio

On the project detail page click **Open in plugin manager**, then click **Start** in the top-right corner of the plugin page to try it; after each new change, click **Reload**. To verify the project, the studio's **Publish** tab has **Run check**; to change features, just keep chatting with the AI.

![Studio project detail: refresh, open folder, open in plugin manager](/images/plugins/onboarding/neko-studio-project-en.png)

![The plugin page has Start and Reload buttons](/images/plugins/onboarding/neko-plugin-detail-en.png)

## 6. Share it with others

**Build package** in the studio exports a `.neko-plugin` file you can send to friends — they click **Import** on the Plugins page to install it. To publish to the Market, see [Publish a Plugin](/plugins/cli).

![Friends install your .neko-plugin package via Import on the Plugins page](/images/plugins/onboarding/neko-import-dialog-en.png)

> If the new project has no AGENTS.md, your N.E.K.O version is too old — update to the latest version and try again.

---

**Running a source checkout or prefer to build manually?** Read [Getting Started with Plugin Development](/plugins/plugin-development) or the [Quick Start](/plugins/quick-start).
