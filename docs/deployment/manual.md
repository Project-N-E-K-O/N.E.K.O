# Manual Setup

For development and customization on any platform.

## Prerequisites

- Python 3.11 (exactly — not 3.12+)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) package manager
- Node.js (>=20.19)
- Git

## Installation

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

## Optional Local Embeddings

Vector memory uses optional local ONNX assets. See
[`embedding-models.md`](embedding-models.md) for the download command, expected
directory layout, and packaging notes for PyInstaller/Nuitka builds.

## Build Frontend

The project has two frontend projects under `frontend/` that must be built before running.

**Recommended** — use the convenience script from the project root. This is the officially supported build path:

```bash
# Windows
build_frontend.bat

# Linux / macOS
./build_frontend.sh
```

If you need to run the commands manually, they must match what the script does:

```bash
cd frontend/react-neko-chat && npm install && npm run build && cd ../..
cd frontend/plugin-manager && npm install && npm run build-only && cd ../..
```

## Running

Prefer the unified launcher when possible:

```bash
uv run python launcher.py
```

This path bootstraps local `cloudsave/`, applies any staged snapshot, and only
then starts the backend services, so it is closer to the real Steam / desktop
startup path.

Alternatively, start the required servers manually in separate terminals:

```bash
# Terminal 1 — Memory server (required)
uv run python memory_server.py

# Terminal 2 — Main server (required)
uv run python main_server.py

# Terminal 3 — Agent server (optional)
uv run python agent_server.py
```

Notes:

- To validate the production Steam Auto-Cloud path, launch through Steam or the desktop launcher. Desktop source runs on Windows, macOS, and Linux can now use the RemoteStorage bundle helper when Steam is running and logged in, but that helper is still a development-side compatibility path rather than the packaged app's main sync path.
- In manual three-server mode, `main_server` will still perform a fallback snapshot import when needed and will try to notify `memory_server` to reload afterward.
- Shutdown no longer stages runtime changes into `cloudsave/` automatically. If you want Steam to upload new character data, prepare or overwrite the staged snapshot for that character from Cloud Save Manager before you exit.
- On macOS source runs, if Apple reports that `SteamworksPy.dylib` cannot be verified, Gatekeeper is usually blocking the local unnotarized Steamworks libraries. First make sure you are launching from the project root. If it is still blocked, run the following from the repo root:

```bash
xattr -dr com.apple.quarantine SteamworksPy.dylib libsteam_api.dylib
codesign --force --sign - libsteam_api.dylib
codesign --force --sign - SteamworksPy.dylib
```

- After that, retry `uv run python launcher.py` or `uv run python main_server.py`.

## Configuration

1. Open `http://localhost:48911/api_key` in your browser
2. Select your Core API provider
3. Enter your API key
4. Click Save

Alternatively, set environment variables before starting:

```bash
export NEKO_CORE_API_KEY="sk-your-key"
export NEKO_CORE_API="qwen"
uv run python main_server.py
```

## Alternative: pip install

If you prefer pip over uv:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python memory_server.py
python main_server.py
```

## Verify

Open `http://localhost:48911` — you should see the character interface.
