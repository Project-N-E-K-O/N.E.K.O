# Manual Source Setup

## Install

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

Python is pinned to 3.11. Run every Python module, script, test, and temporary command through `uv run`.

## Build frontend assets

From the repository root:

```powershell
.\build_frontend.bat
```

```bash
./build_frontend.sh
```

Both scripts verify/unpack the Yui Origin asset, run `npm ci`, build `frontend/plugin-manager/dist/index.html`, and build `static/react/neko-chat/neko-chat-window.iife.js`.

## Run normally

```bash
uv run python launcher.py
```

The launcher plans ports, starts memory/main/agent services, coordinates shutdown, and follows the desktop startup path more closely than split mode. It also applies any staged cloud-save snapshot before server startup. Open the URL it reports; 48911 is only the preferred main port.

## Diagnostic split mode

Use separate terminals only to isolate services:

```bash
uv run python -m app.memory_server
uv run python -m app.main_server
uv run python -m app.agent_server
```

The main UI can load with memory and main, but Agent, hosted-plugin, browser/computer-use, and related capabilities require agent/tool. Split mode does not reproduce launcher fallback ports or coordinated lifecycle behavior.

## Cloud-save notes

- Validate the Steam RemoteStorage path through Steam or the desktop launcher.
- Main-server split mode can perform the fallback staged-snapshot import and notify memory to reload.
- Shutdown does not automatically stage runtime changes. Use Cloud Save Manager to prepare/replace the per-character snapshot intended for upload.
- On macOS source runs, only if Gatekeeper blocks the local unnotarized Steamworks libraries, launch from the repository root and apply the documented quarantine/signing workaround to the two `steamworks/*.dylib` files; do not run that workaround preemptively.

## Configure and verify

Open `/api_key` on the reported main URL, select current Core/Assist providers, enter their credentials, and run the connectivity checks. Source mode does not consume Docker's API-initialization variables.

Use `/health` for startup diagnosis and [Local Embedding Model Assets](./embedding-models) when preparing optional vectors.
