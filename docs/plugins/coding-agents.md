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

## How to open Plugin Manager

Both routes use the plugin manager. Open it from the N.E.K.O main window:

![Opening Plugin Manager from the N.E.K.O main window](/images/plugins/onboarding/neko-open-plugin-manager-en.png)

1. Hover over the catgirl and click the round paw button that appears.
2. In the popup, turn on "NekoClaw Master Switch" and "User Plugin".
3. Hover over the "User Plugin" row and click the "Management Panel" button that appears — Plugin Manager opens in a new window.

## 4. Route A: let the AI guide you from scratch

1. **Open an empty folder** in your agent (anywhere — a dedicated folder for this project; see the official tutorials in your agent's card in section 2 for how).
2. Send it the "Route A" message above.
3. Click Allow whenever the AI asks to run something. It downloads and installs everything itself — be patient, this takes a while.
4. Open the URL it gives you. The first time, fill in a model API (if you don't have an API key yet, you can pick "Free Version" as the page's top tip says):

   ![Filling in the model API on first open](/images/plugins/onboarding/neko-api-setup-en.png)

   1. Pick a core API provider.
   2. Enter your API key.
   3. Click "Save Settings".

5. Open Plugin Manager (see [How to open Plugin Manager](#how-to-open-plugin-manager)).
6. In the plugin list, click your plugin:

   ![Clicking your plugin in the plugin list](/images/plugins/onboarding/neko-plugin-list-en.png)

   1. Click your plugin card to open its detail page.

7. Click "Start" to try it:

   ![The Start and Reload buttons on the plugin detail page](/images/plugins/onboarding/neko-plugin-detail-en.png)

   1. Click "Start" to run the plugin.
   2. Changes reload automatically; click "Reload" here only if a change didn't take effect.

8. After that, just tell the AI what you want changed; next time, say "start N.E.K.O for me".

## 5. Route B: use your installed N.E.K.O

1. Install Git first (the Route B card has the official download link — run the installer).
2. Open Plugin Manager (see [How to open Plugin Manager](#how-to-open-plugin-manager)).
3. Click "Plugin studio" in the sidebar, then "New plugin":

   ![The Plugin studio entry and the New plugin button](/images/plugins/onboarding/neko-studio-entry-en.png)

   1. "Plugin studio" in the sidebar.
   2. The "New plugin" button at the top right.

4. Fill in the name and ID, and click "Choose folder" to pick where the project goes:

   ![Filling in the plugin name, ID and folder](/images/plugins/onboarding/neko-studio-new-en.png)

   1. Plugin name (anything you like).
   2. Plugin ID (starts with a lowercase letter).
   3. "Choose folder" picks where the project is saved.

5. Click "Preview project" to confirm the standard files (you will see AGENTS.md), then click "Create plugin":

   ![Previewing the standard project files and creating the plugin](/images/plugins/onboarding/neko-studio-preview-en.png)

   1. The boxed AGENTS.md and CLAUDE.md in the preview are the guides the AI will read.
   2. Click "Create plugin" when it looks right.

6. Done — **remember where the project folder is**:

   ![The project info and source directory after creation](/images/plugins/onboarding/neko-studio-created-en.png)

   1. "Source directory" is the project folder location.
   2. "Open folder" shows the files inside.
   3. "Open in plugin manager" jumps to the plugin detail page.

7. **Open this project folder** in your agent and send it the "Route B" message above.
8. Click "Open in plugin manager" to reach the plugin detail page, then "Start" to try it; after each change the AI makes, click "Reload":

   ![The Start and Reload buttons on the plugin detail page](/images/plugins/onboarding/neko-plugin-detail-en.png)

   1. Click "Start" to run the plugin.
   2. After each change the AI makes, click "Reload" to see it.

9. If something goes wrong, open the "Logs" tab and copy the error to the AI as-is:

   ![Copying an error from the plugin logs](/images/plugins/onboarding/neko-plugin-logs-en.png)

   1. Click the "Logs" tab.
   2. Copy the lines marked ERROR and send them to the AI.

## 6. Share it with others

**Route B**: on your project page in the studio, click "Build package":

![The Build package button in the studio](/images/plugins/onboarding/neko-studio-package-en.png)

1. Click "Build package" to get a `.neko-plugin` file.

**Route A**: just ask the AI to package it.

A friend who gets the `.neko-plugin` file clicks "Import" in Plugin Manager and drops it in:

![The Import button on the Plugins page](/images/plugins/onboarding/neko-import-en.png)

![Dropping a plugin package into the import dialog](/images/plugins/onboarding/neko-import-dialog-en.png)

1. Click "Import" on the Plugins page.
2. Drop the `.neko-plugin` file into the box, or click to choose it.

Publishing to the Market needs a source build — only Route A can publish directly; see [Publish a Plugin](/plugins/cli).

> If the new project has no AGENTS.md, your N.E.K.O version is too old — update to the latest version and try again.

---

**Running a source checkout or prefer to build manually?** Read [Getting Started with Plugin Development](/plugins/plugin-development) or the [Quick Start](/plugins/quick-start).
