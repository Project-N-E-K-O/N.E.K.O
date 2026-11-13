# Agent System

The Agent system (named NekoClaw in the interface) is how the character gets things done outside the conversation. The main dialogue model decides whether something is to be done and says so with a tool call. The NekoClaw host inside the Agent Server keeps the record, decides who does the work, runs it, and reports back. Nothing analyses a finished turn on its own initiative: the former per-turn analyzer and its execution channels have been removed.

Two terms are used throughout: a **commission** is one thing the character handed over, and a **task agent** is an execution session that works on a commission in several steps. The interface shows a task agent to the user as a "familiar"; code, routes, and field values use `task_agent`.

## Runtime components

```text
Main Server                                   Agent Server (:48915)
┌───────────────────────────┐                 ┌──────────────────────────────────┐
│ LLMSessionManager         │  HTTP           │ NekoClaw host (nekoclaw/host/)   │
│  tools: dispatch_task     │ ──────────────> │  ├─ task brain                   │
│         query_tasks       │ <── receipt ─── │  │   ledger · decision model     │
│ cross_server.py           │                 │  ├─ task agent manager           │
│ MainServerAgentBridge     │  ZeroMQ         │  ├─ capability gateway           │
│ pending_agent_callbacks   │ ─ turn check ─> │  └─ permissions                  │
│                           │ <── reports ─── │ embedded plugin service (:48916) │
└───────────────────────────┘                 └──────────────────────────────────┘
```

There is one host per Agent process. Startup only registers how the host reads the process's switches (`app/agent_server/nekoclaw_mount.py`). The ledger file, the agent runtime, and the gateway listener are created the first time they are needed.

## The two task tools

While the master switch and at least one capability switch are on, `main_logic/core/tool_calling.py` registers two built-in tools for the main dialogue model. Each takes one natural-language sentence and no identifier; which commission a sentence is about is worked out by the host. The definitions are in `config/prompts/prompts_task_tools.py`.

| Tool | Argument | Use |
|---|---|---|
| `dispatch_task` | `task` | Hand over something to do. Additions, changes of mind, stop requests, and the user's answer to a pending question also go through it |
| `query_tasks` | `question` | Ask what is under way, how a commission is going, or whether something can be done. An empty question lists what is under way |

The handlers call `POST /nekoclaw/dispatch` and `POST /nekoclaw/query` on the Agent Server and wait at most 2.5 seconds for the whole request. If the Agent Server cannot be reached, or turns the request down without acting on it (a 4xx status), the tool result states that the task was not handed over. If the request went out and no answer came back in time, or the Agent Server answered with an error of its own (a 5xx status), it states that it is not known whether the task was handed over (the Agent Server may be carrying it out all the same) and tells the model to check with a query instead of dispatching again. Proactive chat requests carry neither tool.

## Inside the host

### Task brain

- **Ledger.** One SQLite file, `<data root>/nekoclaw/ledger.db`, holds the commissions, their events, an outbox of reports, and task preferences. A commission is `queued`, `working`, `waiting_user`, `done`, `blocked`, `failed`, or `cancelled`. The status changes in a single function; `done` and `cancelled` are final, so a late result cannot overwrite a stop.
- **Dispatch, first round (waited for).** Local matching decides whether the sentence is a stop request, an answer, an addition, or a repeat of something under way, whether keywords or BM25 find candidate plugins, and whether a task agent can be started. The receipt says "already under way", "nothing can do this" (with the reason), or "accepted". No model is called, except for one small-model screen when nothing matches locally and no task agent can be started. After one second the receipt is "received" and the conclusion follows as a report.
- **Dispatch, second round (in the background).** The decision model (`nekoclaw/host/decision.py`) selects a plugin entry and fills in its arguments with one model call. When all plugin descriptions together exceed 3000 tokens, a coarse stage first keeps the union of keyword hits, the ten best BM25 matches, and a small-model screen. Plugins come first: a task that one entry can finish is called directly through the plugin service's `/runs` protocol. If no entry fits, or the task needs several steps, the commission goes to a task agent. The model can also answer that neither can do it, or that it is not a task at all; nothing is started then. An answer naming a plugin or entry that does not exist is not asked about again: it is treated as no entry fitting. If this round overturns the receipt, the character is told at once.
- **Query.** Never calls a model. Progress is read from the ledger, and "can this be done" reuses the first round's local matching without filing anything.

