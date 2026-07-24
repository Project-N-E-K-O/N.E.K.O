# Config Files

N.E.K.O. separates **writable user data** from **bundled repository defaults**.

## Writable data root

The storage-location policy selects the application data root. Defaults are:

- Windows: `%LOCALAPPDATA%\N.E.K.O`
- macOS: `~/Library/Application Support/N.E.K.O`
- Linux: `$XDG_DATA_HOME/N.E.K.O`, or `~/.local/share/N.E.K.O`

A user-selected location can replace the default. Inspect the storage-location UI/API when diagnosing another machine.

The selected root's `config/` directory can contain:

| File | Purpose |
| --- | --- |
| `core_config.json` | Provider selection, credentials, role overrides, feature settings |
| `characters.json` | Character definitions and reserved avatar data |
| `user_preferences.json` | User/UI preferences |
| `voice_storage.json` | Saved voice metadata |
| `workshop_config.json` | Workshop settings |

Features may add more runtime files. Prefer the Web UI because it writes the current schema.

## Bundled configuration

The repository `config/` directory is application data, not the normal user-data location.

- `config/api_providers.json` defines provider profiles and related catalogs.
- `config/characters/*.json` contains defaults for the eight supported locales.
- Python modules under `config/` provide validated defaults and constants.

On first use, `utils/config_manager/` migrates or copies supported defaults into the writable root. Subsequent writes go to the writable root.

## Character schema

Character identifiers and display names come from the active character data. Reserved multi-avatar settings live under `_reserved.avatar`; a legacy top-level `live2d` value may still appear for compatibility. The emergency code fallback and the locale JSON defaults are not identical, so never hardcode a translated character name as an identifier.

## Safe manual editing

1. Stop the application.
2. Back up the selected data root.
3. Preserve JSON types and reserved fields.
4. Restart and verify the owning UI.

Never commit personal `core_config.json`, character data, or credentials.
