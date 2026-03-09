# Config Manager

**File:** `utils/config_manager.py` (~1490 lines)

The `ConfigManager` is a singleton that centralizes all configuration loading, validation, and persistence.

## Access

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

## Key methods

### Character data

```python
config.get_character_data()      # All characters (returns master_name, her_name, stores, etc.)
config.load_characters()          # Reload from disk
config.save_characters()          # Persist to disk
```

### API configuration

```python
config.get_core_config()                     # API keys, provider, endpoints
config.get_model_api_config(model_type)      # Config for specific role (realtime/tts_custom/agent/etc.)
config.is_agent_api_ready()                  # Check if agent API is configured
```

### Voice management

```python
config.load_voice_storage()                  # Load all voice data
config.save_voice_storage()                  # Persist voice data
config.get_voices_for_current_api()          # Voices for current API provider
config.save_voice_for_current_api(voice)     # Save a voice for current provider
config.delete_voice_for_current_api(voice_id) # Delete a voice
```

### File system

```python
config.get_workshop_path()        # Steam Workshop directory
config.ensure_live2d_directory()  # Ensure Live2D model directory exists
config.ensure_vrm_directory()     # Ensure VRM model directory exists
```

### Migration

```python
config.migrate_config_files()     # Migrate config files from old paths
config.migrate_memory_files()     # Migrate memory files from old paths
```

## Directory structure

The config manager resolves and manages these directories:

| Property | Purpose |
|----------|---------|
| `docs_dir` | User's Documents directory |
| `app_docs_dir` | `~/Documents/N.E.K.O/` |
| `config_dir` | Configuration files |
| `memory_dir` | Memory store |
| `live2d_dir` | Live2D models |
| `vrm_dir` | VRM models |
| `vrm_animation_dir` | VRM animation files |
| `workshop_dir` | Steam Workshop content |
| `chara_dir` | Character data |
| `project_config_dir` | Project-level config |
| `project_memory_dir` | Project-level memory |

## Configuration resolution

The config manager implements the [priority chain](/config/config-priority):

1. Check environment variables (`NEKO_*`)
2. Check user config files (`core_config.json`)
3. Check API provider definitions (`api_providers.json`)
4. Fall back to code defaults (`config/__init__.py`)

## File discovery

The manager searches for the user documents directory:

- **Windows**: `SHGetFolderPathW` (CSIDL_PERSONAL) → fallback to registry → fallback to `USERPROFILE`. Also searches same-drive "文档"/"Documents"/"My Documents" candidates.
- **macOS/Linux**: `~/Documents/`

The `_get_project_root()` method detects the project root directory for accessing bundled resources.