### Task agents

A task agent is one session of the built-in agent runtime (`nekoclaw/runtime/`), which runs in a subprocess started on demand. Each commission gets its own session. At most ten work at the same time; further commissions stay `queued`. A task agent runs on the agent model, receives the dispatched sentence and the user's own words rather than the conversation history, and can start task agents of its own. When it asks the user a question or needs a permission, the commission is `waiting_user` until the character passes the answer on with `dispatch_task`.

### Capability gateway and permissions

Besides its own file and command tools, a task agent reaches everything through the capability gateway (`nekoclaw/host/gateway.py`): an MCP service on the loopback address, started when the first task agent needs it, with one token per commission. It serves:

- plugin entries that are visible to routing, one tool each, called through the same `/runs` code as a direct call;
- the tools of MCP servers configured in the `mcp_adapter` plugin;
- the built-in tools for keyboard-and-mouse control and for browser control, described in the next section. Only task agents can call them.

Permissions are stored in `<data root>/nekoclaw/permissions.json`. Each category is `first_ask`, `always_ask`, `allow`, or `deny`. By default, plugin entries and file access inside the working directory are allowed, while reading or writing outside the working directory, commands, keyboard-and-mouse, the browser, and each MCP server ask on first use. `deny` also keeps a plugin or an MCP server out of direct calls. For keyboard-and-mouse and the browser, an approval given for a single request lasts until the task agent's current run is over, because their tools are single clicks and key presses.

### Built-in tools: keyboard and mouse, browser

Keyboard-and-mouse control and browser control are two groups of built-in tools (`nekoclaw/builtin/`, in the Agent process). A task agent calls them one step at a time from its own loop, next to its file and command tools, and sees the result of each step before it decides the next. Neither group runs a loop of its own, and no call to them asks a model. The definitions the model sees are in `config/prompts/prompts_nekoclaw_builtin.py`; the gateway lists them within a budget of their own (2400 tokens), apart from the plugin entries.

- **Keyboard and mouse.** One tool, `computer`, whose `action` argument is `screenshot`, `click`, `move`, `drag`, `scroll`, `type`, `key`, or `wait`. It works from screenshots: coordinates are integers from 0 to 999 on both axes whatever the size of the screen, and every call returns a screenshot taken after the action. A coordinate outside that range is refused.
- **Browser.** Fifteen tools named `browser_*`: navigate, take a snapshot, click, hover, fill a field, fill a form, type text, press a key, take a screenshot, list, select, open and close pages, wait for text, and handle a dialog. A page is read as a text snapshot of its accessibility tree in which every element carries a uid, and an action names its element by that uid. A uid that is not in the latest snapshot is refused. A snapshot is cut at 3000 tokens and says where to read on from. Only `http://` and `https://` addresses can be opened.

The browser is one the user has installed: Chrome, Edge, Brave, or Chromium, in that order; on Windows the system's default browser comes first when it is one of them. It is driven over its debugging protocol (the Chrome DevTools Protocol on a WebSocket), without a browser automation library. By default it is started with a profile directory of its own, `<data root>/nekoclaw/browser_profile`: a site logged into there stays logged in, and the user's everyday logins are not available. The user can instead have the tools drive the browser they are already using. That needs Chrome 144 or later, the user switches remote debugging on at `chrome://inspect/#remote-debugging`, and the browser asks for approval at each connection. The choice is kept in `<data root>/nekoclaw/browser.json`; a browser installed in an unusual place is named there as `executable`, or with the `NEKO_BROWSER_PATH` environment variable.

