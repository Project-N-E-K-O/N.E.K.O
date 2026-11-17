---
name: nekoclaw
description: NekoClaw（猫爪，`nekoclaw/`）的不变式、跨进程契约和不显然的既定规则，外加设计稿的导读。改 `nekoclaw/`、`app/agent_server/`、主对话的派发 / 查询工具、键鼠和浏览器工具、使魔空间之前读。
---

# NekoClaw（猫爪）

设计稿是 `docs/design/nekoclaw-rewrite.md`，很长，按下表挑着读。这份文档只写设计稿里没有、或者埋在落地说明里容易漏掉的规矩。叫法（界面「使魔」= 代码 task agent）见 `.agent/rules/neko-guide.md` 的「NekoClaw 术语」。

## 动哪里，先读设计稿哪一节

| 要动的 | 代码 | 设计稿 |
|---|---|---|
| 主对话的派发 / 查询工具、旁听、汇报回流 | `main_logic/core/tool_calling.py`、`app/agent_server/`、`nekoclaw/host/task_brain.py` | 第 6 节；落地出入 6.9、6.10 |
| 台账、任务偏好、能力清单 | `nekoclaw/host/ledger.py`、`preferences.py`、`capabilities.py` | 第 5 节、6.9 |
| agent runtime | `nekoclaw/runtime/` | 第 7、8 节；8.9 |
| 能力网关、权限 | `nekoclaw/host/gateway.py`、`permissions.py` | 第 10 节 |
| 键鼠、浏览器 | `nekoclaw/builtin/` | 第 11 节；11.9 |
| 使魔空间、插件共享空间 | `nekoclaw/host/space.py`、`space_keeper.py` | 第 12 节；12.5 |
| 还没定的、没验证过的 | — | 第 15 节 |

设计稿和代码对不上时以代码和测试为准。上限、时限都在 `config/nekoclaw_settings.py`（新加的可调参数也放这里），提示词在 `config/prompts/prompts_nekoclaw*.py`，测试在 `tests/unit/nekoclaw/` 和 `tests/unit/test_agent_*.py`。

## 三样东西别混

- **neko 插件**：任务大脑直达 `/runs`。这是主路径，一步能办完的事根本不开 task agent。
- **task agent**：插件选不中、或要多步才开。它和插件是平级的两条路。
- **task agent 经网关调插件**：小路。`delivery`、`needs_confirmation`、`clarify`、deferred 在这条路上的处理和直达不一样（6.2 节）。讨论插件结果怎么处理时先讲直达。

## 不变式

1. ⚠️ **「猫爪开没开」主服务和 agent 进程必须是同一个判断**：总开关开着，并且键鼠、浏览器、插件三个开关至少开一个。主服务是 `main_logic/core/notify.py` 的 `_is_agent_enabled()`（开场那句、两个工具、换会话时的「正在办的事」，三者同有同无）；agent 进程是 `app/agent_server/nekoclaw_mount.py` 的 `nekoclaw_on()`（派发受不受理、旁听跑不跑）。只改一边的后果：她手里没有工具，旁听却替她把事派了出去。加能力开关时两处一起改。
2. **要主对话模型记住的东西，按它十轮就失忆来设计**。它约每 `SESSION_TURN_THRESHOLD` 个用户轮次换一次会话，只留几条消息加摘要。所以派发、查询都不带编号，任务记录只在台账里，换会话时从 `/nekoclaw/brief` 现取。
3. **一轮只有一个判断者，旁听没有执行权**：她调了派发，这一轮就不旁听；只有她派发了或口头答应了才立案，她说不做就不做（6.4 节）。
4. **「先回一句、后台再判」的设计，先问后台的结论能不能推翻先回的那句**。能推翻就必须走改口提醒（6.5 节），不能让回执和后来的结论悄悄不一致。
5. **启动链路上不建宿主**。`mount_nekoclaw_host` 只登记回调；`nekoclaw.host.mount.get_host()` 才建，`peek_host()` 不建。只读的入口（任务列表这类）不要为一次读去建宿主，照 `restore_hud()` 先看台账文件在不在。
6. **分层**（`scripts/check_module_layering.py`）：`nekoclaw/runtime/` 和 `nekoclaw/builtin/` 只用 `config`、`utils`；`nekoclaw/host/` 不 import `memory`、`knowledge`、`main_logic`、`main_routers`、`services`，到主对话只经 outbox，到插件只经 HTTP。`tests/unit/nekoclaw/test_guard_coverage.py` 守着各守卫脚本的目录名单里有 `nekoclaw`。
7. **委托的终态和给她的汇报在同一个事务里写**：`Reporter.transition(..., report=reporter.task_result_report(...))`。新加的结束路径不要先改状态、再单独写 outbox。
8. **消耗**：动到模型调用（次数、token、延迟）的改动，PR 描述里给改动前后对比，有增加要维护者点头（6.8 节）。固定开销的上限有测试守着，超了先想办法省，不要直接抬上限：
   - 主对话每轮：`tests/unit/test_task_tools_budget.py` 的 `ON_LIMIT`（设计稿正文里的 221 是旧数）。
   - task agent 每次请求：`tests/unit/nekoclaw/test_builtin_tool_budget.py`。
   - 决策模型每条路径调几次模型：`tests/unit/nekoclaw/test_host_decision.py` 里的断言。查询任何情况下都不调模型。
   - 给 task agent 加工具，默认按「用到才加载」做，固定开销先量。
