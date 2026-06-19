# neko_roast 开发者指南（从这里开始）

> 面向**接手 / 参与 `neko_roast`（直播中心）开发**的人。这是单一入口：先读这份建立心智模型，
> 再按需深入下面「文档地图」里的参考文档。**不要从 `development.md` 开始**——那是参考规格（厚），
> 本文是上手向导（薄）。
>
> 更新日期：2026-06-19 · 测试基线 58 passed / 0 error

---

## 1. 这是什么

`neko_roast`（代号「猫娘锐评」）真身是 N.E.K.O 桌面猫娘的**直播中心 (Live Center)**：把主播直播的
全生命周期接进猫娘——开播 → 直播间互动（弹幕 / 进场 / 礼物 / SC / 舰长）→ 私信 → 主播侧自动化。

「首评新观众锐评」（观众首条弹幕 → 猫按人设锐评其昵称 + 头像）只是**第一个落地的垂直切片**。
所有未来能力以 neko_roast 的**内部模块**形式集成，不做跨插件宿主。

**当前状态**：核心闭环（真实直播监听 → 事件中枢择优 → 锐评 → 猫开口）已真机验证；P5 登录态已落地；

## 2. 五分钟心智模型

记住三句话，后面所有设计都从它们推出来：

- **猫开口才是产品，面板只是遥控器。** 面板退到背景，优先「控制 + 监看」，别精装修。
- **用户多是电脑小白，场景是 LIVE 直播**（不可重来、当众）。第一性原则是**全程可信赖**：
  宁可漏评，不可崩坏输出 / 不可一个模块炸了搞砸全场。**可靠性 > 功能多 > 界面炫。**
- **猫只有一张嘴。** 所有产出都要协调这唯一的输出口，所以有唯一出口、限流、急停、窗口择优。

## 3. 架构总览

**分层**：`Ingest（接入）→ Normalize（归一化）→ Pipeline（处理路由）→ Handler（产出）→ Dispatch（唯一出口）→ Store（数据）`。

**四条不变量（代码强制，勿破）**：
1. NEKO 输出只走 `adapters/neko_dispatcher.py`；
2. 观众档案写只走 `stores/viewer_store.py`；
3. 审计只走 `stores/audit_store.py`；
4. 直播与沙盒**共用** `core/pipeline.py`，安全门 `core/safety_guard.py` 必经。

外加一条 P5 红线：登录凭据只走 `stores/credential_store.py` 加密落盘，**绝不**写 audit / log / config / UI。

## 4. 目录结构速览

```text
neko_roast/
├─ __init__.py            插件入口（NekoRoastPlugin）+ @ui.action / @plugin_entry 动作
├─ core/                  骨架：pipeline / safety_guard / runtime / contracts / event_bus /
│                         module_registry / permission_gate / instructions
├─ modules/               能力模块（InteractionModule）：bili_live_ingest / bili_identity /
│                         developer_sandbox + 预留模块
├─ adapters/              neko_dispatcher（唯一输出）/ bili_auth_service（扫码登录）
├─ stores/                viewer_store / audit_store / avatar_cache / credential_store
├─ ui/panel.tsx           Hosted UI（sucrase 运行时转译，改了不用 rebuild）
├─ i18n/*.json            8 个 locale（新增文案必须同步全部 8 个）
├─ tests/                 插件自带单测
└─ docs/                  本目录（见文档地图）
```

## 5. 开发环境 & 运行态

- **后端** `http://127.0.0.1:48916`（主服务 48911）；**前端** 在 `NEKO-PC/` 下 `npm start`。
- 插件 `auto_start=false`：后端起来后必须 `POST /plugin/neko_roast/start` 才注册 hosted-ui 路由（否则 404）。
- **改 `.py` 后**：`POST .../stop` 再 `.../start` 重载子进程即可；**但新增 `@ui.action`/`@plugin_entry`
  动作要全量重启后端**（surface 暴露校验在主进程，`/start` 不刷新，见 devlog）。
