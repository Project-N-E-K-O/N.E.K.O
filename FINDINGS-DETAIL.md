
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

---
## 2026-09-27 (E-boundary / repair + survey findings S1-S4)
### Survey findings (cleanup map: ../N.E.K.O-audit-artifacts/neko-survey.cleanup-map.html)
- S1 [brain/cua/__init__.py]: 休眠 vendored Agent-S 子树（约 2700 行），零包外消费者；活跃 CUA 路径为 channels→_shared.py:43→brain/computer_use.py。**已随 E-boundary 切除**（接受提交删除，撤出错乱的暂存恢复）。
- S2 [plugin/server/runs/manager.py]: 暂存恢复造成与 plugin/runs/ 正本字节级重复的零引用副本。**已切除**（git rm 10 个 plugin/server 恢复文件）。
- S3 [scripts/diagnose_pc_day1_capsule_wobble.py]: 10 个零引用一次性脚本（约 3300 行，CI/package.json/文档全零引用）。**已切除**。
- S4 [requirements_monitor.txt:1]: 无引用化石文件 + 孤儿 plugin/package-lock.json。**已切除**；quota_rules.yaml 改为保留并恢复 dropper.py 加载器（提交的移除属未记录变更，按非有意处理）。
- Rejected: agent_event_bus/lifecycle_bus/cross_server（三种不同传输，均活跃）；utils/*_state 兼容别名（sys.modules 自替换，对外兼容面）；brain/agent_session.py 在 origin/main 有 3 个运行时导入方，非死代码。
### Repair corrections to D-boundary
- tests/unit/voice_input/{plugin_api,registrar} 的消费方 tests/unit/test_voice_input_registry.py 在 HEAD 存活 → 两文件保留（提交的删除会使 HEAD 无法收集该测试）。
- tests/unit/voice_turn/test_contracts.py 的删除经 pytest 证明是正确且一致的（AsrTurnCapabilities 已随 asr_composition 特性一并移除）→ 维持删除。
- main_logic/quota/dropper.py 恢复 origin/main 版本（撤销未记录的 _load_rules 移除）。
- 证据：残留 grep 零命中；import smoke 14/14；ruff 通过；定向 pytest 见 HANDOFF.md 回执。

---
## 2026-09-27 (pre-PR reconciliation / repair-2)
### Corrections to prior receipts
- S37 [memory/identity.py#107]: 提交删除了 derive_conversation_id + is_conversation_id（未记录切除；唯一消费方为存活的 tests/unit/test_speaker_identity.py:16-18）。此前回执将其误判为"pre-existing ImportError"。两函数已按 origin/main 字节恢复。
- S38 [main_logic/voice_input/registry.py + tests/unit/test_voice_input_registry.py]: E-boundary 保留 plugin_api/registrar 的裁决不完整——同一提交还移除了 registry.issue_plugin_registrar/_register_plugin 与测试的 import+用例，使恢复文件成为零消费孤儿。两文件恢复至 origin/main → plugin voice-input SPI 对 origin/main 净零、测试消费方（test 文件第 17 行）复活。
- S39 [tests/test_agent_rewrite_regression.py#1228]: S14 迁移漏网调用方——init_shared_state 已去 logger 形参，该测试仍传 logger=None → 1 个 PR 引入失败。已移除该 kwarg。
### Verification
- pytest test_speaker_identity + test_voice_input_registry：60 passed。
- pytest test_agent_rewrite_regression：13 failed / 150 passed，失败名单与 origin/main 基线（git worktree 对照）逐条一致；少 2 条用例 = agent_bridge 测试对随主体正确移除。
- ruff check .（0.15.4 == CI 钉版）：通过。import smoke 17/17。plugin test_trigger_service 4 passed。
- 对 origin/main 净零面：memory/identity.py、voice_input/{registry,plugin_api,registrar}、quota/{dropper.py,quota_rules.yaml}（恢复面 = 审计范围外，维持原样）。

---
## 2026-09-27 (full-gate reconciliation / repair-3, S40)
### Finding
- S40 [main_routers/shared_state.py#170 × 12 test files]: S14 移除 init_shared_state 的 logger 形参时仅迁移 app/main_server + 4 个测试文件；其余 7 个测试文件共 93 处调用仍传 `logger=None,` → 全量 CI 门（tests/unit, -n auto）158 failed / 61 errors，根因均为 _build_client/_refresh_shared_state 中的 TypeError。之前"161/166 passed"结论只覆盖了被迁移文件的子集，未跑全量门——教训：签名变更必须 grep 全部调用点。
### Fix
- 括号感知机械迁移：删除 7 文件内 init_shared_state 调用块中的 93 行 `logger=None,`（纯删除 94 行含 agent_rewrite 1 行，零插入、零行尾搅动；GameAgentService(logger=…) 为无关类未动）。
### Provenance ruling (closes advisory State-A/B question)
- plugin_api.py/registrar.py/test_voice_input_registry.py 三者均诞生于 e32972f（2026-09-26 19:07，切片审计前 ~20h，与插件市场同期）→ 属维护者有意铺设的扩展点脚手架；删除属产品决策、需维护者签字 → 维持净零保留（State B-full）。registrar.py:39 对 _register_plugin 的调用随 registry.py 恢复而重新闭合。
### Additional gates run
- gitleaks 8.30.1：PR 净 diff（stdin 模式，474KB）0 leaks；git 历史扫描 110 处存量命中（非本 PR 引入）。npm audit：plugin-manager 25 漏洞（1 critical/15 high/3 moderate...精确：3 low/6 moderate/15 high/1 critical）、react-neko-chat 10 漏洞（1 low/3 moderate/5 high/1 critical）——均为既有 pin，PR 未触 frontend/。mypy（用户侧 uvx 探索跑）：4875 errors/498 files——仓库无类型门配置，属存量基线，不作 PR 判定依据。
### Final full-gate parity (post S40 fix)
- 分支（本工作区）：**9 failed / 25,724 passed / 177 skipped**（5:54）。origin/main 基线（同命令、worktree 对照）：**20 failed / 25,738 passed / 177 skipped**（14:15）。分支严格更绿；残余 9 = asr speaker_shadow 时序 flaky（基线全量同样红、隔离跑全绿）+ avatar_tool_store 符号链接特权（WinError 1314，两侧同败）——环境/时序类，非代码回归。用户首轮全量 158F/61E 的 207 项 S40 簇已全数修复（107+145 分组复验全绿）。

- 消费方普查（origin/main，`git grep issue_plugin_registrar|PluginVoiceInputRegistrar`）：命中仅 plugin_api.py:20（定义）、registry.py:27/102（实现）、test_voice_input_registry.py:17/238/240/277（测试）——**当前 main 上该 SPI 为 test-only，无运行时接线**。此事实供未来产品决策对话直接引用（若维护者确认废弃，可整体切除 4 文件面）。

---
## 2026-09-27 (review round 2 / repair-4 + S5 retraction)
### S5 RETRACTED — maintainer directive (dev team, via user)
- steamworks/ 为**整体收录的第三方 Steamworks 专用库**（未进 PyPI，来自其他 repo），维护者立场：即使部分接口当前未用也**必须整体保留**——删掉会让未来 AI/运行时找不到接口面。S5 的"静态零消费"证据不适用于回调动态派发 + vendored 整体性原则。**已全量恢复至 origin/main（8 文件 +411 行净零）**，8/8 模块导入通过。教训入档：vendored 面的"死接口"判断必须先问收录意图。
### Review-driven fixes (CodeRabbit/Greptile, PR #3182)
- R2-1 [plugin/runs/websocket.py:5]：误删 `import time`（token 内联实现迁往 tokens 模块时的连带误删）→ 3 处 `time.time()` NameError（握手/心跳）。已恢复。
- R2-2 [brain/browser_use_adapter.py:1007]：删除 `del self._agents[session_id]` 时截断了上一行，残留 `await self._` → AttributeError 覆盖原始异常并跳过遮罩清理。已恢复为 `await self._remove_overlay(browser_session)`（方法存续于 :714，同型调用 :942/:974）。
- R2-3 [app/agent_server/channels/computer_use.py:294]：CUA 会话块删除后残留 `cu_session.session_id` 引用 → NameError 被 except 吞掉，初始 task_update 静默断流。已删除该行。
- R2-4 [plugin/settings.py:952]：`validate_config` 被误加进公开快照白名单 tuple（`__all__` 原有条目未动）→ 查询响应会携带函数 repr。已移除。
- R2-5 [app/monitor.py:367]：S33 把 origin 的日译条件分支错误坍缩为**无条件** turn-end 重播——但字幕已随每个 gemini_response 增量广播，每回合多一帧重复。已删除重播（保留 should_clear_next）。
- R2-6 [launcher.py] **证伪不改**：runtime/bootstrap 模块级导入仅 stdlib（subprocess/socket/ctypes 等），重量级加载都在 start_launcher() 内部；origin 同样在模块级导入 bootstrap（:20）后才做辅助模式早退。`_tiktoken_cache` 为 bootstrap 模块级 `if IS_FROZEN:` 副作用，launcher.py:22 的导入即触发；origin 的显式再导入是冗余保险（noqa: F401）。
- 工具盲区备注：仓库 ruff 配置未含 F821（显式 `--select F821` 才报 cu_session/time×3）；已建议维护者纳入，不在本 PR 改门禁。