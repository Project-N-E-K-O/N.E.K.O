# Development Setup

## Clone and synchronize

```bash
git clone https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

`uv` selects the Python 3.11 environment pinned by `pyproject.toml`. Run modules, scripts, pytest, and temporary Python commands through `uv run`.

## Build frontend assets

```powershell
.\build_frontend.bat
```

```bash
./build_frontend.sh
```

The scripts verify/unpack `assets/yui-origin.tar.gz`, run `npm ci`, build the Vue plugin manager to `frontend/plugin-manager/dist/`, and build React chat to `static/react/neko-chat/`.

For iterative work, run `npm ci && npm run dev` in the owning frontend directory. Plugin manager uses port 5173 and proxies to `VITE_BACKEND_URL` or `http://localhost:48916`; React chat uses port 5174. Its production bundle is mounted by both `templates/index.html` and `templates/chat.html`.

## Start and verify

```bash
uv run python launcher.py
```

Open the URL reported by the launcher, normally `http://127.0.0.1:48911`. Configure providers at `/api_key`; use `/health` for startup diagnosis.

## Basic checks

```bash
uv run pytest
uv run ruff check .
```

CI also runs repository-specific checks for async blocking, banned logging imports, prompt/i18n rules, LLM budgets, API trailing slashes, module layering, docs paths, and core contracts. Read `.github/workflows/analyze.yml` before claiming local CI parity.
