# Project Structure

This map describes ownership boundaries, not line counts.

```text
N.E.K.O/
├── app/
│   ├── main_server/          # Main Web/API entrypoint
│   ├── memory_server/        # Memory API/runtime
│   └── agent_server/         # Agent API; hosts NekoPower
├── launcher.py               # Thin public launcher entrypoint
├── launcher_core/            # Bootstrap, ports, process lifecycle
├── config/                   # Defaults, prompts, provider/network data
├── main_logic/               # Conversation, clients, TTS, buses
├── main_routers/             # Main FastAPI route packages/modules
├── memory/                   # Facts, recall, persona, reflection, event/outbox
├── nekopower/
│   ├── host/                 # Task brain, ledger, capability gateway, permissions
│   ├── runtime/              # Agent runtime in which task agents run
│   └── builtin/              # Keyboard-and-mouse and browser tools
├── plugin/                   # SDK, host/server, built-ins, tooling
├── services/                 # Domain services: chat sync, theater runtime, public knowledge (services/knowledge, Memory Server)
├── utils/
│   └── config_manager/       # Writable config/storage package
├── frontend/
│   ├── react-neko-chat/      # Shared React chat implementation
│   └── plugin-manager/       # Vue plugin-manager UI
├── static/                   # Runtime assets and eight JSON locales
├── templates/                # Main/chat/subtitle/settings/feature pages
├── docker/                   # Dockerfiles, Compose, entrypoint
├── scripts/                  # CI, validation, packaging, assets
├── tests/                    # Unit/integration/frontend/e2e/contracts
├── docs/                     # VitePress site
├── pyproject.toml            # Python metadata/dependency groups
└── uv.lock                   # Reproducible dependency lock
```

## Important boundaries

- `launcher.py` delegates to `launcher_core/`.
- `utils/config_manager/` owns writable config/storage; `config/` owns bundled defaults.
- `frontend/react-neko-chat/` is the only real chat UI. `index.html` and `chat.html` mount it; legacy `#chat-container` is deprecated.
- `main_logic/core/` is a package/facade protected by structural CI checks.
- `main_logic/voice_turn/` owns provider-neutral voice contracts; local
  Silero/Smart Turn code and model assets live under
  `main_logic/asr_client/endpointing/`.
- `services/` is an L2 package next to `main_logic/` and `memory/`:
  `main_logic/`, `main_routers/` and `app/` may import it, but it
  never imports `main_logic/`, `main_routers/` or anything above L2. What it
  needs from those layers (saved community session, live session manager,
  character rename) is registered into `services/chat_sync/host.py` by the
  owning module when that module is imported.
- Where a new module goes: unlike `utils/` (generic helpers that know nothing
  about the product) and `main_logic/` (logic tied to the live chat session), a
  package belongs in `services/` only if it meets all three conditions: (1) it
  owns concrete domain state, a protocol or a lifecycle, beyond a generic
  helper; (2) it never imports `main_logic/`, `main_routers/` or `app/`, and
  gets host capabilities through `host.py` hooks; (3) it is a standalone
  subsystem or shared by several subsystems/routers.
  `services/theater_workshop/` is a source-only author SDK and is not shipped
  in the desktop bundle.
- `services/knowledge/` runs inside the Memory Server. `check_module_layering.py` treats it as its own `knowledge` node, so Main-side code,
  other `services/` packages included, reaches it over HTTP only.
- `scripts/check_module_layering.py` enforces these layers. A new top-level
  directory with Python code must be added to its `LAYERS`, or CI fails.
- Docker files are under `docker/`, not the repository root.
- `pyproject.toml` and `uv.lock` are the dependency contract; `requirements.txt` is not the recommended installation entrypoint.

Use `rg`, imports, and routes to find the current owner before editing. A file named in an old issue may now be a package.
