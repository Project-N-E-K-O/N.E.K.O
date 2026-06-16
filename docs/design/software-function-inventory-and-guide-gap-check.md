# N.E.K.O. 软件功能整理与新手教程查漏

本文档基于当前项目代码和现有两份悬浮窗教程设计文档整理：

- `docs/design/avatar-floating-guide-feature-tree.md`
- `docs/design/avatar-floating-panel-functions.md`

目标不是替代两份 7 日教程文档，而是做一张更完整的软件功能地图，并标出哪些功能已经被教程覆盖、哪些功能容易漏写、哪些功能适合放进新手教程或进阶文档。

## 检查范围

本次检查覆盖了以下代码入口：

- 服务器入口：`app/main_server.py`、`app/memory_server.py`、`app/agent_server.py`、`app/monitor.py`。
- 主服务路由：`main_routers/*.py`。
- 页面模板：`templates/*.html`。
- 首页与聊天前端：`static/app-*.js`、`static/*ui-buttons.js`、`static/yui-guide-*.js`、`static/tutorial/core/universal-manager.js`、`static/tutorial/avatar/floating-guide-reset.js`、`frontend/react-neko-chat/src/*`。
- 插件系统：`plugin/server/routes/*.py`、`plugin/plugins/*/plugin.toml`、内置插件目录。
- 测试覆盖线索：`tests/unit/*`、`tests/frontend/*`、`plugin/tests/unit/*`。
- README 与现有设计文档中的产品说明。

## 前端承载边界

本文后续把功能按前端承载位置拆成三类：

- 首页功能：由 `/` 或 `/{lanlan_name}` 渲染的 `templates/index.html` 直接承载，包含首页 Avatar、悬浮按钮、弹窗、侧边面板、React 聊天窗、教程和首页内 HUD。用户不离开主页即可看到或触发。
- 子页面功能：由 `main_routers/pages_router.py` 中显式页面路由或插件 UI 路由承载，通常通过首页设置、侧边面板、聊天工具栏或教程 handoff 打开。
- 后台/API 能力：没有独立页面，或只作为首页/子页面的数据与执行后端存在，例如记忆服务、Agent 服务、插件运行服务、遥测、部署和健康检查。

## 首页功能地图

### 1. 首页 Avatar 与悬浮按钮

- 首页路由：`/` 和 `/{lanlan_name}`，模板为 `templates/index.html`。
- Avatar 形态：Live2D、VRM、MMD 在首页加载和切换。
- 模型交互：拖拽、缩放、鼠标跟随、全屏/局部追踪、锁定/解锁。
- 悬浮按钮：语音、屏幕分享、Agent、设置、锁定、“请她离开/回来”。
- 首页模型表现：动画帧率、画质、悬停淡化、表情气泡、反应气泡。

### 2. 首页聊天窗与消息工具

- React 聊天窗挂在首页，支持外置/内嵌聊天窗口。
- 消息列表、流式文本、富文本、代码块、链接。
- 文本输入、发送、合并消息、允许打断、回复 token 上限。
- 截图按钮、粘贴图片、导入图片、待发送附件队列、单张移除、清空附件。
- 聊天导出、聊天头像、跨窗口通信。
- 翻译按钮、点歌台入口、Galgame 模式按钮、小游戏邀请响应。
- 工具栏折叠、更多工具、Emoji/Avatar 互动工具。
- Avatar 互动工具包括棒棒糖、拳头、锤子等鼠标工具、音效和特殊反馈。

### 3. 首页语音、音频、屏幕与视觉

- 实时语音对话入口和录音状态。
- 麦克风小三角弹窗：麦克风选择、默认设备、录音静音、音量条。
- 音频设置：空间音频、降噪、麦克风增益、播放音量。
- 屏幕分享按钮和屏幕/窗口来源弹窗。
- 截图、交互式截图、图片输入与视觉理解。
- 字幕/翻译脚本可在首页联动显示，但独立字幕窗口属于子页面。

