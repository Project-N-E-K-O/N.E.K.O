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
- Cost-bearing changes must be discussed and approved before implementation. Cost includes memory / CPU / background timers or queues, network polling, extra token / prompt / context usage, new dependencies, storage / IO growth, and core logic or architecture complexity.
- A cost discussion must list explicit decision points for maintainers to approve, not only a plain prose document. Cover the cost category and expected budget, affected modules / interfaces, alternatives and tradeoffs, the recommended option, rollout / rollback or degrade behavior, and required tests / observability.

## Documentation Required For New Features

Every new feature or module must include developer-facing documentation in `docs/`.
Use `docs/README.md` as the document responsibility matrix. Do not copy the same rule into multiple docs; update the canonical document and link to it from the others.

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
- If it has any cost impact, a "Decision Points" section that lists the options maintainers must approve before implementation.

If a feature has no matching documentation, treat the implementation as incomplete.

## Collaboration Rules

- Split work as Feature -> Slice -> PR. One PR should carry one reviewable slice, or one pure docs / tests / refactor purpose.
- Keep a PR at 20 files or fewer by default. If it exceeds 20 files, explain why in the PR description and prefer Draft until the shape is reviewed.
- Use Draft PRs for new base contracts, cross-module migrations, broad documentation governance, or work that will have independent follow-up PRs created from the updated target branch after this PR merges.
- Do not mix feature work with unrelated cleanup, panel rewrites, host/server changes, or old-plugin removal.
- Stacked PRs are prohibited: an open PR must not use another unmerged feature branch or PR as its base, and maintainers must not keep a cascading chain that requires fixes or conflict resolutions to be propagated downstream. A PR is still logically stacked when GitHub shows `main` as its base but its correctness, tests, review, merge, or rollback depend on another unmerged PR.
- Create every PR independently from the latest `main` or an explicitly designated release branch. If slices depend on one another, merge the prerequisite PR first, then create the next branch from the updated target branch.
- Every PR must be independently reviewable, testable, mergeable, and revertible on its declared target branch. Use backward-compatible contracts, feature flags, adapters, or a single cohesive PR instead of an unmerged dependency chain.
- If an open stack is discovered, stop extending and propagating it. Merge the PR closest to the target branch first; then recreate or retarget only the next slice from the updated target branch, verifying that its diff contains no accumulated ancestor changes.
- Only an explicit written maintainer approval for an emergency release or an indivisible migration may waive the no-stacked-PR rule; the exception must document merge order, risk, and the plan to return to independent PRs.
- Phase-specific governance: do not use documentation-governance PRs to implement runtime observability, Gift/SC/Guard behavior, `panel.tsx` refactors, or product changes.

## Review Gate

Protected Modules require core maintainer review. Use role-based ownership from `docs/development.md`「模块 Owner 与 Review Gate」; do not bind review requirements to a specific person.

Protected Modules include:

- Core architecture: `core/contracts.py`, `core/event_bus.py`, `core/module_registry.py`.
- Event layer: `modules/bili_live_ingest/**`, live protocol parsing, LiveEvent schema, event normalization.
- Selection: `modules/live_events/**`, score weights, cooldown window, event competition policy.
- Support events: `modules/live_support_events/**`, verified support evidence, provider-ID dedupe, combo state, bounded priority scheduling, short-thanks request contracts, and session task cleanup.
- Pipeline / output: `core/pipeline.py`, `core/safety_guard.py`, `adapters/neko_dispatcher.py`.
- Runtime: `core/runtime.py`, plugin actions, config persistence, hosted-ui context.
- Stores / privacy: `stores/viewer_store.py`, `stores/audit_store.py`, `stores/credential_store.py`.
- Dashboard shell: `ui/panel.tsx` navigation shell and cross-page orchestration; `ui/panel_components.tsx` display components; `ui/panel_state.ts` panel state contracts and defaults; `ui/panel_helpers.ts` labels and formatting helpers. `ui/panel_compat.tsx` is the full-feature single-file Hosted UI manifest entry for main-branch / older plugin-manager compatibility; regenerate it from the modular sources when the panel changes. Do not replace it with a minimal fallback shell for release packages, because that drops panel functionality.

Open contribution areas include docs, module docs, fixtures, tests, small read-only dashboard display changes, i18n sync, and non-core `config_schema()` changes. New contributors should start there unless a maintainer explicitly scopes a Protected Module change.

## Runtime Observability Checks

Runtime observability terms are canonical in `docs/runtime-observability.md`. For any PR touching runtime behavior, event handling, output, monitor, or dashboard visibility, agents and reviewers must check:

- New event paths explain their Runtime Timeline stage and Event Outcome.
- Expected non-output paths use a stable Skip Reason from `docs/runtime-observability.md`, or add a minimal new reason there first.
- Safety Guard and Dispatcher remain visible stages.
- Dashboard state is derived from runtime/audit facts, not raw payloads.
- No monitor, audit, or dashboard data exposes raw payloads, cookies, tokens, avatar bytes/base64, or unredacted private data.

## Reviewer Checklist

Reviewers should check at least:

- Scope: one Slice, clear files touched, no unrelated business/UI/test changes.
- Size: <=20 files, or a justified Draft / split plan.
- Architecture: EventBus / pipeline / safety guard / dispatcher / store / audit boundaries are preserved.
- Output: no module calls `plugin.push_message()` directly; NEKO output stays behind `adapters/neko_dispatcher.py`.
- Observability: event paths have stage/outcome/skip-reason coverage per `docs/runtime-observability.md`.
- Cost: cost-bearing changes were discussed before implementation and include explicit approved decision points.
- Privacy: no raw private data, cookies, tokens, avatar bytes/base64, or raw payloads go to logger, audit, config, or UI.
- Docs: documentation follows `docs/README.md` canonical source routing.
- Tests: required commands are listed, or the PR explicitly states it is docs-only.

## Required Checks

For docs-only PRs, state that no code tests were run because the change is documentation-only. For any PR touching Python, UI, i18n, contracts, config schema, manifest, or runtime behavior, run at least:

```powershell
uv run pytest plugin/plugins/neko_roast/tests -q
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

For packaged compatibility builds, also confirm `ui/panel_compat.tsx` keeps the full panel surface: it may import `@neko/plugin-ui`, but it must not contain relative imports, `window.NekoUiKit`, or `__modules`.
