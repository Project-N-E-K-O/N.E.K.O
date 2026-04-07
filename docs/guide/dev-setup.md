# Development Setup

## Clone the repository

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

## Install dependencies

```bash
uv sync
```

This installs all Python dependencies into a managed virtual environment. The project requires Python 3.11.

## Start the servers

N.E.K.O. runs as multiple cooperating servers. At minimum, you need the **main server** and the **memory server**:

```bash
# Terminal 1 — Memory server
uv run python memory_server.py

# Terminal 2 — Main server
uv run python main_server.py
```

Optionally, start the agent server for background task execution:

```bash
# Terminal 3 — Agent server (optional)
uv run python agent_server.py
```

## Configure API keys

Once the main server is running, open the Web UI to configure your API keys:

```
http://localhost:48911/api_key
```

Select your preferred Core API provider and enter your API key. See [API Providers](/config/api-providers) for details on each provider.

## Verify the setup

Open the main interface:

```
http://localhost:48911
```

You should see the character interface with a Live2D model. Try sending a text message or starting a voice session to verify everything works.

## Default ports

| Server | Port | Purpose |
|--------|------|---------|
| Main server | 48911 | Web UI, REST API, WebSocket |
| Memory server | 48912 | Memory storage and retrieval |
| Monitor server | 48913 | Status monitoring |
| Agent/Tool server | 48915 | Agent task execution |
| Plugin server | 48916 | User plugins |

## Build the frontend projects

The project has two modern frontend projects under `frontend/`. Both must be built before running the full application.

### Quick build (all at once)

```bash
# Windows
build_frontend.bat

# Linux / macOS
./build_frontend.sh
```

### Chat Window (React)

```bash
cd frontend/react-neko-chat
npm install
npm run dev          # Dev server on port 5174
npm run build        # Production build → static/react/neko-chat/
```

The chat window is built as an IIFE library (`NekoChatWindow`) and embedded in `templates/index.html`.

### Plugin Manager (Vue)

```bash
cd frontend/plugin-manager
npm install
npm run dev          # Dev server on port 5173 (proxies API to localhost:48916)
npm run build-only   # Production build → frontend/plugin-manager/dist/
```

The plugin manager dashboard is served at `/ui/` by the plugin server (port 48916).

## Running tests

```bash
uv run pytest
```

See `tests/README.md` for details on the test suite structure and how to run specific test categories.
