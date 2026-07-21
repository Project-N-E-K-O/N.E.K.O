# Deployment Overview

Choose a path according to the audience:

| Path | Intended use | Contract |
| --- | --- | --- |
| Desktop release | End users | Electron UI plus packaged Python backend |
| Source launcher | Contributors/local development | `uv run python launcher.py` |
| Docker Compose | Headless/server deployment | Nginx in front of the Python services |
| Standalone modules | Service isolation | Start memory, main, and agent separately |

The cross-platform desktop workflow builds Windows, macOS, and Linux artifacts. Scheduled output is a **nightly prerelease**, not a stable-release promise.

## Requirements

Source development requires Python 3.11 exactly, `uv`, and Node compatible with the lockfiles (the plugin manager requires `^20.19.0 || >=22.12.0`). Docker requires a current Docker Engine with Compose.

Local vector recall is optional and CPU-only. See [Local Embedding Model Assets](./embedding-models) for gates and asset layout; BM25 remains available when vectors are disabled.

## Default source ports

| Port | Process |
| ---: | --- |
| 48911 | Main Web/API server |
| 48912 | Memory server |
| 48915 | Agent/tool server |
| 48916 | User-plugin host |

Additional ports are listed in [Environment Variables](/config/environment-vars). Source entrypoints bind to loopback. Docker publishes host 48911/48912 to Nginx HTTP/HTTPS; those mappings are not the source-process port table.

## Exposure warning

N.E.K.O. is primarily a local companion. Before exposing it beyond a trusted host, review authentication, proxy headers, TLS, firewall rules, and the privacy impact of configuration, memory, browser, screen, and plugin capabilities.
