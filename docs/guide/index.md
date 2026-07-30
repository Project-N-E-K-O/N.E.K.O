# Developer Guide

Project N.E.K.O. is an open-source AI companion platform with avatar rendering, realtime/text interaction, persistent memory, agent execution, and plugins. This site documents the current repository for contributors and integrators; it is not a product-pricing or provider-capability catalog.

## Repository surfaces

- Python 3.11 FastAPI/Uvicorn services under `app/`
- Conversation and persistent-memory domains under `main_logic/` and `memory/`
- Agent execution under `brain/` and the agent server
- Jinja/static pages plus one shared React chat implementation
- Vue plugin manager under `frontend/plugin-manager/`
- Electron desktop distribution built from N.E.K.O.-PC plus this packaged backend
- Container deployment under `docker/`

## Evaluate N.E.K.O. before setup

| Question | Buyer guide |
| --- | --- |
| Is the app free, and what can AI services cost? | [Cost and providers](./cost-and-providers) |
| Can it run completely offline? | [Local and offline boundaries](./local-and-offline) |
| Where can conversations and memory be sent? | [Technical data flow and privacy controls](./data-and-privacy) |
| Which installation channel should I choose? | [Steam, GitHub Releases, or source](./install-options) |

## Start here

| Goal | Page |
| --- | --- |
| Check tools | [Prerequisites](./prerequisites) |
| Prepare a checkout | [Development Setup](./dev-setup) |
| Run N.E.K.O. | [Quick Start](./quick-start) |
| Navigate code | [Project Structure](./project-structure) |
| Understand services | [Architecture](/architecture/) |
| Build a plugin | [Plugin Quick Start](/plugins/quick-start) |
| Deploy | [Deployment](/deployment/) |

All Python examples use `uv run`. If a page conflicts with the same-revision entrypoint, loader, or workflow, current code is the source of truth; please report the documentation drift.
