# AGENTS.md

N.E.K.O. (Project N.E.K.O.) — an AI companion "digital life" platform. Python 3.11 FastAPI backend (single merged process) + React/Vue frontends + a plugin ecosystem. Chinese is the working language of the codebase docs and most comments.

**Read these before making changes:**
- `.agent/rules/neko-guide.md` — the authoritative dev rulebook. NOT auto-loaded by non-Antigravity tools; read it explicitly. `.agent/skills/<name>/SKILL.md` holds domain-specific workflows.
- `CONTRIBUTING.md` — PR workflow, mandatory PR-description reports, i18n requirements.
- Plugin work: `CONTEXT.md` (contract glossary) + `plugin/PLUGIN_DEVELOPMENT_GUIDE.md` (SDK v2 full guide).
- Dev docs live in `docs/zh-CN/`; architecture: `docs/architecture/`; design decisions: `docs/design/`.

## Environment & commands

- **Python is pinned to `==3.11.*`** and managed by **uv**. Always `uv sync` then `uv run ...` — never system python, never bare `pytest`.
- Run the app: `uv run python -m app.memory_server` + `uv run python -m app.main_server` (optional: `-m app.agent_server`). Web UI on `http://localhost:48911`. The three servers are merged into one FastAPI process — a change to one affects the others.
- Frontend build (required after any frontend change; built assets are gitignored, so a stale local build silently serves old code):
  - `./build_frontend.sh` (Windows: `build_frontend.bat`) — unpacks bundled models + builds both frontends.
  - Manual: `cd frontend/react-neko-chat && npm ci && npm run build` → output `static/react/neko-chat/neko-chat-window.iife.js`; `cd frontend/plugin-manager && npm ci && npm run build-only`.
- Lint: `ruff check .` is configured with **only the ASYNC210/220/221/222/251 blocking rules** — the real gates are the custom AST walkers in `scripts/check_*.py` (run `uv run python scripts/check_<name>.py`). See CI `analyze.yml` for the full list.
- Unit gate: `uv run pytest tests/unit -q`. Plugin gate: `uv run pytest plugin/tests -q` (plugin/tests has its own pytest.ini). CI runs both on **Windows** — win32 paths are exercised there.
- `pytest.ini` pins `--randomly-seed=20260731`. Debug order with `--randomly-dont-reorganize`; never `-p no:randomly` (breaks the addopts).
- e2e/frontend tests need `--run-e2e`, Playwright browsers (`uv run playwright install`), and `tests/api_keys.json` (copy from `.template`). `performance` marker needs `RUN_PERF_TESTS=true`.

## Architecture facts that change how you work

- **Zero-blocking async policy.** One event loop serves main/memory/agent servers. In `async def` paths: no sync file IO (`json.load`, `atomic_write_json`), no sync SQLite, no `httpx.Client`/`requests`/`time.sleep`. Use the `a*` mirrors (`atomic_write_json_async`, `memory/timeindex.py` a* methods) or `asyncio.to_thread`. Enforced by `scripts/check_async_blocking.py`.
- **Strict layer ordering** (enforced, including dynamic imports): `config/steamworks → utils → memory/main_logic → main_routers → plugin → brain → app`. `scripts/check_module_layering.py --show-layers` prints it.
- **Chat UI is React-only.** `frontend/react-neko-chat` produces the IIFE bundle mounted by both `index.html` (floating overlay) and `chat.html` (fullscreen). The old `#chat-container` DOM chat is dead — `app-chat-adapter.js` shims legacy calls. Dev web mode = single `/` window; Electron dist = separate `/chat`, `/subtitle` windows — consider both when touching routing.
- **No trailing slashes on API routes** — backend decorators (`@router.get('')`, not `'/'`) and frontend `fetch('/api/foo')`. A trailing slash triggers Starlette's 307 to an absolute URL that breaks behind proxies (past regression, two CI lints).
- **Logging: `utils.logger_config` only** — loguru/structlog/logbook imports fail CI. Anything containing raw conversation text must use `print`, never `logger`.
- **LLM call contract** (all CI-enforced): never pass `temperature=`; every call needs output token budget + `timeout=` and input budget via `truncate_to_tokens`; get model/base_url/key from `config_manager.get_model_api_config(tier)`, never hardcoded fallbacks; heavy SDKs (openai/anthropic/bs4/...) only imported in-function (startup-import gate).
- **Symmetry is a hard requirement.** Providers (TTS/voice/etc.) get structurally symmetric paths; `core.py` must stay provider-agnostic; never add `_2`-suffixed duplicates — extract a method. New native voice providers register via `utils/native_voice_registry.py`, no new `if core_api_type ==` branches.
- Telemetry is on by default; `DO_NOT_TRACK=1` disables it.

## Conventions & CI gates to respect

- **i18n**: user-facing strings must support all 8 locales (`static/locales/` en/ja/ko/zh-CN/zh-TW/ru/es/pt + plugin-manager locale group) — all files must change in lockstep with identical line ranges. Backend multilingual dicts live in `config/prompts/prompts_*.py`, not inline. Prompt strings at LLM call sites must be English-bodied (CJK ratio <30%). Docstrings in main code: English (CJK docstrings fail on PR diff, `plugin/` and `local_server/` exempt).
- **PR description gates** (`scripts/check_pr_report.py`): touching `*.py` under `app/`, `main_logic/`, `memory/`, or `main_routers/` requires a non-empty 回归报告/Regression Report section; >20 counted files requires 不拆分理由/Why Not Split; UI changes need before/after screenshots.
- Codebase is bilingual — match the file's existing language for comments; PR text usually Chinese.

## Plugin subsystem

- Plugin authoring is confined to `plugin/plugins/<plugin_id>/`. Do NOT modify the platform layer (`plugin/sdk`, `plugin/server`, `plugin/core`, manifest/registry internals) without explicit escalation.
- Plugin/Adapter packages run as **separate processes over ZMQ IPC**, not in-process. `neko-plugin` is the CLI (`plugin.neko_plugin_cli.cli:main`).
- `plugin/plugins/neko_live/AGENTS.md` is an example of the per-plugin agent contract.

## Repo quirks

- `.git` history is ~390 MB — use partial clone (`git clone --filter=blob:none`) for fresh checkouts.
- `docs/` ships as VitePress with itself as deploy root: markdown links must not start with `..` (CI-enforced).
- `deps/` holds local wheels/forks pinned via `[tool.uv.sources]` (pyncm-async, rapidocr-pillow) — don't "fix" these to PyPI. The `galgame` dependency group (OCR) is NOT installed by default; code import-guards it.
