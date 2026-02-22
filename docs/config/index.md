# Configuration Overview

N.E.K.O. uses a layered configuration system with multiple sources. Configuration values are resolved in priority order from highest to lowest.

## Priority chain

1. **Environment variables** (highest) — `NEKO_*` prefix
2. **User config files** — `core_config.json`, `user_preferences.json`
3. **API provider config** — `api_providers.json`
4. **Code defaults** (lowest) — `config/__init__.py`

## Quick reference

| What to configure | Where |
|-------------------|-------|
| API keys and providers | [Environment Variables](./environment-vars) or Web UI at `/api_key` |
| Config file locations | [Config Files](./config-files) |
| Available AI providers | [API Providers](./api-providers) |
| Model selection per task | [Model Configuration](./model-config) |
| How overrides work | [Config Priority](./config-priority) |

## Web UI configuration

The easiest way to configure N.E.K.O. is through the Web UI:

- **API keys:** `http://localhost:48911/api_key`
- **Character settings:** `http://localhost:48911/chara_manager`
- **Model management:** `http://localhost:48911/model_manager`

Changes made through the Web UI are persisted to the appropriate config files automatically.
