# Config Priority

N.E.K.O. resolves configuration values through a layered priority system. Higher-priority sources override lower ones.

## Priority order

```
┌─────────────────────────────────┐  Highest priority
│  1. Environment Variables       │  NEKO_* prefix
│     (set in shell or .env)      │
├─────────────────────────────────┤
│  2. User Config Files           │  core_config.json
│     (~/Documents/N.E.K.O/)      │  user_preferences.json
├─────────────────────────────────┤
│  3. API Provider Config         │  config/api_providers.json
│     (project directory)         │
├─────────────────────────────────┤
│  4. Code Defaults               │  config/__init__.py
│     (hardcoded fallbacks)       │
└─────────────────────────────────┘  Lowest priority
```

## Example resolution

For the summary model:

1. Check `NEKO_SUMMARY_MODEL` environment variable
2. Check `core_config.json` for a custom summary model URL/name
3. Check the selected assist provider's `summary_model` in `api_providers.json`
4. Fall back to `DEFAULT_SUMMARY_MODEL = "qwen-plus"` in `config/__init__.py`

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
