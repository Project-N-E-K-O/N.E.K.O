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