- **改 `panel.tsx` / `i18n`**：**不用 rebuild 前端**（plugin-manager 用 sucrase 运行时转译），UI 里重开面板即生效。
- **动作调用**：`POST .../hosted-ui/action/<id>`，body `{"args":{...},"kind":"panel","surface_id":"main"}`。
- **配置改不动时**（写竞争）：走 host 直写 `POST /plugin/neko_roast/config/hot-update`，
  body `{"config":{"neko_roast":{...}},"mode":"temporary"}`（内存热更、不落盘）。
- ⚠️ `dry_run` 默认开启；只有主播明确进入正式输出窗口时才关掉。`dry_run=false` 连真房间猫会**真开口**。测试房 `81004`。

详见 `live-center-roadmap.md` §5「运行态 & 交互速查」。

## 6. 核心契约：加一个事件 handler（最常见的扩展）

这是「多人各写各模块」的核心路径。直播事件经 `EventBus` 按 `type` 路由，加一个事件族功能 =
写一个模块 + 订一个事件类型，**零改外壳、零碰接入层**：

1. 在 `modules/<your_id>/__init__.py` 写 `BaseModule` 子类，声明 `id` / `title` / `domain`（如 `"interaction"`）。
2. `setup(ctx)` 里订阅：`self._unsub = ctx.event_bus.subscribe("gift", self._on_gift, owner=self.id)`；
   `teardown` 里 `self._unsub()`。
3. handler `_on_gift(event: LiveEvent)`：从 `event.payload` / `event.raw` 取字段，**绝不**自己
   `push_message`——整理成 payload 交给 `ctx.handle_live_payload(...)`，走安全门 + 四条不变量。
4. 功能参数用 `config_schema()` 声明（面板自动渲染功能卡）；新增文案同步 8 locale；补单测。
5. 在 `core/runtime.py` 用 `registry.register(...)` 注册你的模块。

**`live_events`（订阅 `"danmaku"` / `"gift"` / `"super_chat"` / `"guard"` 做窗口择优）是可照抄的参考订阅者。** 三条保证：每订阅者隔离、失败按 owner
归属记 audit、无订阅者静默丢弃。完整契约 + `LiveEvent` 信封字段见 `development.md`「直播事件中枢（EventBus）」。

## 7. 模块贡献模型

后端早已模块化（`InteractionModule` + `ModuleRegistry`）。一个功能 = 一个自包含 `modules/<id>/` 文件夹，
声明四个面向，平台据此组合：

- **生命周期**：`setup` / `teardown`（+ `on_enable` / `on_disable`，由 `ModuleRegistry.enable/disable` 隔离调用）。
- **事件**：`ctx.event_bus.subscribe(type, handler, owner)`（见 §6）。
- **数据**：只经 `viewer_store` / `audit_store` 边界（四条不变量）。
- **界面**：`domain`（归哪个一级页）+ `config_schema()`（声明式参数，面板自动渲染成功能卡）。

**加新功能 = 加模块 + 声明上面这些，零改外壳、并行无冲突。** 详见 `ui-architecture.md` §2 / §3。

## 8. 可靠性：五层兜底

LIVE + 多模块 + 多人写 ⇒ **任何单个模块失败都不能搞砸直播**。这是平台保证，不是各模块自觉：

| 层 | 保证 | 状态 |
|---|---|---|
| ① 注册层 | 坏模块 setup/teardown 抛错只标 `degraded` + audit，其余照常 | ✅ |
| ② 事件层 | EventBus 每订阅者隔离 + 归属 + audit，无 handler 静默丢弃 | ✅ |
| ③ 输出层 | 唯一出口 + dry_run / 限流 / 急停 / 队列 | ✅ |
| ④ UI 层 | 单模块卡渲染抛错 → 降级卡，整盘面板照常（`safeModuleCard`） | ✅ |
| ⑤ 操作层 | 一键急停 + 安全状态灯 + 自动急停 | ✅ |

详见 `ui-architecture.md` §4。

## 9. UI 约定

- Hosted UI 在 `ui/panel.tsx`，**六个一级页**（控制台 / 直播间互动 / 观众 / 私信 / 自动化 / ⚙设置
  + 开发者沙盒按 dev 模式条件追加），id / 顺序由契约测试锁定。
