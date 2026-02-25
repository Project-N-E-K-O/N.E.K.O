# Project Structure

```
N.E.K.O/
├── main_server.py              # Main server entry point (port 48911)
├── memory_server.py            # Memory server entry point (port 48912)
├── agent_server.py             # Agent server entry point (port 48915)
├── launcher.py                 # Desktop launcher (Steam/exe)
├── monitor.py                  # Monitor service
│
├── brain/                      # Agent & task execution
│   ├── task_executor.py        # Main task execution engine
│   ├── computer_use.py         # Computer vision/interaction
│   ├── browser_use_adapter.py  # Browser automation adapter
│   ├── mcp_client.py           # Model Context Protocol client
│   ├── planner.py              # Task planning & decomposition
│   ├── analyzer.py             # Result analysis
│   ├── deduper.py              # Duplicate detection
│   ├── processor.py            # Task processing pipeline
│   └── agent_session.py        # Agent session management
│
├── config/                     # Configuration
│   ├── __init__.py             # Constants, defaults, port definitions
│   ├── api_providers.json      # API provider profiles
│   ├── prompts_sys.py          # System prompts (emotion, proactive chat)
│   └── prompts_chara.py        # Character system prompts
│
├── main_logic/                 # Core business logic
│   ├── core.py                 # LLMSessionManager (central session handler)
│   ├── omni_realtime_client.py # Realtime API WebSocket client
│   ├── omni_offline_client.py  # Text/Response API client (offline fallback)
│   ├── tts_client.py           # TTS engine adapter (CosyVoice, GPT-SoVITS)
│   ├── cross_server.py         # Inter-server communication
│   └── agent_event_bus.py      # ZeroMQ event bridge (main ↔ agent)
│
├── main_routers/               # FastAPI route handlers
│   ├── websocket_router.py     # WebSocket /ws/{lanlan_name}
│   ├── characters_router.py    # /api/characters/*
│   ├── config_router.py        # /api/config/*
│   ├── live2d_router.py        # /api/live2d/*
│   ├── vrm_router.py           # /api/model/vrm/*
│   ├── memory_router.py        # /api/memory/*
│   ├── agent_router.py         # /api/agent/*
│   ├── workshop_router.py      # /api/steam/workshop/*
│   ├── system_router.py        # /api/* (misc system endpoints)
│   ├── pages_router.py         # HTML page serving
│   └── shared_state.py         # Global state shared across routers
│
├── memory/                     # Memory management
│   └── store/                  # Memory data storage (SQLite)
│
├── plugin/                     # Plugin system
│   ├── sdk/                    # Plugin SDK (base class, decorators)
│   │   ├── base.py             # NekoPluginBase
│   │   └── decorators.py       # @neko_plugin, @plugin_entry, etc.
│   └── plugins/                # User plugin directory
│
├── utils/                      # Utility modules
│   ├── config_manager.py       # Centralized config management (1500+ lines)
│   ├── language_utils.py       # i18n, language detection, translation
│   ├── audio_processor.py      # Audio resampling, noise reduction
│   ├── frontend_utils.py       # Model discovery, text utilities
│   ├── api_config_loader.py    # API provider resolution
│   ├── logger_config.py        # Logging setup with rate limiting
│   ├── translation_service.py  # LLM-backed translation
│   ├── workshop_utils.py       # Steam Workshop helpers
│   ├── web_scraper.py          # Web content scraping & filtering
│   └── screenshot_utils.py     # Screenshot processing for vision APIs
│
├── static/                     # Frontend assets
│   ├── app.js                  # Main application JS
│   ├── theme-manager.js        # Dark/light mode
│   ├── css/                    # Stylesheets
│   ├── js/                     # Feature-specific JS modules
│   ├── locales/                # i18n JSON files (en, zh-CN, zh-TW, ja, ko)
│   └── live2d-ui-*.js          # Live2D UI components
│
├── templates/                  # Jinja2 HTML templates
│   ├── index.html              # Main interface
│   ├── chara_manager.html      # Character management
│   ├── api_key_settings.html   # API key config
│   └── ...                     # Other page templates
│
├── docker/                     # Docker deployment files
│   ├── Dockerfile
│   ├── docker-compose.yml
│   ├── entrypoint.sh
│   └── CONFIG_REFERENCE.md     # Configuration reference
│
├── tests/                      # Test suite
│   ├── unit/                   # Unit tests
│   ├── frontend/               # Frontend integration tests (Playwright)
│   ├── e2e/                    # End-to-end tests
│   └── utils/                  # Test utilities
│
├── pyproject.toml              # Project metadata & dependencies
└── requirements.txt            # Pinned dependency list
```

## Key files

| File | Lines | Role |
|------|-------|------|
| `main_logic/core.py` | ~2300 | Central session manager — the heart of the system |
| `utils/config_manager.py` | ~1500 | Configuration loading, validation, persistence |
| `main_logic/tts_client.py` | ~2300 | TTS synthesis with multi-provider support |
| `brain/task_executor.py` | ~1600 | Agent task planning and execution |
| `utils/web_scraper.py` | ~1900 | Web content scraping for proactive chat |
