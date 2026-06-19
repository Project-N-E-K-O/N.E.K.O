# neko_roast Agent Rules

This file is for IDE agents and future contributors working inside `plugin/plugins/neko_roast`.

## Hard Rules

- Do not bulk-copy the large `bilibili_danmaku` / `bilibili_dm` implementations into this plugin. Reusing an old capability is allowed only by extracting it into a small, tested module (e.g. the absorbed `DanmakuListener` now living in `modules/bili_live_ingest/danmaku_core.py` + `livedanmaku.py`) — never paste the big files wholesale.
- Add new capabilities as modules, not as large inline blocks in `__init__.py`.
- New live-event handlers subscribe via `ctx.event_bus.subscribe(type, handler, owner=self.id)` in `setup` (and unsubscribe in `teardown`); the handler receives a `LiveEvent` and must route output through the pipeline / `neko_dispatcher`, never call `plugin.push_message` directly. `live_events` (subscribes `"danmaku"` / `"gift"` / `"super_chat"` / `"guard"` for window selection) is the reference subscriber. See docs/development.md「直播事件中枢（EventBus）」.
- Live input and developer sandbox input must share `core/pipeline.py`.
- `developer_tools_enabled` is the only developer-mode master switch; do not reintroduce separate chat-tool or sandbox-debug switches.
- Developer-mode gating must be enforced in backend actions/runtime, not only by disabling Hosted UI buttons.
- Do not bypass `core/safety_guard.py`.
- All NEKO output must go through `adapters/neko_dispatcher.py`.
- The roast instruction text is built in `modules/avatar_roast/build_request()` (`InteractionRequest.prompt_text`); `adapters/neko_dispatcher.push_roast()` must consume `prompt_text` and must not re-assemble the roast prompt itself.
- Never let the model describe an avatar it cannot see: when `ViewerIdentity.avatar_vision_ok` is False (default avatar / animated-decode failure / fetch failure), the roast must fall back to name / avatar-META only.
- All viewer profile writes must go through `stores/viewer_store.py`.
- All audit records must go through `stores/audit_store.py`.
- Login credentials must only be stored via `stores/credential_store.py` (Fernet-encrypted on disk). Never write credentials / cookies / tokens to audit, log, config, or UI — only echo uid / username / logged-in state.
- Do not write raw private user data, cookies, tokens, or raw payloads to logger.
- When adding UI text, update all 8 locale files.
- Python commands must be run through `uv run`.

## Documentation Required For New Features

Every new feature or module must include developer-facing documentation in `docs/`.

Use one of these patterns:

- Small change: update `docs/development.md`.
- New module or substantial workflow: add `docs/modules/<module_id>.md`.
- User-facing workflow change: update `docs/quickstart.md` if needed.

Always check whether the same change also requires updates to:

- `README.md` for plugin positioning, current focus, or explicit non-goals.
- `docs/README.md` for document index or responsibility changes.
- `docs/development.md` for long-term development rules, boundaries, current implementation snapshots, sandbox semantics, validation results, or follow-up entry points.
- `docs/quickstart.md` for user-facing workflow, page, button, or operation-order changes.
- `AGENTS.md` when future agent behavior or hard rules change.

The documentation must explain:

- What the feature does.
- Which module owns it.
- Which contracts, stores, entries, and UI surfaces it touches.
- How it goes through safety guard and pipeline.
- What data it reads or writes.
- How to test it.
- Known limitations and rollback/degrade behavior.

If a feature has no matching documentation, treat the implementation as incomplete.

## Required Checks

Run at least:

```powershell
uv run pytest plugin/tests/unit/plugins/test_neko_roast_contracts.py plugin/plugins/neko_roast/tests/test_smoke.py
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```
