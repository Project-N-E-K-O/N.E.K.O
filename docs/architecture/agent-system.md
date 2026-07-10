# Agent System

The agent system enables N.E.K.O. characters to perform background tasks — browsing the web, controlling the computer, delegating to standalone agent channels, and calling external tools — triggered by conversation context.

## Architecture

```
Main Server                          Agent Server
┌────────────────┐                  ┌────────────────────┐
│ LLMSession     │                  │ TaskExecutor        │
│ Manager        │  ZeroMQ          │   ├── Planner       │
│   │            │ ──────────────>  │   ├── Processor     │
│   │ agent_flags│  PUB/SUB         │   ├── Analyzer      │
│   │            │                  │   └── Deduper        │
│   │ callbacks  │ <──────────────  │                      │
│   │            │  PUSH/PULL       │ Adapters:            │
└────────────────┘                  │   ├── Computer Use   │
                                    │   ├── Browser Use    │
                                    │   ├── OpenClaw       │
                                    │   ├── OpenFang       │
                                    │   └── User Plugin    │
                                    └────────────────────┘
```

## Capability flags

Agent capabilities are toggled via flags managed through the `/api/agent/flags` endpoint:

| Flag | Default | Description |
|------|---------|-------------|
| `agent_enabled` | false | Master switch for agent system |
| `computer_use_enabled` | false | Screenshot analysis, mouse/keyboard |
| `browser_use_enabled` | false | Web browsing automation |
| `user_plugin_enabled` | false | Plugin / Model Context Protocol tool calls |
| `openclaw_enabled` | false | OpenClaw standalone-agent channel |
| `openfang_enabled` | false | OpenFang standalone-agent channel |

## Task execution pipeline

1. **Trigger**: The main server detects an actionable request in conversation and publishes an analyze request via ZeroMQ.

2. **Plan**: The `Planner` decomposes the request into a task plan with ordered steps.

3. **Execute**: The `Processor` runs each step through the appropriate adapter:
   - **Computer Use** — Takes screenshots, analyzes them with vision models, performs mouse/keyboard actions
   - **Browser Use** — Navigates web pages, extracts content, fills forms
   - **OpenClaw / OpenFang** — Delegate the task to a standalone agent channel
   - **User Plugin** — Calls external tools via user-installed plugins (Model Context Protocol)

4. **Analyze**: The `Analyzer` evaluates whether the task goal has been achieved.

5. **Deduplicate**: The `Deduper` prevents redundant results from being sent.

6. **Return**: Results stream back to the main server via ZeroMQ PUSH/PULL.

## ZeroMQ socket map

| Address | Type | Direction | Purpose |
|---------|------|-----------|---------|
| `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent | Session events, task requests |
| `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main | Task results, status updates |
| `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent | Analyze request queue |

## Computer Use

The Computer Use adapter (`brain/computer_use.py`) enables vision-based computer interaction:

1. Capture screenshot of the desktop
2. Send to a vision model (e.g., `qwen3-vl-plus`) for analysis
3. Plan mouse/keyboard actions based on the visual understanding
4. Execute actions via `pyautogui`

Configuration for Computer Use models is available in the [Model Configuration](/config/model-config) reference.

## Browser Use

The Browser Use adapter (`brain/browser_use_adapter.py`) wraps the `browser-use` library for web automation:

- Navigate to URLs
- Extract page content
- Fill forms
- Click elements
- Take page screenshots

## OpenClaw

The OpenClaw adapter (`brain/openclaw_adapter.py`) delegates an actionable task to the OpenClaw standalone agent channel (internally referenced as `qwenpaw`).

## OpenFang

The OpenFang adapter (`brain/openfang_adapter.py`) delegates an actionable task to the OpenFang standalone agent channel.

The channel selection priority is defined in `brain/task_executor.py` as `_CHANNEL_PRIORITY = ["qwenpaw", "openfang", "browser_use", "computer_use"]`. Plugin / MCP tool calls (`user_plugin_enabled`) are dispatched through a separate path and are **not** part of `_CHANNEL_PRIORITY`.

## API endpoints

See the [Agent REST API](/api/rest/agent) for the full endpoint reference.
