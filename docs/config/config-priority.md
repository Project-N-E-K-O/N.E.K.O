# Config Priority

There is no project-wide “environment > user file > provider file > code” rule. Resolve precedence for the setting being changed.

## User and model settings

`utils/config_manager/` builds runtime settings from code defaults, the selected provider profile, and writable `core_config.json`. Provider choice, credentials, saved endpoint results, and supported per-role overrides are applied by that loader.

Provider profiles come from `config/api_providers.json`. If it is absent or malformed, core/assist loaders use code fallbacks. Assist profiles merge JSON fields over same-key code defaults and retain code-only fallback profiles.

## Ports

For each port, `config/network.py` checks:

1. `NEKO_<PORT_NAME>`
2. bare `<PORT_NAME>` for compatibility
3. Electron's `port_config.json`
4. the code default

The launcher may choose fallback ports when preferred ports are occupied and exports the selected values to child services.

## Other runtime variables

String, list, and boolean helpers in `config/network.py` use the same prefixed-then-bare lookup only for names wired in code. Memory-vector switches are parsed independently by `config/memory_settings.py`.

## Docker initialization

`docker/entrypoint.sh` can generate `/app/config/core_config.json` from API-related `NEKO_*` values when that file is absent or `NEKO_FORCE_ENV_UPDATE` is set. This is initialization, not a universal live environment overlay. Persisted user data can already contain newer writable configuration; confirm effective values in the Web UI after startup.
