# 项目结构

```
N.E.K.O/
├── main_server.py              # 主服务器入口（端口 48911）
├── memory_server.py            # 记忆服务器入口（端口 48912）
├── agent_server.py             # 智能体服务器入口（端口 48915）
├── launcher.py                 # 桌面启动器（Steam/exe）
├── monitor.py                  # 监控服务
│
├── brain/                      # 智能体与任务执行
│   ├── task_executor.py        # 主任务执行引擎
│   ├── computer_use.py         # 计算机视觉/交互
│   ├── browser_use_adapter.py  # 浏览器自动化适配器
│   ├── mcp_client.py           # Model Context Protocol 客户端
│   ├── planner.py              # 任务规划与分解
│   ├── analyzer.py             # 结果分析
│   ├── deduper.py              # 重复检测
│   ├── processor.py            # 任务处理流水线
│   └── agent_session.py        # 智能体会话管理
│
├── config/                     # 配置
│   ├── __init__.py             # 常量、默认值、端口定义
│   ├── api_providers.json      # API 提供商配置
│   ├── prompts_sys.py          # 系统提示词（情绪、主动聊天）
│   └── prompts_chara.py        # 角色系统提示词
│
├── main_logic/                 # 核心业务逻辑
│   ├── core.py                 # LLMSessionManager（中央会话处理器）
│   ├── omni_realtime_client.py # Realtime API WebSocket 客户端
│   ├── omni_offline_client.py  # 文本/Response API 客户端（离线回退）
│   ├── tts_client.py           # TTS 引擎适配器（CosyVoice、GPT-SoVITS）
│   ├── cross_server.py         # 服务间通信
│   └── agent_event_bus.py      # ZeroMQ 事件桥（主服务器 ↔ 智能体）
│
├── main_routers/               # FastAPI 路由处理器
│   ├── websocket_router.py     # WebSocket /ws/{lanlan_name}
│   ├── characters_router.py    # /api/characters/*
│   ├── config_router.py        # /api/config/*
│   ├── live2d_router.py        # /api/live2d/*
│   ├── vrm_router.py           # /api/model/vrm/*
│   ├── memory_router.py        # /api/memory/*
│   ├── agent_router.py         # /api/agent/*
│   ├── workshop_router.py      # /api/steam/workshop/*
│   ├── system_router.py        # /api/*（杂项系统端点）
│   ├── pages_router.py         # HTML 页面服务
│   └── shared_state.py         # 跨路由共享的全局状态
│
├── memory/                     # 记忆管理
│   └── store/                  # 记忆数据存储（SQLite）
│
├── plugin/                     # 插件系统
│   ├── sdk/                    # 插件 SDK（基类、装饰器）
│   │   ├── base.py             # NekoPluginBase
│   │   └── decorators.py       # @neko_plugin、@plugin_entry 等
│   └── plugins/                # 用户插件目录
│
├── utils/                      # 工具模块
│   ├── config_manager.py       # 集中式配置管理（1500+ 行）
│   ├── language_utils.py       # 国际化、语言检测、翻译
│   ├── audio_processor.py      # 音频重采样、降噪
│   ├── frontend_utils.py       # 模型发现、文本工具
│   ├── api_config_loader.py    # API 提供商解析
│   ├── logger_config.py        # 日志配置（含速率限制）
│   ├── translation_service.py  # 基于 LLM 的翻译
│   ├── workshop_utils.py       # Steam 创意工坊辅助工具
│   ├── web_scraper.py          # 网页内容抓取与过滤
│   └── screenshot_utils.py     # 截图处理（用于视觉 API）
│
├── static/                     # 前端资源
│   ├── app.js                  # 主应用 JS
│   ├── theme-manager.js        # 深色/浅色模式
│   ├── css/                    # 样式表
│   ├── js/                     # 功能模块 JS
│   ├── locales/                # 国际化 JSON 文件（en、zh-CN、zh-TW、ja、ko）
│   └── live2d-ui-*.js          # Live2D UI 组件
│
├── templates/                  # Jinja2 HTML 模板
│   ├── index.html              # 主界面
│   ├── chara_manager.html      # 角色管理
│   ├── api_key_settings.html   # API 密钥配置
│   └── ...                     # 其他页面模板
│
├── docker/                     # Docker 部署文件
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   └── CONFIG_REFERENCE.md     # 配置参考
│
├── tests/                      # 测试套件
│   ├── unit/                   # 单元测试
│   ├── frontend/               # 前端集成测试（Playwright）
│   ├── e2e/                    # 端到端测试
│   └── utils/                  # 测试工具
│
├── pyproject.toml              # 项目元数据与依赖
└── requirements.txt            # 固定依赖列表
```

## 关键文件

| 文件 | 行数 | 作用 |
|------|------|------|
| `main_logic/core.py` | ~2300 | 中央会话管理器 —— 系统的核心 |
| `utils/config_manager.py` | ~1500 | 配置加载、验证、持久化 |
| `main_logic/tts_client.py` | ~2300 | 多提供商 TTS 合成 |
| `brain/task_executor.py` | ~1600 | 智能体任务规划与执行 |
| `utils/web_scraper.py` | ~1900 | 用于主动聊天的网页内容抓取 |
