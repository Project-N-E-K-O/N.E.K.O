
<div align="center">

![Project N.E.K.O.](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/neko_logo.jpg)

[简体中文](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/README.MD) · [日本語](README_ja.md) · [Русский](README_ru.md)

# Project N.E.K.O.

A local-first AI companion runtime with browser and Electron surfaces, persistent memory, embodied avatars, Agent capabilities, and a plugin SDK.

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Apache License 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE)
[![Developer Docs](https://img.shields.io/badge/Developer_Docs-project--neko.online-40C5F1)](https://project-neko.online)
[![Steam](https://img.shields.io/badge/Steam-N.E.K.O.-000000?logo=steam)](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_readme&utm_content=badge_en)

</div>

This file is a concise repository overview. The [developer documentation](https://project-neko.online) is the source for current architecture, setup, configuration, API, deployment, plugin, and contribution details. It deliberately avoids provider/model inventories, pricing, product-version promises, and copied roadmap dates.

## Current repository boundaries

- **Conversation runtime:** text, audio, and vision pipelines with character configuration.
- **Avatar surfaces:** Live2D, VRM, MMD, PNGTuber, and desktop-pet-related paths.
- **Memory:** persisted conversation events, projections, recall candidates, evidence/reflection, persona, and maintenance queues.
- **Agents:** browser and computer automation, task-state transport, external Agent adapters, and runtime tool services.
- **Plugins:** SDK contracts, built-in plugins, hosted surfaces, lifecycle hooks, routing, and packaging gates.
- **Frontends:** static/Jinja pages, one React chat implementation, and a Vue plugin manager. Browser `/` and Electron routes such as `/chat` and `/subtitle` are separate runtime contexts.

An implementation being present does not guarantee equal support for every provider, platform, distribution, or optional integration.

## Run from source

Requirements:

- Python exactly 3.11;
- [uv](https://docs.astral.sh/uv/);
- Node.js `^20.19.0 || >=22.12.0` when rebuilding frontends.

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

Build both frontend projects on the first checkout or after frontend changes:

```bash
# Linux / macOS
./build_frontend.sh
```

```powershell
# Windows PowerShell
.\build_frontend.bat
```

Start the supported service suite:

```bash
uv run python launcher.py
```

Open `http://127.0.0.1:48911`. See [development setup](guide/dev-setup.md) and [quick start](guide/quick-start.md) before splitting services manually.

## Ports and deployment

| Context | Host port | Meaning |
| --- | ---: | --- |
| Source runtime | `48911` | Main Web/API service |
| Source runtime | `48912` | Memory service |
| Docker Compose | `48911` | Nginx HTTP entry |
| Docker Compose | `48912` | Nginx HTTPS entry |

These are two different port models. Other internal/default service ports and overrides are documented in [environment variables](config/environment-vars.md).

The tracked Compose file pulls an image; it has no `build:` section. Use:

```bash
docker compose up -d
```

For local image builds, storage, TLS, and image selection, follow the [Docker guide](deployment/docker.md). For source and desktop artifacts, start from the [deployment overview](deployment/index.md).

## Documentation map

- [Getting started](guide/index.md)
- [Architecture](architecture/index.md)
- [API reference](api/index.md)
- [Configuration](config/index.md)
- [Frontend](frontend/index.md)
- [Plugin development](plugins/index.md)
- [Deployment](deployment/index.md)
- [Contributing](contributing/index.md)

The API/provider configuration is schema-driven. Use the current settings UI, `config/api_providers.json`, and [field reference](api_providers_fields.md) instead of a copied provider or model list.

## Privacy and telemetry

The current opt-out recognized by the runtime is `DO_NOT_TRACK=1` or `NEKO_DO_NOT_TRACK=1`. Consult the repository-root README and `utils/token_tracker/` in the revision you run for the current telemetry disclosure; this short translation does not duplicate volatile payload details.

## Contributing and license

Read `.agent/rules/neko-guide.md` and the matching `.agent/skills/*/SKILL.md` before editing. All project Python commands use `uv run`, and user-visible i18n changes update all eight runtime locale files.

Project N.E.K.O. is licensed under the [Apache License 2.0](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE). Use [GitHub Issues](https://github.com/Project-N-E-K-O/N.E.K.O/issues) for reproducible bugs and scoped proposals.