- 功能卡由模块 `config_schema()` **声明式驱动**（boolean→Toggle / select→pill / 其余→Input），改即存。
- 宿主 runtime 约束（写 UI 前必读）：`data:` URL 会被剥（用 CSS `background-image` 绕）、**SVG 渲不了**、
  无 `useRef`。详见 `ui-architecture.md` §6。
- **新增 UI 文案必须同步 8 个 locale 文件。**

## 10. 测试 & 校验（提交前必跑）

仓库根 `N.E.K.O/` 下：

```powershell
# 全量（插件 tests/）
uv run pytest plugin/plugins/neko_roast/tests -q
# CLI 检查（0 error 才算过；6 条模板 warning 允许）
uv run python -m plugin.neko_plugin_cli.cli check plugin/plugins/neko_roast
```

改了 `panel.tsx` 还要确认 sucrase 转译通过（前端 plugin-manager 用生产同款选项
`transforms:['typescript','jsx']`, `jsxPragma:'h'`, `production`）。当前基线：**58 passed / 0 error**。

## 11. 红线 / 硬规则

写代码前过一遍 `AGENTS.md`（IDE agent 与贡献者的硬规则），要点：

- 四条不变量 + 凭据红线（见 §3）。
- 新能力加成模块，不要在 `__init__.py` 堆大块内联；事件 handler 走 `subscribe` + pipeline，不碰 `push_message`。
- 不整体拷贝旧插件 `bilibili_danmaku` / `bilibili_dm` 大文件；复用只拆小模块 + 补测试。
- **勿与 neko_roast 同直播间双连**旧插件。
- `developer_tools_enabled` 是开发者模式唯一总控；权限以后端检查为准，不只靠前端禁用。
- **没有对应文档的新功能视为未完成**（见 `development.md`「新功能文档要求」）。

## 12. 文档地图

| 文档 | 是什么 | 何时读 |
|---|---|---|
| **developer-guide.md**（本文）| 上手向导 | 第一份，建立心智模型 |
| `development.md` | 参考规格（已落地设计逐项细节）| 实现某功能前查对应章节 |
| `live-center-roadmap.md` | 定位 / 进度 / 决策 / 运行态速查 / 路线 / 下一阶段 TODO | 想知道做到哪、接下来做什么 |
| `ui-architecture.md` | 面板 UI / 模块贡献模型 / 五层兜底 / 宿主约束 | 写面板或新模块前 |
| `AGENTS.md` | IDE agent 与贡献者硬规则 | 写代码前 |
| `quickstart.md` | 主播 / 使用者操作流程 | 想知道用户怎么用 |
| `devlog.md` | 宿主 / SDK 侧问题与跨层待办 | 撞到「不像本插件的锅」时 |

## 13. 已知坑 & 宿主侧待办（提前知道少踩）

- **配置写竞争**：host/core 修复 `Fix plugin host config and data root handling (#1884)` 已进入当前 `Roast` 分支；插件侧仍保留“内存先行 + 带预算持久化”的免疫策略，避免未来 host 持久化异常拖垮直播 action。详见 `development.md`「配置持久化与写竞争」。
- **存储宿主历史 bug**：`PluginStore.store.enabled` 构造期冻结、插件数据不跟随 selected_root 已由 #1884 修复；观众档案仍走本地 JSON，当前 UI 暂不恢复 `viewer_store_dir` 自定义入口，待插件侧回归后再启用。详见 `devlog.md`。
- **hosted-ui 渲染**：`data:` URL 被剥、SVG 渲不了（见 §9 / `ui-architecture.md` §6）。
- **新增 action 要全量重启后端**才暴露（见 §5）。
- **lookup -352 风控**：查询失败 ≠ 监听失败；登录态可根治。详见 `development.md`「直播间查询与 -352 风控」。

## 14. 下一步去哪看


当前接手建议：先读 `live-center-roadmap.md` §10 确认真实 TODO，再读 `ui-architecture.md` 确认模块贡献模型。新增 P3 handler 必须走 `EventBus` 订阅、复用 `pipeline` / `safety_guard` / `neko_dispatcher`，同步 8 个 locale，并在 `docs/modules/<module_id>.md` 或 `development.md` 留下模块文档。
