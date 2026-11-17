---
name: feature-notes
description: 各功能域里读代码不容易看出来的既定规则和踩过的坑，按功能索引：区域判定与免费线路、遥测、版本问卷、语言偏好与角色配置锁、小游戏、小剧场、卡片锻造、插件页面守卫与依赖同步、FastAPI 路由树、屏幕分享。改到这些功能之前先查对应条目。
---

# 功能杂项备忘

每个功能只列最要紧的两三条，细节在 `references/`。i18n 相关的规则不在这里，见 `.agent/skills/i18n-pitfalls/`。

## 通用规则

- **GUI 一律在 Electron 前端做，Python 后端是 headless 服务，禁用 `tkinter`。** 守门脚本 `scripts/check_no_tkinter.py`（理由写在它的 docstring 里）。替代做法：
  - 选目录 / 文件：Electron 的 `dialog.showOpenDialog`；非 Electron 环境走 `main_routers/storage_location_router.py` 里的平台原生桥（PowerShell / osascript / zenity 等），桥失败就抛 `_DirectoryPickerUnavailable` 让前端提示用户手填，不要加 tk 兜底。
  - 截图框选：`static/app/app-buttons.js` 的 `captureDesktopRegionDirectly`。
  - 提示、确认框：后端只通过 HTTP / WebSocket 报状态，由前端渲染。
  - ⚠️ 为什么不是「加个兜底就行」：Nuitka 打包默认不带 tk 插件，`tk.Tk()` 抛的是 `SystemExit`（继承 `BaseException`），`except Exception` 抓不住，会一路逃出 worker 拖垮 uvicorn。
- **冻结发行包默认跑合并模式，源码运行默认多进程。** `launcher_core/runtime.py` 的 `_should_use_merged_mode()` 在没有 `NEKO_MERGED` 环境变量时返回 `IS_FROZEN`。所以「memory server 是独立进程，拿不到 X」在发行包里不成立；反过来「三个服务共享事件循环和进程级单例」在源码模式下也不成立。评审里出现跨进程 / 共享状态的论证时先确认说的是哪种形态。
- **多个失败点不要一起 `except Exception: pass` 收成 `None`。** 这样 CI 红了日志里只有 `assert None == ...`，线上等于功能坏了没人知道。至少按失败点打日志。
- **依赖平台专有工具的功能，先确认三个平台都能跑。** 出过整条路径只靠 PowerShell 加 `System.Drawing` 实现、在 Linux / macOS 上从第一版起就必然失败、评审多轮都没人发现的事。自动评审能把局部磨细，不会告诉你功能在某个平台上根本没跑起来。
- **单文件塞多个子系统的 PR 评审收敛不了。** 拆分门只数文件数，拦不住「一个文件几千行、里面有加密、适配、并发编排、缓存、校验、前端六块东西」的情况。这种改动先拆出核心基础设施单独提。

## 按功能索引

### 区域判定、免费线路、免费音色
- 区域探测是**单个后台线程**，它是探测结果的唯一写者；`get_core_config` 等调用方只读，绝不在调用线程做网络 IO。⚠️ 不要再加「再起一个探测」之类的多写者补丁。不变式写在 `utils/config_manager/core_config.py` 的模块 docstring 里。
- Steam 的地区只是兜底票，**任何情况下都不落定** `_region_cache`；落定只认 IP 探测结果。
- 免费音色和线路按**手上快照的 `base_url`** 配对，错配时一律不下发、交给服务端默认值（`tests/unit/test_free_voice_route_pairing.py`）。
- 换默认猫娘的免费音色 ID 时，旧值必须追加进 `_DEPRECATED_FREE_YUI_VOICE_IDS`，否则存量用户静默掉到通用音色。
- 详见 `references/region-telemetry-survey.md`。

### 遥测
- `event` 通道只写本机文件、不上报；要在服务端看到的指标（实验曝光、漏斗、计数）必须用 `counter` / `histogram`。既有做法是 event 和 counter 成对打。
- ⚠️ 前端 `appTelemetry.counter(name, value, dims)` 的维度是**第三个**参数，和 `event(name, fields)` 不一样。
- 详见 `references/region-telemetry-survey.md`。

### 版本问卷
- 要弹出来得一串闸全过：没开 DNT、判定为 Steam 版、当前版本有问卷文件、前端判定是老玩家升级、这一版没填过。任何一道不过都静默不弹。
- 只发 Steam 版、只发老玩家是产品设计，不是 bug。
- 打包必须带上 `config/surveys`（`scripts/check_nuitka_dist.py` 的必备资源里有它）。
- 详见 `references/region-telemetry-survey.md`；文件格式见 `config/surveys/README.md`。