No browser is shipped with the application and none is downloaded at start. Playwright is a test dependency only, and `browser-use` is not a dependency. When no usable browser is installed, browser control is not ready and reports that none was found (`AGENT_BROWSER_NOT_FOUND`).

`computer` and `browser_take_screenshot` return pictures, so they are offered only to a task agent whose model can see pictures. A model counts as able to until its provider refuses a request because it carried a picture; that request is sent once more without pictures, and the model is recorded in `<data root>/nekoclaw/model_capabilities.json`. A task agent's context keeps the three most recent screenshots of each group.

There is one desktop and one browser. A commission occupies a group from its first call until its task agent's run ends, it starts waiting for the user, or it ends. A commission and the task agents it started share one occupation. Another commission that needs the group waits up to 120 seconds and is then told which commission has it.

When a commission begins or stops occupying a group, the Agent process sends a `builtin_activity` event, and the Main Server forwards it to every connected page as an `agent_builtin_activity` WebSocket message. The pages pass it on as a `neko-agent-builtin-activity` DOM event and draw nothing themselves. `GET /api/agent/builtin/activity` returns the current occupations, and `POST /api/agent/builtin/stop` stops the commission that holds one; both are described in [Agent REST API](/api/rest/agent).

## Reports back to the conversation

Every report is written to the ledger's outbox first and then sent to the Main Server over ZeroMQ. A failed send is retried after one second, doubling up to five minutes. Reports that an earlier process left unsent go out when the host is next used.

| Outbox kind | Meaning | Sent as |
|---|---|---|
| `task_result` | How a commission ended | `task_result` event |
| `question` | A commission waits for the user's answer or approval | `proactive_message` event |
| `correction` | The second round or the turn check found that what the character said does not hold | `proactive_message` event |
| `receipt` | Conclusion of a first round that outlasted its one-second budget | `proactive_message` event |

The Main Server queues a report on the matching `LLMSessionManager` and delivers it in one of the existing modes: `proactive` wakes the character to speak, `passive` rides along with her next turn, and a `silent` result is not reported to her at all. Text sessions can deliver at once; voice sessions may defer until an injection or hot-swap boundary. The turn in which she relays a report is not sent to the turn check below.

A dialogue session is replaced every few turns and keeps no task record. Each new session calls `GET /nekoclaw/brief` and is told what is still under way. Every change of a commission is also sent as a `task_update` event for the [Task HUD](/architecture/task-hud-system).

## Turn check

When a turn that the user started ends and the character called no tool that acts (memory recall, knowledge lookup, and `query_tasks` do not count), `main_logic/cross_server.py` sends a `turn_check_request` with the user's words and her reply. `publish_turn_check_reliably()` waits briefly for `turn_check_ack` and retries once. Turns she started herself, agent-callback turns, and image-only turns are not sent.

The host first applies a gate that costs nothing: the turn is skipped when the input-time intent score is known and below 0.2, no plugin keyword occurs in the user's words, and every routable plugin declares keywords. A turn that passes costs one small-model call with one of four verdicts.

| Verdict | What follows |
|---|---|
| Nothing to do, or she declined on purpose | Nothing |
| She said it cannot be done, but local matching finds a way | She is told and corrects herself |
| She agreed to do it but did not dispatch | It is dispatched for her, and she is told |
| She said it is done, but the ledger has no such commission | She is told and corrects herself |

The check has no authority of its own. A commission is filed only because the character dispatched it or agreed out loud.

## Transport map

HTTP carries control and queries on `127.0.0.1:48915`; the embedded plugin service listens on `127.0.0.1:48916`. Events use synchronous ZeroMQ sockets on background receive threads, which also works with the Windows Proactor event loop.

| Default address | Pattern | Direction | Purpose |
|---|---|---|---|
| `tcp://127.0.0.1:48961` | PUB / SUB | Main → Agent | Session and lifecycle events |
| `tcp://127.0.0.1:48963` | PUSH / PULL | Main → Agent | Reliable `turn_check_request` queue |
| `tcp://127.0.0.1:48962` | PUSH / PULL | Agent → Main | ACKs, status, task updates, and reports |

