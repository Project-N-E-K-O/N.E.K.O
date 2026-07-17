# Code Style

The enforced rules live in `pyproject.toml`, `.agent/rules/neko-guide.md`, and CI scripts.

## Python

- Target Python 3.11.
- Use `uv run` for Python commands.
- Keep async request paths non-blocking; offload blocking filesystem/CPU calls where required.
- Preserve the module-layer order checked by `scripts/check_module_layering.py`.
- Keep heavyweight SDK imports off the startup import chain.
- Do not introduce `loguru`, `structlog`, `logbook`, or `tkinter`; CI rejects them.
- Raw conversation/privacy-sensitive text may only use `print`, never persistent project loggers.

Run `uv run ruff check .` and relevant repository check scripts.

## Frontend

The frontend is hybrid: static/Jinja JavaScript, one React chat application, and a Vue plugin manager. Edit the owner rather than duplicating behavior.

- Chat UI/logic belongs in `frontend/react-neko-chat/`.
- Both `index.html` and Electron `chat.html` mount the same React component.
- Do not add new behavior to legacy `#chat-container`.
- Consider browser `/` and Electron routes such as `/chat` and `/subtitle`.
- Use the i18n system; update all eight locales in lockstep.

## Provider symmetry

Provider/backend/feature paths must remain structurally symmetric. If one peer provider is split or gains a lifecycle/config path, inspect and update the corresponding peers rather than leaving an exceptional branch without justification.

## API paths

Backend decorators and frontend callers use no trailing slash for API resources:

- correct: `/api/characters`
- incorrect: `/api/characters/`

Use `@router.get("")` under an `APIRouter` prefix, except for the literal site root. CI checks backend and frontend forms.

## Prompts and i18n

Keep multi-language prompt tables in owning `config/prompts_*.py` modules and follow prompt-budget/temperature checks. When translating a system prompt, preserve the exact watermark fragment `======以上为`.

## Commits and PRs

Keep one coherent concern per commit/PR. Explain behavior and validation; do not claim tests or platforms you did not run.
