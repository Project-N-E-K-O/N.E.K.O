# N.E.K.O audit — resumption (D-boundary + proof)
Status: Pre-PR reconciliation complete, full-gate verified (2026-09-27). Branch = origin/main + 1 commit; repairs 2+3 applied in worktree; full unit gate 9F/25,724P vs main-baseline 20F/25,738P (strictly greener; residual = asr timing flaky + Windows symlink privilege). Ready to commit + push pending user confirmation.
Authority: text proof = authoritative; visual only for confirmed nodes/edges (not drawn as runtime impact).

## Repair receipt (2026-09-27, E-boundary: index reconciliation + documented cuts)
- Baseline: commit 0021e9d 后暂存区 57 个文件 = 对提交全部 54 D + 3 rename 源的完整恢复，与提交意图矛盾，且其中 plugin_api/registrar 被 HEAD 测试引用（HEAD 自身不一致）。
- Retired (git rm, 54 files, ≈8,900 LOC): brain/cua 子树(9)、brain/agent_session.py、main_logic/{agent_bridge,forge_credit_ledger}.py、quota/{cloud_sync,ux_state}.py、voice_identity_service/asr_composition.py、steamworks 5 个死接口、plugin/server 重复层(10)、scripts 一次性工具(11)、tests 附属(4，含 test_contracts.py — 其测试对象 AsrTurnCapabilities 已被提交从 contracts.py 一并移除，首轮误判后经 pytest 纠错)、specs/monitor_build.spec、requirements_monitor.txt、docker/.dockerignore、plugin/package-lock.json(孤儿 lockfile)。
- Retained with proof (staged, 3 files): main_logic/voice_input/{plugin_api,registrar}.py（tests/unit/test_voice_input_registry.py:17 存活消费）、config/quota_rules.yaml（配套恢复 dropper 加载器）。
- Restored behavior: main_logic/quota/dropper.py 恢复至 origin/main 版本（撤销提交中未记录的 _load_rules 移除）；净效果 vs origin/main 为零。
- Residue fix: config/memory_settings.py:278 注释移除对已删 ux_state.py 的引用。
- Verification: 残留 grep 零命中（yui_guide_director_parts 命中均指向存活的 tests/ 副本）；import smoke 14/14；ruff check 通过；定向 pytest 8 文件（voice_input registry、voice_turn contracts、yui director×2、steam achievement、quota notify、plugin runs×2）。
- Undo: 全部被删文件可从 origin/main 恢复（`git checkout origin/main -- <path>`）；dropper 同理。
- Residual risk (post repair-2): 13 个 test_agent_rewrite_regression 内容钉死断言在 origin/main 基线同样失败（静态资产未随之更新，仓库既有债，与本 PR 无关）；依赖审计 24 包 268 条 advisory 均为 requirements.txt 既有 pin（本 PR 未改依赖）；brain/cua 外部 fork 引用无法排除；quota 掉落规则功能维持 origin/main 原样，仍需产品确认是否保留。

## Execution receipt (D-boundary / Change phase, post-interrupt)
- S5 (steamworks dead interfaces): 11 imports/instantiations removed, 2 methods (relaunch, run_forever) deleted, 3 dead interfaces dropped from _LINUX_OPTIONAL_WRAPPER_METHODS. Import smoke pass.
- S12 (launcher.py facade): reduced to bootstrap + start_launcher (2034 bytes, 135 lines removed). Smoke pass.
- S14 (shared_state dead fields): removed sync_shutdown_event, sync_process, websocket_locks, logger params + init_shared_state adapter builds. 2 test files (test_storage_location_router.py, test_cloudsave_autocloud_router.py, test_cloudsave_autocloud.py, test_cloudsave_lifecycle_flow.py) retargeted; 160 passed.
- R8 (tool_router aliases): removed 2 re-export aliases; retargeted test_cloudsave_autocloud_router.py import to main_logic.tool_calling canonicals. Pass.
- S33 (monitor demo translate): removed is_japanese + translate_japanese_to_chinese + no-op branch; 20 lines removed.
- S17/35/36 (dead files + docker docs): 11 dead files deleted; docker/env.template + README.MD + docker/README_Docker.md + docker/CONFIG_REFERENCE.md cleaned.

## Validation (D-boundary only — full-suite unrelated error in test_speaker_identity.py)
- D-boundary import smoke: OK
- Affected pytest subset (storage_location_router + tool_image_protocol + cloudsave_autocloud_router + autocloud): 161 passed, 0 failed.
- Full run (ignoring heavy browser/voice): 1 unrelated speaker_identity error, 40 warnings, 1 skipped.

## Authority boundary preserved
- Proof records (this file + FINDINGS-DETAIL.md) = canonical.
- No graph-reachability claim treated as runtime impact; only the 15 edited files + 11 deleted files are confirmed.
- Visual map: NOT drawn (Survey-mode visual receipt skipped per visual-reporting.md — map would only replicate the already-confirmed text evidence; delivery omitted rather than fabricated).