### 4. 首页设置弹窗与侧边面板

- 设置弹窗由首页悬浮按钮打开。
- 对话设置侧边面板：合并消息、允许打断、表情气泡、回复 token 上限。
- 动画设置侧边面板：画质、帧率、鼠标跟踪、全屏/局部跟踪、锁定悬停淡化。
- 主动搭话侧边面板：最低间隔、媒体凭证、屏幕分享、新闻/视频网站、个人动态、音乐推荐、表情包分享、小游戏邀请。
- 隐私模式侧边面板：主动视觉感知开关和感知间隔。
- 角色设置快捷侧边面板：角色、模型管理、声音克隆等子页面入口。
- API、记忆浏览、存储位置等入口从首页进入，但完整管理界面在子页面。

### 5. 首页主动陪伴与活动感知

- 主动搭话模式、主动话题来源和最低间隔。
- 活动信号采集：系统活动、屏幕/窗口、媒体播放、网站、音乐、小游戏邀请等。
- 主动视觉感知与隐私边界。
- 音乐播放完成信号、个人动态、表情包/梗图相关触发。
- 用户控制边界集中体现在屏幕、隐私、主动搭话、记忆和存储入口。

### 6. 首页 Agent 与任务 HUD

- Agent 弹窗：状态栏、总开关、键鼠控制、Browser Control、专属桌面、用户插件、OpenClaw。
- Agent 任务 HUD：运行/排队统计、任务列表、空状态、完成/失败/取消状态。
- 任务操作：折叠/展开、拖拽位置保存、终止全部任务、单任务终止。
- Agent 侧边面板：用户插件管理入口、OpenClaw 接入教程入口。
- 独立 `/agenthud` 子页面承载完整 Agent HUD 与任务详情流；首页 HUD/侧边入口不是 Agent 管理的唯一入口。
- 真实执行、任务详情、校正和完成回调由后台 Agent API 支撑。

### 7. 首页新手教程与提示

- 首页 YUI 苏醒教程：`static/tutorial/yui-guide/steps.js`、`static/tutorial/yui-guide/wakeup.js`。
- 模型旁悬浮窗教程：`static/tutorial/yui-guide/director.js`，当前 4 日骨架，目标 7 日。
- 通用教程管理器：状态持久化、手动意图、重置、页面 handoff。
- 教程 UI：花瓣转场、ghost cursor、spotlight、高亮覆盖层、跳过按钮、生气退出。
- 教程提示、自动启动提示、pending notice。

### 8. 首页级系统辅助

- 存储位置 bootstrap 与启动受限提示。
- 本地 mutation 安全头和页面配置。
- Token 用量/遥测前端安装点。
- Steam 成就/游玩时长相关前端触发。
- Debug health 脚本加载，但完整诊断属于后台/API 能力。

## 子页面功能地图

以下页面由 `main_routers/pages_router.py` 或插件 UI 路由显式承载，不属于首页内功能。

