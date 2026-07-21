# Project Structure

This map describes ownership boundaries, not line counts.

```text
N.E.K.O/
├── app/
│   ├── main_server/          # Main Web/API entrypoint
│   ├── memory_server/        # Memory API/runtime
│   └── agent_server/         # Agent/tool API and channels
├── launcher.py               # Thin public launcher entrypoint
├── launcher_core/            # Bootstrap, ports, process lifecycle
├── brain/                    # Agent routing/execution adapters
├── config/                   # Defaults, prompts, provider/network data
├── main_logic/               # Conversation, clients, TTS, buses
├── main_routers/             # Main FastAPI route packages/modules
├── memory/                   # Facts, recall, persona, reflection, event/outbox
├── plugin/                   # SDK, host/server, built-ins, tooling
├── utils/
│   └── config_manager/       # Writable config/storage package
├── frontend/
│   ├── react-neko-chat/      # Shared React chat implementation
│   └── plugin-manager/       # Vue plugin-manager UI
├── static/                   # Runtime assets and eight JSON locales
├── templates/                # Main/chat/subtitle/settings/feature pages
├── docker/                   # Dockerfiles, Compose, entrypoint
├── scripts/                  # CI, validation, packaging, assets
├── specs/                    # PyInstaller specifications
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
- Docker files are under `docker/`, not the repository root.
- `pyproject.toml` and `uv.lock` are the dependency contract; `requirements.txt` is not the recommended installation entrypoint.

Use `rg`, imports, and routes to find the current owner before editing. A file named in an old issue may now be a package.
