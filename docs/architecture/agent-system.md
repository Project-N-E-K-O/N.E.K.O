# Agent System

The agent system enables N.E.K.O. characters to perform background tasks — browsing the web, controlling the computer, calling external tools, and triggering local plugins — triggered by conversation context.

## Architecture

```
Main Server                          Agent Server
┌────────────────┐                  ┌─────────────────────────────┐
│ LLMSession     │                  │ DirectTaskExecutor           │
│ Manager        │  ZeroMQ          │   ├── _assess_mcp()         │
│   │            │ ──────────────>  │   ├── _assess_browser_use() │
│   │ agent_flags│  PUB/SUB         │   ├── _assess_computer_use()│
│   │            │                  │   └── _assess_user_plugin() │
│   │ callbacks  │ <──────────────  │                              │
│   │            │  PUSH/PULL       │ Priority: MCP > BU > CU > UP │
└────────────────┘                  │                              │
                                    │ Adapters:                    │
                                    │   ├── McpRouterClient        │
                                    │   ├── BrowserUseAdapter      │
                                    │   ├── ComputerUseAdapter     │
                                    │   └── POST /plugin/trigger   │
                                    │                              │
                                    │ Support:                     │
                                    │   ├── Analyzer               │
                                    │   └── Deduper                │
                                    └─────────────────────────────┘
```

## Capability flags

Agent capabilities are toggled via flags managed through the `/api/agent/flags` endpoint:

| Flag | Default | Description |
|------|---------|-------------|
| `agent_enabled` | false | Master switch for agent system |
| `mcp_enabled` | false | Model Context Protocol tool calls |
| `computer_use_enabled` | false | Screenshot analysis, mouse/keyboard |
| `browser_use_enabled` | false | Web browsing automation |
| `user_plugin_enabled` | false | Local plugin execution via plugin system |

## Task execution pipeline

The `DirectTaskExecutor` (in `brain/task_executor.py`) is the core engine. It replaces the older two-step Planner→Executor flow with a unified **parallel assessment + priority execution** model:

1. **Trigger**: The main server detects an actionable request in conversation and publishes an analyze request via ZeroMQ.

2. **Parallel Assessment**: The executor runs **all four assessors concurrently** (using `asyncio.gather`):
   - `_assess_mcp()` — Evaluates whether any MCP tool can handle the request; returns `tool_name` + `tool_args`
   - `_assess_browser_use()` — Evaluates whether browser automation is appropriate
   - `_assess_computer_use()` — Evaluates whether desktop GUI automation is appropriate
   - `_assess_user_plugin()` — Evaluates whether a local plugin can handle it; returns `plugin_id` + `entry_id`

3. **Priority Selection**: The first successful assessment wins, in order: **MCP → Browser Use → Computer Use → User Plugin**.

4. **Execute**: The selected adapter runs the task:
   - `_execute_mcp()` — Calls the MCP tool via `McpRouterClient`
   - `BrowserUseAdapter.run_instruction()` — Runs browser automation
   - `ComputerUseAdapter.run_instruction()` — Runs desktop automation
   - `_execute_user_plugin()` — `POST /plugin/trigger` with plugin_id and entry_id

5. **Analyze**: The `Analyzer` evaluates multi-turn conversation to identify completed tasks and determine response methods.

6. **Deduplicate**: The `Deduper` uses LLM to judge whether a new result is semantically duplicate of existing results.

7. **Return**: Results stream back to the main server via ZeroMQ PUSH/PULL as `task_result` or `proactive_message`.

## ZeroMQ socket map

| Address | Type | Direction | Purpose |
|---------|------|-----------|---------|
| `tcp://127.0.0.1:48961` | PUB/SUB | Main → Agent | Session events, task requests |
| `tcp://127.0.0.1:48962` | PUSH/PULL | Agent → Main | Task results, status updates |
| `tcp://127.0.0.1:48963` | PUSH/PULL | Main → Agent | Analyze request queue |

