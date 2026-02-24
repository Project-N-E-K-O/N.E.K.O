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
config.save_character(name, data) # Persist changes
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

The manager searches for config files in this order:

1. User documents directory (`~/Documents/N.E.K.O/`)
2. Project `config/` directory
3. Creates defaults if nothing found

On Windows, the documents path is resolved via the Windows API (`SHGetFolderPath`). On macOS/Linux, it uses `~/Documents/`.
