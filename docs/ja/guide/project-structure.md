# プロジェクト構造

Line count ではなく ownership boundaries を示します。

```text
N.E.K.O/
├── app/                     # main/memory/agent services
├── launcher.py              # thin entrypoint
├── launcher_core/           # bootstrap/ports/lifecycle
├── brain/                   # Agent routing/adapters
├── config/                  # defaults/prompts/provider/network
├── knowledge/               # public knowledge packs (Memory Server)
├── main_logic/              # conversation/clients/TTS/buses
├── main_routers/            # Main FastAPI routes
├── memory/                  # fact/recall/persona/reflection/event/outbox
├── plugin/                  # SDK/host/built-ins/tools
├── services/                # domain services: chat sync/theater runtime
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

`main_logic/core/` is protected by structural CI checks。
`main_logic/voice_turn/` owns only provider-neutral voice contracts; the local
Silero/Smart Turn implementation and model assets live under
`main_logic/asr_client/endpointing/`。Docker files are under `docker/`。
Dependency contract is `pyproject.toml` + `uv.lock`, not a manual
`requirements.txt` install.

`services/` is an L2 package next to `main_logic/`、`memory/`、`knowledge/`。
`main_logic/`、`main_routers/`、`app/` may import it, but it never imports
`main_logic/`、`main_routers/` or anything above L2. Capabilities it needs from
those layers (saved community session、live session manager、character rename)
are registered into `services/chat_sync/host.py` by their owning modules at
import time。`knowledge/` runs inside the Memory Server; Main-side code,
`services/` included, reaches it over HTTP only。
`scripts/check_module_layering.py` enforces these layers; a new top-level
directory with Python code must be added to its `LAYERS`, or CI fails.

Use `rg`, imports, routes to find the current owner; historical files may now be packages.
