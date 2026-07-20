# Agent Automation, QwenPaw, and Correction Feedback

> This page is maintained in English and [Simplified Chinese](/zh-CN/plugins/use-claw). There is no Japanese mirror yet. It documents the Agent routing currently implemented in this repository; it is not a plugin SDK tutorial or an installation guide for QwenPaw.

N.E.K.O can route Agent tasks to user plugins, browser automation, desktop automation, or an external Agent service. Channel selection, task execution, and correction feedback are separate layers.

## Current implementation status

| Capability | Status | Source area |
|---|---|---|
| User-plugin selection | Implemented | `brain/plugin_filter.py`, `brain/task_executor.py` |
| Unified Browser Use / Computer Use assessment | Implemented | `brain/task_executor.py` |
| QwenPaw integration under the compatibility name OpenClaw | Adapter implemented; service is external | `brain/openclaw_adapter.py`, `app/agent_server/channels/openclaw.py` |
| Task registry, state, and cancellation | Implemented | `app/agent_server/registry.py`, `app/agent_server/api_runtime.py` |
| Browser Use / Computer Use correction feedback | Implemented | `brain/task_executor.py`, `app/agent_server/api_runtime.py` |
| Equivalent correction for QwenPaw, OpenFang, or user plugins | Not implemented | Requires a new product and data-boundary design |

Agent Server is implemented as the `app/agent_server/` package, not a single-file monolith. Use the package modules listed above as source references.

## Channel selection

### User plugins use a separate two-stage path

User-plugin entry selection does not share one decision object with Browser Use, Computer Use, or QwenPaw:

1. When the combined plugin descriptions are below the configured token threshold, all plugins with Agent-visible entries go directly to Stage 2.
2. Above the threshold, Stage 1 runs BM25 and an LLM coarse screen in parallel, then unions their output with manifest regex `keywords` hits.
3. Stage 2 reads the remaining plugins' full descriptions and returns `plugin_id` plus runtime `entry_id`.
4. The host validates both IDs against the candidates shown in that assessment. It gives the LLM one correction retry, then forces `can_execute = false` if the result is still invalid.

Runtime `entry_id` comes from `@plugin_entry(id=...)` or dynamic entry registration. It is unrelated to `[plugin].entry = "module.path:ClassName"`, which only tells the host how to import the plugin class.

### Non-plugin channels share one assessment

`TaskExecutor` dynamically includes only available QwenPaw, OpenFang, Browser Use, and Computer Use channels in one LLM assessment. If more than one result says it can execute, the fixed priority is:

```text
QwenPaw > OpenFang > Browser Use > Computer Use
```

QwenPaw `/clear`, `/new`, `/stop`, and `/daemon approve` requests also have a dedicated magic-command classification path. Ordinary tasks still require availability checks and unified assessment.

## The QwenPaw / OpenClaw boundary

`OpenClawAdapter` is the compatibility-facing class name for the **external QwenPaw service**. This repository implements:

- configuration and health probing;
- adapters for the legacy Responses-compatible API and v2 console streaming API;
- a local sender-to-external-session mapping;
- task, stream, stop, and magic-command forwarding.

This repository does not ship the QwenPaw service process and cannot guarantee that it is installed, running, or authorized for the requested models and tools. QwenPaw enters the channel candidate set only when the health check reports ready. A configured loopback URL is an integration default, not a promise that N.E.K.O embeds the service.

“OpenClaw” is a compatibility name in current source, not a second repository-owned Agent implementation.

## Task state and cancellation

Agent Server keeps its task registry in process. `app/agent_server/api_runtime.py` exposes task queries and:

```text
POST /tasks/{task_id}/cancel
```

Cancellation is dispatched by task type: Computer Use receives a cancellation signal, Browser Use calls its cancel operation, and OpenFang or QwenPaw/OpenClaw forwards a remote stop request. Terminal task records are removed after the registry TTL; this is not a permanent task history.

Cancellation is cooperative and best effort. A local `cancelled` status cannot prove that an external service had not already performed an irreversible action.

## Implemented correction feedback

Correction currently supports only **Browser Use and Computer Use correcting each other**. After a task reaches a terminal state, a client can call:

```text
POST /api/agent/tasks/{task_id}/correction
```

```json
{
  "correct_tool": "browser_use",
  "correct_instruction": "This only needs webpage interaction; do not control the desktop.",
  "user_note": "Optional note"
}
```

The current endpoint requires:

- an original task type of `computer_use` or `browser_use`;
- status `completed`, `failed`, or `cancelled`;
- the other supported tool as `correct_tool`;
- correction context captured when the task was created;
- a non-blank `correct_instruction`.

### Storage and privacy

`TaskExecutor.record_tool_correction()` writes `correction_memory.json` in the active configuration directory. Stored events contain a truncated and redacted user request, normalized intent, recent context, wrong channel, corrected channel, and explanation. Passwords, tokens, cookies, email addresses, one-time codes, identity-number patterns, and common phone-number patterns are replaced before writing. The file is written atomically and permissions are tightened where supported.

This is a dedicated Agent-routing correction file. It is not character memory and not plugin SDK `bus.memory`. At most 300 events are kept; another submission for the same `task_id` updates the existing event.

### Retrieval

Before unified channel assessment, the code performs lightweight keyword matching over the current request, normalized intent, and recent context. It selects at most three relevant events and injects only the normalized intent, previous wrong choice, and confirmed correct channel. It is not vector search and does not inject the complete correction archive.

## Not implemented

The following are proposals, not current behavior:

- correction feedback for QwenPaw, OpenFang, user plugins, or a specific plugin `entry_id`;
- automatically turning natural-language criticism into a correction submission;
- a UI to review, export, delete, or partition corrections by user;
- embedding/vector or character-memory retrieval for corrections;
- synchronizing local correction events to QwenPaw/OpenClaw.

Any such extension first needs explicit user confirmation, identity isolation, retention and deletion rules, and an external synchronization boundary. It must not silently reuse character memory or plugin `bus.memory`.

## Troubleshooting order

1. Check the feature flag and runtime availability. Unavailable channels are not candidates.
2. Separate the user-plugin two-stage path from unified non-plugin assessment.
3. Inspect the task `type`, `status`, and captured correction context.
4. For QwenPaw, check N.E.K.O adapter configuration and external-service health independently.
5. If a correction is not reused, confirm it was written to `correction_memory.json` and that the new request shares matchable terms.

Anything beyond these boundaries must be labeled as a proposal rather than current integration status.
