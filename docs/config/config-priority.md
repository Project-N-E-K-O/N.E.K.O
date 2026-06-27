# Config Priority

N.E.K.O. resolves configuration values through a layered priority system. Higher-priority sources override lower ones.

## Priority order

```
┌─────────────────────────────────┐  Highest priority
│  1. Environment Variables       │  NEKO_* prefix
│     (set in shell or .env)      │
├─────────────────────────────────┤
│  2. User Config Files           │  core_config.json
│     (platform app-data/N.E.K.O/)│  user_preferences.json
├─────────────────────────────────┤
│  3. API Provider Config         │  config/api_providers.json
│     (project directory)         │
├─────────────────────────────────┤
│  4. Code Defaults               │  config/__init__.py
│     (hardcoded fallbacks)       │
└─────────────────────────────────┘  Lowest priority
```

The user config files live in the platform app-data directory: `%LOCALAPPDATA%/N.E.K.O/` on Windows, `~/Library/Application Support/N.E.K.O/` on macOS, and `~/.local/share/N.E.K.O/` on Linux. `~/Documents/N.E.K.O/` is now only a legacy fallback.

## Example resolution

For the summary model:

1. Check `core_config.json` for a custom summary model URL/name
2. Check the selected assist provider's `summary_model` in `api_providers.json`
3. Fall back to `DEFAULT_SUMMARY_MODEL = "qwen-plus"` in `config/__init__.py`

## When to use each layer

| Layer | Best for |
|-------|----------|
| Environment variables | Docker deployment, CI/CD, secrets management |
| User config files | Web UI configuration (auto-managed) |
| API provider config | Default model assignments per provider |
| Code defaults | Fallback values when nothing else is configured |

## Docker-specific notes

In Docker deployments, environment variables are the primary configuration mechanism. The `entrypoint.sh` script automatically generates `core_config.json` from `NEKO_*` environment variables at startup.

See [Docker Deployment](/deployment/docker) for details.