| 路由/入口 | 模板或承载 | 主要功能 |
| --- | --- | --- |
| `/model_manager`、`/l2d` | `templates/model_manager.html` | Live2D/VRM/MMD 模型选择、上传、删除、预览、用户模型目录、Workshop 模型引用。 |
| `/live2d_parameter_editor` | `templates/live2d_parameter_editor.html` | Live2D 参数读取、调参、保存/加载。 |
| `/live2d_emotion_manager` | `templates/live2d_emotion_manager.html` | Live2D 情感映射管理。 |
| `/vrm_emotion_manager` | `templates/vrm_emotion_manager.html` | VRM 表情/情感映射管理。 |
| `/mmd_emotion_manager` | `templates/mmd_emotion_manager.html` | MMD 情感映射、光照/曝光/色调映射和动作表现管理。 |
| `/voice_clone` | `templates/voice_clone.html` | 声音克隆、参考音频、Workshop 参考语音、静音检测和裁剪任务。 |
| `/api_key` | `templates/api_key_settings.html` | 核心 API、辅助 API、服务商、代理模式、连通性配置。 |
| `/character_card_manager` | `templates/character_card_manager.html` | 角色创建/编辑/删除/重命名、角色卡导入导出、卡面与元数据、Steam 创意工坊上传/订阅/同步、云存档入口。 |
| `/chara_manager` | 重定向到 `/character_card_manager` | 旧入口兼容。 |
| `/card_maker` | `templates/card_maker.html` | 角色卡卡面制作，独立加载模型并调整构图。 |
| `/cloudsave_manager` | `templates/cloudsave_manager.html` | 云存档摘要、角色上传/下载、Steam AutoCloud 信息、跨窗口角色同步。 |
| `/memory_browser` | `templates/memory_browser.html` | 近期聊天文件、摘要、自动记忆整理、强力记忆、保存、清理、教程重置、存储位置入口。 |
| `/cookies_login` | `templates/cookies_login.html` | 平台 Cookie 管理、二维码登录、外部服务凭证获取。 |
| `/chat` | `templates/chat.html` | 独立聊天窗口。 |
| `/subtitle` | `templates/subtitle.html` | 独立字幕窗口。 |
| `/agenthud` | `templates/agenthud.html` | 独立 Agent HUD 窗口。 |
| `/jukebox` | `templates/jukebox.html` | 点歌台独立窗口。 |
| `/jukebox/manager` | `templates/jukebox_manager.html` | 点歌台配置、歌曲、动作、绑定、导入导出和打包。 |
| `/toast` | `templates/toast.html` | Electron Toast 通知窗口。 |
| `/soccer_demo` | `templates/soccer_demo.html` | VRM + Live2D 足球 demo。 |
| `/api/agent/openclaw/guide` | `templates/openclaw_guide.html` | OpenClaw 接入教程。 |
| 插件管理 UI `/ui` | `plugin/server/routes/frontend.py` | 插件管理面板。 |
| 插件页面 `/plugin/{plugin_id}/ui` | `plugin/server/routes/plugin_ui.py` | 插件自带 UI、Hosted UI、surface、locale、i18n。 |

当前仓库还包含 `templates/viewer.html` 和 `templates/return-ball.html` 等模板；本次在 `pages_router.py` 未看到普通页面路由，视为专用入口或历史/实验页面，后续若有实际打开路径再补入子页面表。

## 后台/API 能力地图

这些能力主要服务首页或子页面，本身不一定有独立前端页面。

### 1. 启动、服务与部署

- 一键启动与打包入口：`launcher.py`、`specs/*.spec`。
- 主服务：页面、配置、角色、模型、聊天、Agent 代理、插件、音乐、游戏、存储等 API。
- 记忆服务：记忆缓存、整理、查询、反思、近期历史、存储启动状态。
- Agent 服务：电脑控制、浏览器控制、OpenFang、OpenClaw、MCP 可用性、任务队列。
- 监看端：字幕页、同步 WebSocket、模型观看/同步。
- Docker 部署：HTTP/HTTPS、SSL 证书、环境变量、健康检查。
- 健康检查：`/health`、插件服务 `/health`、调试健康页 `/api/debug/health`。

### 2. 角色、人设、声音与角色卡 API

- 当前角色读取与切换。
- 角色创建、编辑、删除、重命名、主角色设置。
- 人设预设、人格选择、首次人格引导、重新选择人格。
- 声音 ID、声音预览、自定义 TTS 声音列表。
- 麦克风保存、清空声音 ID。
- 角色卡导入、导出、带头像导出、卡面与元数据。

### 3. 模型服务、API 与配置

- 核心 API 与辅助 API 配置。
- 多服务商配置和供应商列表。
- 页面配置、用户偏好、对话设置。
- 代理模式、连通性测试、GPT-SoVITS 语音列表和连通性测试。
- Prompt 模块：角色、系统、记忆、游戏、galgame、主动搭话、Agent、情感、活动信号等。
- Token 用量统计。

