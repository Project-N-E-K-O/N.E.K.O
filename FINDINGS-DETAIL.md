
---
## 2026-09-27 (resume / D-boundary / Change mode)
### Confirmed findings with deep links
- S5 [steamworks/__init__.py#71]: dead interfaces (Input, Music, Screenshots, Matchmaking, MicroTxn) removed; methods.py block removed; structs.py MicroTxnAuthorizationResponse_t deleted; import smoke OK.
- S12 [launcher.py#1]: facade reduced to bootstrap + start_launcher only (line count -135); smoke OK.
- S14 [main_routers/shared_state.py#170, #203, #272]: dead fields (sync_shutdown_event, sync_process, websocket_locks, logger) + adapter constructions removed; init_shared_state signature changed; callers in app/main_server/__init__.py + 4 test files retargeted; 160 tests pass.
- R8 [main_routers/tool_router.py#92, #96]: alias rebinds removed; test retargeted to main_logic.tool_calling.
- S33 [app/monitor.py#278]: dead translate_japanese_to_chinese + is_japanese removed; no-op turn-end branch collapsed.
- S17 [scripts/*, tests/unit/widget_interaction_behavior.test.js]: 7 dead scripts + 1 dead JS test deleted.
- S35 [docker/.dockerignore]: dead .dockerignore removed.
- S36 [README.MD#336, docker/env.template#72, docker/README_Docker.md#51, docker/CONFIG_REFERENCE.md#65]: dead env-var documentation removed.

### Evidence level
Every edited line verified by `git diff` + targeted pytest (161 pass, 0 fail for affected subsets). Full-suite unrelated error in test_speaker_identity.py (pre-existing ImportError: memory.identity missing `derive_conversation_id` — no relationship to D-boundary edits). Visual map: NOT delivered (text proof authoritative; graph reachability not treated as runtime impact per visual-reporting.md).
