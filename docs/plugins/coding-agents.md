---
title: Build Plugins with Coding Agents
description: Build your first N.E.K.O plugin without writing code — let an AI coding agent set everything up, or use the Plugin studio in your installed N.E.K.O app.
---

# Build Plugins with Coding Agents

You do not need to write code. Pick an AI coding agent, copy one message to it, and the AI does the rest — you only click Allow and buttons in the app.

**What you can build**: for example, a plugin where the catgirl reminds you to drink water, a tool she can call in chat, or a small panel showing today's tasks — if you can describe it in one sentence, you can build it.

## 1. Pick a route

<CodingAgentOnboarding section="routes" />

## 2. Choose your agent

<CodingAgentOnboarding section="agent" />

## 3. Copy this message and send it to the AI

Pick a route on the card (A or B) first, then copy.

<CodingAgentOnboarding section="prompt" />

## 4. Route A: let the AI guide you from scratch

1. **Open an empty folder** in your agent (anywhere — a dedicated folder for this project).
2. Send it the "Route A" message above.
3. Click Allow whenever the AI asks to run something; it downloads, installs, and configures everything itself, which takes a while. When it gives you a URL, open the web page — the first time, fill in a model API key in the UI when prompted.
4. In the web plugin manager, click **Start** to try your plugin; every later change reloads automatically.
5. Next time you want to continue, just tell the AI "start N.E.K.O for me".

![The plugin page has Start and Reload buttons](/images/plugins/onboarding/neko-plugin-detail-en.png)

## 5. Route B: use your installed N.E.K.O

1. Install Git first: on the [official download page](https://git-scm.com/downloads), pick your OS and run the installer.
2. Open the N.E.K.O plugin manager, go to **Plugin studio** in the sidebar, and click **New plugin**: enter a name and ID, pick a parent folder, click **Preview project** to confirm the standard files, then **Create plugin**. **Remember where this project folder is.**

   ![Creating a plugin in Plugin studio and previewing the standard files](/images/plugins/onboarding/neko-studio-new-en.png)

3. **Open this plugin project folder** in your agent.
4. Send it the "Route B" message above.
5. Click **Start** on the plugin page to try it; after each change the AI makes, click **Reload**. If something fails, paste the error back to the AI as-is.

![The plugin page has Start and Reload buttons](/images/plugins/onboarding/neko-plugin-detail-en.png)

## 6. Share it with others

On Route B, click **Build package** in the studio; on Route A, just ask the AI to package it. Either way you get a `.neko-plugin` file you can send to friends — they click **Import** on the Plugins page to install it. Publishing to the Market needs a source build, so only Route A can publish directly — see [Publish a Plugin](/plugins/cli).

![Friends install your .neko-plugin package via Import on the Plugins page](/images/plugins/onboarding/neko-import-dialog-en.png)

> If the new project has no AGENTS.md, your N.E.K.O version is too old — update to the latest version and try again.

---

**Running a source checkout or prefer to build manually?** Read [Getting Started with Plugin Development](/plugins/plugin-development) or the [Quick Start](/plugins/quick-start).