### 4. 记忆系统

- 五维记忆：工作记忆、近期记忆、事实记忆、反思记忆、人格记忆。
- 记忆查询、搜索、follow-up topic、surfaced 记录。
- 事实抽取、证据链、反思、归档、时间索引。
- 旧版记忆扫描和清理。
- 记忆服务缓存、续写、settle、reload。

### 5. Agent 与任务执行 API

- Agent 状态、flags、总开关。
- 键鼠控制、Computer Use、Browser Use。
- OpenFang 专属桌面/无头执行后端。
- OpenClaw 可用性检查。
- MCP 可用性。
- Agent 命令、内部请求分析。
- 任务列表、任务详情、取消、校正、完成回调、管理控制。

### 6. 插件系统与内置插件

- 插件服务健康、可用性、插件列表、启动/停止/刷新/重载/删除。
- 插件扩展 enable/disable。
- 插件配置：基础配置、TOML、profile、热更新。
- 插件 UI、Hosted UI、surface、locale、i18n。
- 插件日志、WebSocket 日志、指标和历史指标。
- Run/upload/blob/cancel/export 执行记录。
- 插件 CLI：打包、检查、验证、解包、分析、上传、下载。
- LLM tool callback。
- 内置插件：
  - B 站弹幕监听：直播间弹幕过滤、分析、回复。
  - B 站私信：私信自动对话，支持文本、图片、视频分享等消息类型。
  - Galgame 游玩助手：OCR、窗口绑定、文本提取、场景总结、选项建议、自动推进、视觉分类训练。
  - Minecraft 游戏代理：游戏状态、库存、配置热更。
  - 生活助手：天气、逐小时预报、路线规划、常用地点、附近 POI、单位/货币等。
  - MCP Adapter：连接 MCP server，把工具暴露给 N.E.K.O.
  - 备忘提醒：一次性和重复提醒。
  - 米家智能家居：设备控制、家庭/设备/场景。
  - 主动搭话控制器：集中调度主动搭话模式和频率。
  - QQ 自动回复：OneBot、权限管理、AI 回复。
  - STS2 自动游玩：Slay the Spire 2 策略、候选动作、执行、局势总结。
  - Study Companion：屏幕分类、出题、答题评估、解释、记忆卡片、复习、习惯、番茄钟、知识图谱、笔记导出。
  - 网络搜索：按地区选择搜索引擎，无 API Key。

### 7. 游戏与场景陪玩 API

- 通用游戏路由：聊天、路线 start/state/drain/heartbeat/end、speak、实时上下文、quick lines、角色信息。
- Mirror assistant。
- Galgame 主路由和插件能力：OCR、RapidOCR/Tesseract/Textractor、角色资料、剧情历史、场景总结、选项建议、自动推进。
- STS2 自动游玩插件。
- Minecraft Agent 插件。

### 8. 音乐、点歌台与媒体 API

- 音乐搜索、代理、域名列表、网易云播放。
- 点歌台配置、歌曲添加/删除、显示状态、元数据编辑。
- 动作绑定、默认动作、导入、导出、打包文件夹、文件访问。

### 9. Steam、创意工坊与云存档 API

- Steam 成就、游玩时长、成就列表、图片代理。
- Steam 创意工坊：订阅列表、下载、下载状态、路径、读取文件、上传预览图、上传参考音频、发布、同步角色。
- 角色创意工坊元数据、声音参考音频。
- 云存档摘要、Steam AutoCloud 配置。
- 单角色上传/下载云存档。
- 存储位置与云存档启动流程联动。

### 10. 存储位置、诊断、遥测与安全

- 存储位置 bootstrap、status、diagnostics、retained-source。
- 选择目录、打开当前目录、预检、重启、退出。
- 本地 mutation 安全头。
- 匿名 Token 用量遥测、DO_NOT_TRACK/NEKO_DO_NOT_TRACK opt-out。
- 本地遥测服务：HMAC、防重放、限流、append-only 存储。
- 事件日志、instrumentation、活动/遥测一致性测试。
- 文件存在检查、首张图片查找、截图安全代理。

