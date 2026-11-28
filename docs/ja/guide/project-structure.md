# プロジェクト構造

Line count ではなく ownership boundaries を示します。

```text
N.E.K.O/
├── app/                     # main/memory/agent services
├── launcher.py              # thin entrypoint
├── launcher_core/           # bootstrap/ports/lifecycle
├── config/                  # defaults/prompts/provider/network
├── main_logic/              # conversation/clients/TTS/buses
├── main_routers/            # Main FastAPI routes
├── memory/                  # fact/recall/persona/reflection/event/outbox
├── nekopower/                # task brain/host (host/), agent runtime (runtime/), keyboard-and-mouse/browser tools (builtin/)
├── plugin/                  # SDK/host/built-ins/tools
├── services/                # domain services: chat sync/theater runtime/public knowledge (services/knowledge, Memory Server)
├── utils/config_manager/    # writable config/storage
├── frontend/react-neko-chat/
├── frontend/plugin-manager/
├── static/                  # runtime assets/eight locales
├── templates/
├── docker/
├── scripts/
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

`services/` is an L2 package next to `main_logic/`、`memory/`。
`main_logic/`、`main_routers/`、`app/` may import it, but it never imports
`main_logic/`、`main_routers/` or anything above L2. Capabilities it needs from
those layers (saved community session、live session manager、character rename)
are registered into `services/chat_sync/host.py` by their owning modules at
import time。`services/knowledge/` runs inside the Memory Server and is its own `knowledge` node in the layering check; Main-side code,
other `services/` packages included, reaches it over HTTP only。
`scripts/check_module_layering.py` enforces these layers; a new top-level
directory with Python code must be added to its `LAYERS`, or CI fails.

New module placement: unlike `utils/` (generic helpers that know nothing about the product) and `main_logic/` (logic tied to the live chat session), a package belongs in `services/` only if it meets all three conditions: (1) it owns concrete domain state, a protocol or a lifecycle beyond a generic helper; (2) it never imports `main_logic/`、`main_routers/`、`app/`, and gets host capabilities through `host.py` hooks; (3) it is a standalone subsystem or shared by several subsystems/routers. `services/theater_workshop/` is a source-only author SDK and is not shipped in the desktop bundle.

Use `rg`, imports, routes to find the current owner; historical files may now be packages.
