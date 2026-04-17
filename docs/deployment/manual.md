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

Start the required servers in separate terminals:

```bash
# Terminal 1 — Memory server (required)
uv run python memory_server.py

# Terminal 2 — Main server (required)
uv run python main_server.py

# Terminal 3 — Agent server (optional)
uv run python agent_server.py
```

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