## 现有两份文档已覆盖的功能

现有两份文档对以下“模型旁悬浮窗 7 日教程”内容覆盖较完整：

- Day 1：首页相遇、语音入口、截图/图片附件、设置入口。
- Day 2：屏幕分享、屏幕/窗口来源、麦克风弹窗、通话边界。
- Day 3：Agent、状态栏、总开关、键鼠/浏览器控制、专属桌面、任务 HUD。
- Day 4：用户插件、插件管理入口、OpenClaw。
- Day 5：角色设置、模型管理、模型细调、声音克隆、API Key。
- Day 6：对话设置、主动搭话、隐私模式、动画表现。
- Day 7：记忆浏览、记忆整理、编辑清理、教程重置、存储位置、毕业。
- 通用规则：每天最多一轮、错过多天只补最早未完成轮、教程演示不保存用户配置变更、敏感能力强调用户控制权。

## 容易漏写的功能

以下功能在现有两份文档中没有系统展开，建议在新功能整理、进阶教程或 Day 5-7 的简短提示中补上：

| 功能 | 承载位置 | 代码入口线索 | 建议处理 |
| --- | --- | --- | --- |
| 导入图片按钮 | 首页聊天窗 | React 聊天窗 `importImageButtonLabel` | Day 1 和截图附件一起讲，避免用户只知道截图、不知道可导入本地图片。 |
| 翻译按钮 | 首页聊天窗 + API | React 聊天窗 `translateButtonLabel`、`/api/translate` | Day 1 或进阶聊天工具说明中补一句。 |
| 点歌台入口 | 首页入口 + 子页面 | React 聊天窗 `jukeboxButtonLabel`、`/jukebox`、`/api/jukebox` | 可放进进阶功能，不必压进 7 日主线。 |
| Galgame 模式按钮 | 首页聊天窗 + 插件/API | React 聊天窗 `galgameModeEnabled`、`/api/galgame/options` | Day 4 插件生态可作为内置插件例子。 |
| 小游戏邀请 | 首页聊天窗 + API | `/api/mini_game/invite/respond`、React choice prompt | Day 6 主动陪伴/互动边界可提一句。 |
| Avatar 互动工具 | 首页聊天窗/鼠标工具 | React `toolIconItems`、棒棒糖/拳头/锤子交互 | Day 6 动画表现旁补“互动工具”小节或进阶说明。 |
| “请她离开/回来”和锁定 | 首页悬浮按钮 | `goodbye`、`return`、`lock icon` | 现有文档标为可选，建议 Day 6 明确纳入边界能力。 |
| 角色卡制作/导入导出 | 子页面 | `/character_card_manager`、`/card_maker`、characters router | Day 5 个性化建议补“角色卡与创意工坊分享”。 |
| Steam 创意工坊 | 子页面 + API | `/character_card_manager`、`/api/steam/workshop` | Day 5 或进阶文档，作为分享/备份/同步能力。 |
| 云存档 | 子页面 + API | `/cloudsave_manager`、`/api/cloudsave` | Day 7 存储位置旁补“云端同步/备份”。 |
| Cookie 登录管理 | 子页面 + API | `/cookies_login`、`/api/auth` | 插件/外部服务进阶文档，不建议新手 7 日主线展开。 |
| 插件服务管理能力 | 子页面 + 后台服务 | `plugin/server/routes/*`、插件管理 UI `/ui` | Day 4 只讲用户视角；另写开发者/高级用户插件管理说明。 |
| 插件内置清单 | 后台插件 + 部分插件 UI | `plugin/plugins/*/plugin.toml` | Day 4 加“内置插件示例”，避免用户不知道生态范围。 |
| Agent 任务校正/完成 | 首页 HUD + API | `/api/agent/tasks/{task_id}/correction`、`complete` | Day 3 HUD 可补“任务可取消，也会有结果/校正反馈”。 |
| MCP Adapter | 后台插件 + Agent 能力 | `mcp_adapter` 插件、Agent MCP availability | Day 4 插件生态或 Agent 高级能力中补充。 |
| 遥测 opt-out | 后台能力/隐私说明 | README、`utils/token_tracker.py` | 不适合 Day 1-7 主线，但需要独立隐私说明入口。 |
| 存储启动受限状态 | 首页提示 + 子页面/API | `/api/storage/location/bootstrap/status` | Day 7 存储位置说明中补“存储未就绪时部分页面受限”。 |
| Study Companion | 插件子页面/surface | `study_companion` surfaces | Day 4 内置插件例子或独立学习模式文档。 |
| 生活助手/米家/QQ/B 站/提醒/搜索 | 插件子页面/API | 内置插件 manifest | Day 4 用例清单里补充。 |
| 游戏路线与 Minecraft/STS2 | 首页聊天/插件/API | `game_router.py`、对应插件 | Day 4 或进阶“游戏陪玩”功能整理。 |
| 点歌台/音乐代理 | 子页面 + API | `music_router.py`、`jukebox_router.py` | 进阶娱乐功能整理。 |
| 监看端/字幕/Toast | 子页面/独立窗口 | `app/monitor.py`、templates | 进阶页面清单。 |