### 语言偏好、角色配置锁、凭证中心、破冰教程
- `character_config_mutation_lock` 只保护 `characters.json` 的「读、改、存」事务。等客户端、等 connector、等 Steam 的 `await` 都不能放在锁里。
- 会话的 `user_language` 是 `None` 表示「没有证据」，不能当成「语言变了」。
- memory server 对语言偏好写入返回 409 是设计信号（因果序落败），不是故障。
- 破冰教程的选项池是有意只写不读的。
- 详见 `references/config-and-characters.md`。

### 小游戏
- `config/prompts` 里小游戏相关的 prompt 按「具体游戏一个文件、跨游戏按层分」组织，没有转发用的门面文件。
- ⚠️ 两套 locale 归一写法有意并存（羽毛球 quick-lines 用完整 locale 键，其余用短码键），不要统一。
- 斜杠快捷指令（整条消息只有 `/别名`）在后端识别，不改成前端识别。
- 详见 `references/minigames-and-theater.md`。

### 小剧场
- 发给模型的上下文是若干个 `======以下为X======` / `======以上为X======` 包起来的块，**不是一整段 JSON**；读回要用 `services/theater/prompt_data.py` 的 `prompt_data_block_payload`。
- 现状：内置剧本的 `meta.language` 都是 `zh-CN`，市场路由不带语言参数，没有语言闸。
- 详见 `references/minigames-and-theater.md`；设计文档见 `docs/design/neko-theater-*.md`。

### 卡片锻造
- 本仓库只提供候选事实（`main_logic/card_forge_facts.py` 的 `build_forge_facts_payload`，经 `/api/card-drop/facts` 和 `/api/card-drop/facts/query` 暴露）；「记忆够不够」的预检在社区端。改计数或过滤的语义前先确认消费方怎么用。
- 不按记忆来源禁止锻造。要排除某类记忆，走写入时带的结构化标记，不按来源或正文关键词。
- 详见 `references/minigames-and-theater.md`。

### 插件：页面守卫、依赖同步、入口元数据
- 插件页面会直接调用的写路由用 `PluginPageMutationGuardedRoute`（可信来源即放行，token 可选）；市场里还有插件不带 token 时，不得改成默认必需。完整说明见 `docs/design/security/local-mutation-auth.md`。
- `neko-plugin` 的依赖同步强制用 uv；只有找不到 uv 才回退到 pip 并打醒目警告。uv 失败不回退 pip。
- 配置文件里的 `[[entries]]` 表只在服务端生效，插件子进程看不到。
- 详见 `references/plugins-and-routing.md`；插件宿主的其它规则在 `.agent/skills/plugin-host-pitfalls/`。

### FastAPI 路由树、公共知识库
- ⚠️ 现在用的 FastAPI 版本里 `include_router` 不再把子路由展平进父 `router.routes`。测试里遍历路由一律用 `tests/fastapi_routes.py` 的 `iter_routes`。
- 公共知识库跑在 Memory Server 里，Main 只能经 HTTP 访问，分层门禁止 Main 侧 import `knowledge`。见 `docs/architecture/knowledge-architecture.md`。
- 详见 `references/plugins-and-routing.md`。

### 屏幕分享、前端与测试的零散坑
- `window.stopScreening` 只停发送（分享还要继续时用）；会话收尾用 `window.teardownScreenSharing`。两个不要混用。
- 「枚举屏幕来源会不会弹系统对话框」只有一个判断入口：`static/app/desktop-capture-provider.js` 的 `window.desktopSourceEnumerationMayPrompt`。
- solution-style 的 `tsconfig.json` 下 `tsc --noEmit` 一个文件都不检查，要用 `tsc -b --noEmit`。
- 详见 `references/frontend-and-tests.md`。

## references 索引

| 文件 | 什么时候读 |
|---|---|
| `references/region-telemetry-survey.md` | 动区域判定、免费线路 URL 改写、免费音色、遥测埋点、版本问卷之前 |
| `references/config-and-characters.md` | 动角色增删改名、语言偏好、凭证中心页面、破冰教程存储之前 |
| `references/minigames-and-theater.md` | 动小游戏 prompt / 路由 / SDK、斜杠指令、小剧场、卡片锻造之前 |
| `references/plugins-and-routing.md` | 动插件服务器写路由、`neko-plugin` 依赖同步、插件入口元数据、路由遍历类测试之前 |
| `references/frontend-and-tests.md` | 动屏幕分享、跑本地测试遇到「单跑绿批跑红」、Windows 上文件替换报权限错误时 |