The event bridge uses NOBLOCK sends with fallback, ACK/retry mechanisms (0.5s timeout, 1 retry), and background delivery via asyncio event loop.

## MCP Client

The `McpRouterClient` (in `brain/mcp_client.py`) implements the MCP (Model Context Protocol) 2024-11-05 standard:

- **Transport**: HTTP + SSE (Server-Sent Events), JSON-RPC 2.0
- **Endpoint**: Configurable `MCP_ROUTER_URL` environment variable, default path `/mcp`
- **Caching**: `TTLCache(ttl=3s)` for tool listing, with failure cooldown (1s)
- **Tool catalog**: `McpToolCatalog` provides `get_capabilities()` for assessment

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/list",
  "params": {}
}
```

## Computer Use

The Computer Use adapter (`brain/computer_use.py`) implements a **Thought → Action → Code** loop inspired by the Kimi paradigm:

1. Capture screenshot of the desktop
2. Send to a vision-language model (VLM) for analysis
3. VLM returns structured output: `## Thought` / `## Action` / `## Code`
4. Execute the generated `pyautogui` code
5. Loop until `computer.terminate()` is called or max steps (50) reached

Key features:
- **Normalized coordinates**: `[0-999]` coordinate system mapped to actual screen resolution (`_ScaledPyAutoGUI`)
- **CJK text input**: Uses clipboard paste for CJK character support
- **Thinking mode**: Supports Claude extended thinking (reasoning field)
- **Image history**: Retains last 3 screenshots for context
- **Available actions**: `click`, `doubleClick`, `rightClick`, `moveTo`, `dragTo`, `scroll`, `write`, `press`, `hotkey`, `computer.wait(seconds)`, `computer.terminate(status, answer)`

Configuration for Computer Use models is available in the [Model Configuration](/config/model-config) reference.

## Browser Use

The Browser Use adapter (`brain/browser_use_adapter.py`) wraps the `browser-use` library for web automation:

- Navigate to URLs
- Extract page content
- Fill forms and click elements
- Take page screenshots

Key features:
- **Blue breathing glow**: Injected via CDP — a visual indicator showing the browser is being controlled by the agent
- **Session reuse**: A single `session_id` can run multiple sequential tasks
- **Timeout**: `asyncio.wait_for` with 300s default timeout
- **Keep-alive**: Browser window remains visible (`keep_alive=True`)
- **Overlay loop**: Background task re-injects the blue border every 1.5 seconds

## User Plugin

The User Plugin adapter triggers local plugins installed in the plugin system:

- Assessment returns `plugin_id` + `entry_id` for the matching plugin
- Execution sends `POST /plugin/trigger` with the plugin identifier and arguments
- Plugin execution timeout: 10 seconds (configurable in `plugin/settings.py`)

## Brain modules

| Module | File | Purpose |
|--------|------|---------|
| DirectTaskExecutor | `brain/task_executor.py` | Unified parallel assessment + priority execution |
| McpRouterClient | `brain/mcp_client.py` | MCP tool routing via HTTP+SSE |
| ComputerUseAdapter | `brain/computer_use.py` | Vision-based desktop automation |
| BrowserUseAdapter | `brain/browser_use_adapter.py` | Web browsing automation |
| TaskPlanner | `brain/planner.py` | Legacy two-step task planning (reference) |
| ConversationAnalyzer | `brain/analyzer.py` | Multi-turn conversation analysis, task identification |
| Processor | `brain/processor.py` | Data preprocessing/postprocessing |
| TaskDeduper | `brain/deduper.py` | LLM-based duplicate detection |
| MainBridge | `brain/main_bridge.py` | Inter-server event publishing bridge |
| AgentSessionManager | `brain/agent_session.py` | Session lifecycle and task tracking |
| CUA | `brain/cua/` | Computer Understanding Agent — closed-loop vision-based automation subsystem |

## API endpoints

See the [Agent REST API](/api/rest/agent) for the full endpoint reference.