## 建议更新到 7 日教程的最小补丁

为了不把新手教程讲得过满，建议只把下面这些补充进 7 日主线：

1. Day 1：截图附件小节改成“截图、粘贴图片、导入图片”，并顺手提到聊天工具栏还有翻译/点歌等进阶按钮。
2. Day 3：任务 HUD 小节补充“任务会有结果状态，必要时可以取消或等待校正反馈”。
3. Day 4：插件生态加入内置插件示例：B 站弹幕/私信、生活助手、米家、备忘提醒、网络搜索、学习陪伴、Galgame、Minecraft、STS2、MCP Adapter。
4. Day 5：角色/模型/声音/API 后补“角色卡、创意工坊和云存档是分享与备份入口”，不展开操作。
5. Day 6：把锁定、“请她离开/回来”、Avatar 互动工具列为相处边界和互动表现的一部分。
6. Day 7：存储位置小节补充“云存档、存储启动受限状态、遥测 opt-out 指向独立隐私说明”。

## 不建议塞进 7 日教程的功能

这些功能存在代码入口，但更适合单独文档或高级设置页，不建议压进 7 日新手主线：

- Docker、SSL、部署、调试健康页。
- 插件 CLI、打包、验证、上传和 Hosted UI 开发细节。
- 遥测协议、HMAC、防重放、限流等实现细节。
- 记忆证据链、反思算法、归档细节。
- Steam SDK 初始化、成就、游玩时长等平台细节。
- Cookie 登录具体平台流程。
- 各内置插件的完整操作教程。

## 结论

现有两份悬浮窗教程文档已经覆盖了“用户第一次认识桌面陪伴核心能力”的主线，但它们不是全量软件功能说明。按前端承载位置看，当前功能可分成三层：

1. 首页层：Avatar、聊天、语音、屏幕、设置弹窗、主动陪伴、Agent HUD 和新手教程。
2. 子页面层：模型管理、情感映射、声音克隆、API Key、角色卡、创意工坊、云存档、记忆浏览、Cookie 登录、点歌台、字幕和插件 UI。
3. 后台/API 层：记忆服务、Agent 执行、插件运行、游戏陪玩、Steam/云存档、遥测、存储位置和部署诊断。

推荐后续文档结构：

- 保留两份 7 日教程文档，作为新手引导落地依据。
- 用本文作为全量功能盘点和查漏表。
- 另开“进阶功能入口说明”文档，专门讲点歌台、创意工坊、云存档、插件、Cookie 登录、游戏陪玩和学习陪伴。