The ports can be overridden with `NEKO_ZMQ_SESSION_PUB_PORT`, `NEKO_ZMQ_ANALYZE_PUSH_PORT`, and `NEKO_ZMQ_AGENT_PUSH_PORT`. Agent → Main events have no HTTP fallback. Reports wait in the outbox until the bridge takes them; `task_update` and status events are best effort.

## Capability state

The authoritative state is exposed through Main Server proxies under `/api/agent/*`. The frontend changes state through `/api/agent/command` and refreshes it through `/api/agent/state`; it should not treat local checkbox state as authoritative.

| State | Meaning |
|---|---|
| `analyzer_enabled` / UI `agent_enabled` | Master switch |
| `computer_use_enabled` | Keyboard-and-mouse tools for task agents |
| `browser_use_enabled` | Browser tools for task agents |
| `user_plugin_enabled` | Plugin entries, for direct calls and through the gateway |

Managers and the Agent Server initialize these switches off. Persisted runtime intent is restored after the first real `greeting_check`, so "off at construction" does not reset the user's choice on every page load. Keyboard-and-mouse and browser control additionally require the agent model's connectivity check to pass (`app/agent_server/model_probe.py`). Keyboard-and-mouse also requires that input can be sent on this machine, and browser control that a usable browser is installed.

## Stopping, limits, and restarts

- Stopping writes `cancelled` first, then cancels the plugin run (`POST /runs/{run_id}/cancel` on the plugin service) or closes the task agent's session. `POST /admin/control` with `end_all`, and turning the master switch off, stop every commission under way.
- The desktop and the browser are each occupied by one commission at a time, as described under the built-in tools; within an occupation, calls are carried out one after the other. Stopping a commission cancels the call under way and frees what it occupied. A click or a navigation that was already sent is not undone.
- A plugin may return `deferred: true`. The commission stays `working` until `/api/agent/tasks/{task_id}/complete` is called, or fails after one hour.
- When a host starts after a restart, commissions the previous process left `queued`, `working`, or `waiting_user` are marked `failed` and reported with the character's next turn. Deferred commissions are kept until their deadline.
- The Agent process keeps ended tasks in its HUD view for five minutes.

## Implementation map

| Concern | Current implementation |
|---|---|
| Task tools and their handlers | `main_logic/core/tool_calling.py`, `config/prompts/prompts_task_tools.py` |
| Turn-end trigger for the turn check | `main_logic/cross_server.py` |
| ZeroMQ bridge and ACK/retry | `main_logic/agent_event_bus.py` |
| Dispatch, query, turn check | `nekoclaw/host/task_brain.py` |
| Plugin selection and the decision model | `nekoclaw/host/selection.py`, `decision.py` |
| Ledger, outbox, reports | `nekoclaw/host/ledger.py`, `outbox.py`, `reports.py` |
| Task agents and the agent runtime | `nekoclaw/host/task_agents.py`, `nekoclaw/runtime/` |
| Capability list, gateway, permissions | `nekoclaw/host/capabilities.py`, `gateway.py`, `permissions.py` |
| Keyboard-and-mouse and browser tools | `nekoclaw/builtin/`, `config/prompts/prompts_nekoclaw_builtin.py` |
| Occupation of the desktop and the browser; which models see pictures | `nekoclaw/host/leases.py`, `model_capabilities.py` |
| Calls to plugin entries (`/runs`) | `nekoclaw/host/plugin_runs.py` |
| Host mounting, HUD view, switches and events of the built-in tools, agent model probe | `app/agent_server/nekoclaw_mount.py`, `nekoclaw_builtins.py`, `model_probe.py` |
| Agent lifecycle and HTTP API | `app/agent_server/api_runtime.py`, `api_routes.py` |
| Result delivery to chat | `main_logic/core/proactive.py` |

See [Agent REST API](/api/rest/agent) for the public control endpoints and [Agent Server API](/api/agent-server) for the internal routes.
