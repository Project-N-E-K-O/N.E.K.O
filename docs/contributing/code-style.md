# Code Style

## Python

- **Python 3.11** — Required; do not use 3.12+ features
- **Type hints** — Use where practical, especially for public APIs
- **Async** — Use `async/await` for I/O operations in FastAPI handlers
- **Imports** — Standard library first, then third-party, then local
- **Line length** — No strict limit, but keep reasonable (~120 chars)

## JavaScript

- **ES6+** — Use modern syntax (arrow functions, const/let, template literals)
- **No framework** — The frontend uses vanilla JS by design
- **i18n** — All user-facing strings should use the locale system

## API URLs — no trailing slash

Every backend API endpoint and every frontend caller MUST use the no-trailing-slash form:

- ✅ `/api/characters`, `/api/live2d/models`, `/api/memory/funnel/{lanlan_name}`
- ❌ `/api/characters/`, `/api/live2d/models/`

Rationale (see `.agent/rules/neko-guide.md` for the full incident write-up):

1. **Industry convention.** Stripe, GitHub, Google, AWS, and the Microsoft REST API Guidelines all forbid trailing slashes on REST resources. We follow the same convention so callers' instincts match.
2. **Reverse-proxy safety.** FastAPI/Starlette's `redirect_slashes=True` (default) sends a 307 to the no-slash form with an **absolute** `Location` header built from the request's `Host`. Behind a reverse proxy that doesn't preserve `Host` (or with `proxy_headers` off), that absolute URL points at the internal `127.0.0.1:<port>` and the browser fails with `ERR_CONNECTION_REFUSED`. The bug we shipped in PR #938 (chara_manager regression on LAN reverse proxies) is exactly this. Avoiding the redirect entirely sidesteps the whole class of failure.

Concrete rules:

- **Backend** — `APIRouter(prefix="/api/foo")` + `@router.get('')` (NOT `@router.get('/')`). The only exception is the literal root page `@router.get("/")` in `pages_router.py`.
- **Frontend** — `fetch('/api/foo')`, never `fetch('/api/foo/')`. Prefix builders that concatenate a variable (e.g. `` `/api/foo/${id}` ``, `` '/api/foo/' + encodeURIComponent(name) ``) are fine — the slash is a path separator, the final URL still has no trailing slash.

Both rules are enforced by CI:

- `scripts/check_api_trailing_slash.py` — backend AST check on `main_routers/*.py` and `*_server.py`
- `scripts/check_frontend_api_trailing_slash.py` — frontend regex check on `static/`, `frontend/`, `templates/`

## Commit messages

Follow conventional commits when possible:

```
feat: add voice preview for custom voices
fix: resolve WebSocket reconnection on character switch
docs: update API reference for memory endpoints
refactor: extract TTS queue logic into separate module
```

## Pull requests

- Keep PRs focused on a single concern
- Include a description of what changed and why
- Reference related issues if applicable
- Ensure `uv run pytest` passes