9. **发给模型的提示词**：
   - 发给免费档的提示词必须带水印，否则请求被拒。`prompts_nekoclaw_brain.py` 的提示词表有测试守着（`test_host_decision.py`），新加一张表要登记进那条测试。
   - 不传 `temperature`（`scripts/check_no_temperature.py` 管着 `nekoclaw/`）。
   - task agent 的系统提示和自带工具定义照抄 dsh 的英文原文，不翻译。改动要更新快照：`NEKOCLAW_UPDATE_SNAPSHOTS=1` 跑 `tests/unit/nekoclaw/test_prompt_snapshot.py`，再看 `snapshots/` 的 diff。
   - 界面上不出现 skill / 技能书。「给 task agent 一个 skill」在实现里就是按组加载的说明加工具定义，不做通用的技能加载机制。
10. **键鼠和浏览器在 task agent 自己的循环里**：两组细粒度工具加各自的说明，经能力网关给出。没有里层循环，没有哪个工具自己调模型。实现留在 agent 进程（`nekoclaw/builtin/`），不进运行时子进程。浏览器直接走调试协议，不依赖 browser-use、Playwright。
11. **有窗口的浏览器留给用户**：应用只在「保存了有变化的浏览器设置」和「退出应用」时关它。任务办完、全部终止、自动停用、关开关、关总开关都留着，窗口和调试连接都不动。用户自己关掉以后，task agent 再要用时先问（11.6 节的两张表）。
12. **使魔空间（`task_agent_space`）只有宿主写**，task agent 用 `note_lesson` 提交；细节见 `references/host.md`。
13. **插件契约不动**：`/runs`、`push_message`、`@llm_tool`、投递语义、`_ctx` 注入的字段、可见性开关（第 14 节「必须守住」）。
14. **和桌面壳的契约点**（主仓库这边要保持，改了要同步壳）：`agent_builtin_activity` 事件、`GET /api/agent/builtin/activity`、`POST /api/agent/builtin/stop`（11.8 节）；`task_update` 事件和 `/tasks` 的形状、任务状态值（6.10 节）；新手教程依赖的 DOM id 和 `AgentHUD.*`。没有壳时（源码运行、Docker）没有横幅，操作照常。

## 已经定了，不要再当选项提

- OpenClaw 不接。
- 内置运行时只维护移植来的核心循环，不 fork 上游、不换成别的 SDK。
- 系统级沙箱现在不做；将来的方向是自己做、Windows 原生，不依赖 Docker。仓库里的 seccomp 配置只服务 Docker 部署镜像里的浏览器。
- task agent 的额度将来按用量算。现行按次扣是过渡（第 15 节），不要再设计按次数的方案。
- 退出应用时关浏览器。

## references

- `references/runtime.md`：改 `nekoclaw/runtime/` 前读。移植代码怎么动、模型给的正则、目录搜索、上下文裁剪和落盘里定过的取舍。
- `references/host.md`：改 `nekoclaw/host/`、`app/agent_server/` 前读。调插件、叫停、查询和旁听里定过的评审意见，使魔空间里「看着像 bug、其实有意」的清单。
- `references/builtin-tools.md`：改键鼠、浏览器工具或它们的加载方式前读。用到才加载的规则和已知缺口、浏览器收尾、测试上的坑、Docker。
