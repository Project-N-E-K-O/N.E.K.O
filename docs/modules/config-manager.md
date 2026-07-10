# Config Manager

**File:** `utils/config_manager.py` (~1500 lines)

The `ConfigManager` is a singleton that centralizes all configuration loading, validation, and persistence.

## Access

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

## Key methods

### Character data

```python
config.get_character_data()      # All characters
config.load_characters()          # Reload from disk
config.save_characters(data)      # Persist the full character dict (sync, blocks the event loop)
config.asave_characters(data)     # Async version (use on async paths)
```

### API configuration

```python
config.get_core_config()              # API keys, provider, endpoints
config.get_model_api_config(model_type)  # Config for specific model role
```

### File system

```python
config.get_workshop_path()        # Steam Workshop directory
config.ensure_live2d_directory()  # Create Live2D model directory
config.ensure_vrm_directory()     # Create VRM model directory
```

## Configuration resolution

The config manager implements the [priority chain](/config/config-priority):

1. Check environment variables (`NEKO_*`)
2. Check user config files (`core_config.json`)
3. Check API provider definitions (`api_providers.json`)
4. Fall back to code defaults (`config/__init__.py`)

## File discovery

The manager searches for the runtime data root (which holds `config/`, `memory/`, etc.) in this order:

1. Platform standard app-data directory:
   - Windows: `%LOCALAPPDATA%\N.E.K.O\` (e.g. `C:\Users\<you>\AppData\Local\N.E.K.O\`)
   - macOS: `~/Library/Application Support/N.E.K.O/`
   - Linux: `$XDG_DATA_HOME/N.E.K.O/` (or `~/.local/share/N.E.K.O/` when unset)
2. Legacy locations (used only for one-time data import / last-resort fallback): the user documents directory (`~/Documents/N.E.K.O/`, resolved on Windows via the Windows API `SHGetFolderPath`), the executable directory (frozen builds), and the current working directory.
3. Creates defaults under the app-data directory if nothing usable is found.

Project `config/` files shipped with the source tree are still resolved relative to the repo root in source mode.
