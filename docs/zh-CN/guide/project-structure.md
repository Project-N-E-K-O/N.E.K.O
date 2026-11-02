# 项目结构

下图描述稳定的所有权边界，不记录易过期行数。

```text
N.E.K.O/
├── app/                     # main/memory/agent 服务包
├── launcher.py              # 薄公开入口
├── launcher_core/           # bootstrap、端口、进程生命周期
├── brain/                   # Agent 路由/适配器
├── config/                  # 默认值、prompt、Provider/网络数据
├── knowledge/               # 公共知识包（运行在 Memory Server 内）
├── main_logic/              # 对话、客户端、TTS、总线
├── main_routers/            # 主 FastAPI 路由
├── memory/                  # fact/recall/persona/reflection/event/outbox
├── plugin/                  # SDK、host/server、内置插件、工具
├── services/                # 领域服务：聊天同步、小剧场运行时
├── utils/config_manager/    # 可写配置与存储包
├── frontend/
│   ├── react-neko-chat/     # 唯一真实聊天 UI
│   └── plugin-manager/      # Vue UI
├── static/                  # 运行时资源与 8 个 locale
├── templates/               # main/chat/subtitle/settings/features
├── docker/                  # Docker/Compose/entrypoint
├── scripts/                 # CI/校验/打包/资源
├── specs/                   # PyInstaller spec
├── tests/                   # unit/integration/frontend/e2e/contracts
├── docs/                    # VitePress
├── pyproject.toml
└── uv.lock
```

`launcher.py` 委托给 `launcher_core/`；`utils/config_manager/` 管可写配置，`config/` 管内置默认值。`react-neko-chat/` 是唯一聊天 UI，`index.html` 与 `chat.html` 共用，旧 `#chat-container` 已废弃。

`main_logic/core/` 受 CI 结构契约约束。`main_logic/voice_turn/` 只拥有
Provider 无关的语音合同；本地 Silero/Smart Turn 实现与模型资源归
`main_logic/asr_client/endpointing/`。Docker 文件不在仓库根。依赖契约是
`pyproject.toml` + `uv.lock`，`requirements.txt` 不是推荐安装入口。

`services/` 是 L2 包，与 `main_logic/`、`memory/`、`knowledge/` 同层：
`main_logic/`、`main_routers/`、`app/` 可以 import 它，它绝不 import
`main_logic/`、`main_routers/` 或 L2 以上的任何包。它需要的上层能力（已保存的社区
会话、在线会话管理器、角色改名）由各自的所属模块在被 import 时注册进
`services/chat_sync/host.py`。`knowledge/` 运行在 Memory Server 内，Main 侧代码
（含 `services/`）只经 HTTP 访问它。`scripts/check_module_layering.py` 强制这些
层级；新增含 Python 代码的顶层目录必须登记进它的 `LAYERS`，否则 CI 失败。

编辑前用 `rg`、import 和 route 找当前 owner；历史 Issue 中的单文件可能已拆成 package。
