# プロジェクト構造

Line count ではなく ownership boundaries を示します。

```text
N.E.K.O/
├── app/                     # main/memory/agent services
├── launcher.py              # thin entrypoint
├── launcher_core/           # bootstrap/ports/lifecycle
├── brain/                   # Agent routing/adapters
├── config/                  # defaults/prompts/provider/network
├── main_logic/              # conversation/clients/TTS/buses
├── main_routers/            # Main FastAPI routes
├── memory/                  # fact/recall/persona/reflection/event/outbox
├── plugin/                  # SDK/host/built-ins/tools
├── utils/config_manager/    # writable config/storage
├── frontend/react-neko-chat/
├── frontend/plugin-manager/
├── static/                  # runtime assets/eight locales
├── templates/
├── docker/
├── scripts/
├── specs/
├── tests/
├── docs/
├── pyproject.toml
└── uv.lock
```

`launcher.py` delegates to `launcher_core/`。`utils/config_manager/` owns writable config、`config/` owns bundled defaults。`react-neko-chat/` is the only real chat UI and is mounted by `index.html` / `chat.html`; legacy `#chat-container` is deprecated.

`main_logic/core/` is protected by structural CI checks. Docker files are under `docker/`. Dependency contract is `pyproject.toml` + `uv.lock`, not a manual `requirements.txt` install.

Use `rg`, imports, routes to find the current owner; historical files may now be packages.
