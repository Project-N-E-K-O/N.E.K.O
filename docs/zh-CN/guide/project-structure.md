# 项目结构

下图描述稳定的所有权边界，不记录易过期行数。

```text
N.E.K.O/
├── app/                     # main/memory/agent 服务包
├── launcher.py              # 薄公开入口
├── launcher_core/           # bootstrap、端口、进程生命周期
├── brain/                   # Agent 路由/适配器
├── config/                  # 默认值、prompt、Provider/网络数据
├── main_logic/              # 对话、客户端、TTS、总线
├── main_routers/            # 主 FastAPI 路由
├── memory/                  # fact/recall/persona/reflection/event/outbox
├── plugin/                  # SDK、host/server、内置插件、工具
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

`main_logic/core/` 受 CI 结构契约约束。Docker 文件不在仓库根。依赖契约是 `pyproject.toml` + `uv.lock`，`requirements.txt` 不是推荐安装入口。

编辑前用 `rg`、import 和 route 找当前 owner；历史 Issue 中的单文件可能已拆成 package。
