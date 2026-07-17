# Prerequisites

## Required for source development

| Tool | Requirement | Source |
| --- | --- | --- |
| Python | exactly 3.11.x | `pyproject.toml` |
| uv | a current compatible release | `uv.lock` and workflows |
| Git | a maintained release | repository operations |
| Node.js + npm | `^20.19.0 || >=22.12.0` for plugin manager | `frontend/plugin-manager/package.json` |

Install Python dependencies with `uv sync`; do not use a hand-maintained `pip install -r requirements.txt` flow. Frontend and docs builds use committed lockfiles and `npm ci`.

## Feature-specific tools

- Docker Engine + Compose for containers
- Steam client/SDK environment for Steam integration tests
- Platform build dependencies for Nuitka/Electron packaging
- Playwright Chromium for browser/frontend tests
- Provider credentials only for runtime paths or tests that call those providers

A free route may exist in current provider data, but availability and limits are external state, not a development prerequisite or documentation promise.

Use a case-preserving filesystem, enable long paths on Windows, and keep the checkout writable. Model preparation, frontend builds, tests, and packaging create local artifacts.
