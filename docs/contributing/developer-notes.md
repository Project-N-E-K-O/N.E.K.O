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

## Catgirl visit: local development loopback

Visits (design record: [Catgirl visiting infrastructure](/design/visit-infrastructure); Servers contract: [Servers contract](/design/visit-servers-contract)) are enabled by default. Set `NEKO_VISIT_ENABLED=0` to hide the entry points and disable starting or joining new visits during an emergency rollback. What the code supports today:

- **Servers is still required.** Credentials (`POST /api/visit/credentials`) and the ticket public keys (`GET /api/visit/pubkeys`) are always fetched from `NEKO_SOCIAL_BASE_URL` (default: the production community site) with the signed-in community OAuth session. There is no built-in local Servers stand-in, and ticket verification fails closed when the public-key list has never been fetched, so a loopback test needs a Servers test environment that implements the contract, plus one community account per side (redeeming your own invite is rejected).
- **Dev signing key.** `NEKO_VISIT_DEV_KEYFILE=<path>` adds that Ed25519 key's public half under the reserved dev kid. Verification follows the production code path; there is no skip-verify branch. Create a key and sign tickets with the same key using `scripts/visit_dev_mint.py`. A Servers test environment can return those tickets as `identity_ticket`:

  ```bash
  uv run python scripts/visit_dev_mint.py keygen --out <path outside the repo>/visit_dev_key.pem
  uv run python scripts/visit_dev_mint.py pubkey --keyfile <path>/visit_dev_key.pem
  uv run python scripts/visit_dev_mint.py mint --keyfile <path>/visit_dev_key.pem \
      --role guest --visit-id <22 chars> --visit-uid <24 hex>
  ```

- **Local LiveKit.** Run `livekit-server --dev` (dev key pair, plain `ws://127.0.0.1:7880`), or use the LiveKit deployment template on a host with real domains. That template lives in the private N.E.K.O.Connect repository together with the other server-side visit services, not in this repository. `NEKO_VISIT_DEV_LIVEKIT_URL=ws://127.0.0.1:7880` adds that host to the accepted LiveKit hosts. Plain `ws://` is accepted only for this configured host and only when it is a loopback literal. `NEKO_VISIT_DEV_LIVEKIT_API_KEY` / `_SECRET` are read into `config.visit_settings` but no backend code path signs LiveKit JWTs with them. The room JWT still comes from Servers. Note that `--dev` keeps LiveKit's default automatic room creation, unlike the `auto_create: false` deployment.
- **Two backends on one machine.** Give each side its own lock directory (`NEKO_RUNTIME_STATE_DIR`), runtime root (`NEKO_STORAGE_SELECTED_ROOT`; set `NEKO_STORAGE_ANCHOR_ROOT` to the same path), Electron profile (`NEKO_USER_DATA_DIR`), and port set (`NEKO_MAIN_SERVER_PORT`, `NEKO_MEMORY_SERVER_PORT`, `NEKO_MONITOR_SERVER_PORT`, `NEKO_COMMENTER_SERVER_PORT`, `NEKO_TOOL_SERVER_PORT`, `NEKO_USER_PLUGIN_SERVER_PORT`). The launcher accepts the storage-root override only when the lock directory is also isolated, so a normal launch cannot accidentally inherit a stale data root. Start each with `uv run python launcher.py`, then open each UI at `http://127.0.0.1:<main port>/`.

### Deployment limits

- **Reverse-proxy deployments do not support visits.** This includes the Docker image, which sets `NEKO_BEHIND_PROXY=true` in `docker/entrypoint.sh`. Proxy headers rewrite `client.host`, so the local-origin check cannot be trusted. Visits answer 403 `VISIT_E_UNAUTHORIZED`, and the settings page hides the visit group and shows the reason.
- **Non-loopback access (Docker, LAN) is unsupported by default.** The visit gate requires the browser and the backend to be on the same machine, talking over `127.0.0.1` / `::1`.
- **`NEKO_VISIT_ALLOW_NONLOCAL=1` is an escape hatch.** It disables the loopback check, the proxy-mode rejection and the forwarding-header rejection. The operator must authenticate at the proxy and overwrite (not append) `X-Forwarded-For`, at their own risk. Without that, any client that can reach the backend can read invite codes and transcripts and send visit lines on the user's behalf.

## Steam and packaging

Achievements and cloud state can have external irreversible effects. Use existing test hooks and staging flows; do not test destructive behavior on a real account casually. Packaging changes must follow [Nuitka Packaging](./nuitka-packaging) and the current build workflow.

## Validation

Run the narrowest relevant tests first, then broader tests/builds proportional to risk. The authoritative static gate is `.github/workflows/analyze.yml`; plugin tests, docs build, desktop packaging, and Docker have separate workflows.
