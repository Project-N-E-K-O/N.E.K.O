# Developer Notes

These rules are current repository contracts. Historical incident details belong in the owning rule, test, or design record rather than copied model/version tables.

## Mandatory rules

### Python through uv

```bash
uv run python launcher.py
uv run pytest
uv run ruff check .
```

Do not document or run project Python with bare `python`, `pytest`, or ad-hoc `pip`.

### Eight-locale i18n

The runtime locale set is:

```text
en, ja, ko, zh-CN, zh-TW, ru, pt, es
```

A user-visible i18n change updates every file in `static/locales/`. Plugin-manager locale changes follow their own synchronized locale group. CI enforces lockstep diffs.

### Privacy-sensitive output

Raw conversation or other privacy-sensitive user text may use `print` only; do not send it through project `logger` calls. System events may use the configured logger when they contain no raw private content.

### Prompt watermark

Translations or rearrangements of system prompts must preserve `======以上为`.

### Structural symmetry

Provider/backend/feature implementations remain structurally paired. Review peer providers whenever one path is split, renamed, configured, or packaged.

## Runtime boundaries

- Browser development uses `/`, one page/window, and preferred port 48911.
- Electron loads separate routes/windows such as `/chat` and `/subtitle`.
- Static paths, initialization, IPC, and build outputs must work in both contexts.
- `frontend/react-neko-chat/` is the single chat implementation. `index.html` and `chat.html` mount it at `#react-chat-window-root`.
- Legacy `#chat-container` is hidden/deprecated; `app-chat-adapter.js` bridges old `appendMessage()` calls.

## Backend boundaries

- API resources do not end in `/`; avoid Starlette redirects behind proxies.
- Blocking work must not run directly in async handlers.
- Sync configuration writes used during startup should have an async `a*` counterpart for async paths.
- Main package layering and `main_logic/core/` facade contracts are CI-enforced.
- Do not pass `temperature=` to restricted LLM construction/call sites; observe output, timeout, and input budgets.

## Memory

Conversation event persistence, projections, recall candidates, evidence/reflection, persona, and maintenance queues are separate layers. Read [Memory System](/architecture/memory-system) and the implemented design records before modifying them. Do not describe one-hour in-memory plugin context as semantic memory recall.

## Frontend boundaries

The project is not “vanilla JS only.” It includes static/Jinja JavaScript, React chat, and a Vue plugin manager. Check the owning subtree and its tests. Avoid fixed-delay DOM assumptions; follow existing lifecycle/event patterns.

## Steam and packaging

Achievements and cloud state can have external irreversible effects. Use existing test hooks and staging flows; do not test destructive behavior on a real account casually. Packaging changes must follow [Nuitka Packaging](./nuitka-packaging) and the current build workflow.

## Validation

Run the narrowest relevant tests first, then broader tests/builds proportional to risk. The authoritative static gate is `.github/workflows/analyze.yml`; plugin tests, docs build, desktop packaging, and Docker have separate workflows.
