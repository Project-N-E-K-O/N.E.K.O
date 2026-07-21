# Config Manager

**Package:** `utils/config_manager/`

`ConfigManager` is the process-wide facade for runtime storage and persisted configuration. It is assembled from mixins for storage roots, migrations, characters, core API configuration, voice storage, quota, and Workshop integration. The package facade preserves the stable `utils.config_manager` import path.

## Access and migration

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

`get_config_manager()` caches one instance per process. On the first normal access it runs config, card-face, and memory migrations. Import of legacy Documents memory is best-effort and does not block startup if it fails.

Startup phase 0 can use `get_config_manager(migrate=False)` to construct the manager without running migrations. `reset_config_manager_cache()` exists for tests and controlled reinitialization; application code should not create competing manager instances.

## Runtime roots and file resolution

The selected runtime root owns directories such as `config/`, `memory/`, `plugins/`, `live2d/`, `vrm/`, and `character_cards/`. It is resolved from the storage policy, with `NEKO_STORAGE_SELECTED_ROOT` and `NEKO_STORAGE_ANCHOR_ROOT` available to the startup/storage workflow. These variables are not a universal per-setting override system.

If a previously selected root is unavailable, the manager enters a recovery state at the anchor root instead of silently treating that location as the new committed choice. Cloud-save write fences can reject ordinary writes while storage state is unsafe. Local-state failures carry a structured `LocalStateDirectoryError`.

For JSON configuration:

- `get_config_path(name)` reads the runtime `config/` copy first, then a project `config/` default;
- `get_runtime_config_path(name)` always points to the writable runtime copy;
- `load_json_config()` returns a deep copy of the supplied default when the file is missing or unreadable;
- `save_json_config()` writes atomically to the runtime copy and enforces the cloud-save write fence.

Provider profiles and code defaults are resolved by the relevant core/voice methods. There is no single global “environment → user file → provider file → default” chain that applies to every setting.

## Main contracts

### Character data

```python
characters = config.load_characters()
config.save_characters(characters)          # synchronous filesystem write
await config.asave_characters(characters)   # offloaded async wrapper

current = config.get_character_data()
current_async = await config.aget_character_data()
```

`get_character_data()` does not return the raw character dictionary. It resolves the current master and catgirl, effective persona payloads and prompts, name mapping, and per-character memory paths, then returns them as a nine-item tuple. Use `load_characters()` when the raw persisted mapping is required.

The character cache is guarded by locks and checked against the file modification time. A damaged current-character selection may be corrected to the first valid character and written back.

### API and voice configuration

```python
core = config.get_core_config()
core_async = await config.aget_core_config()
conversation = config.get_model_api_config("conversation")
agent_ready = config.is_agent_api_ready()

voices = config.load_voice_storage()
config.save_voice_storage(voices)
```

These methods normalize the stored configuration and resolve role/provider-specific settings. Voice helpers also validate preset and cloned voice identifiers against the currently selected provider.

### Directories and integrations

The manager exposes path and creation helpers for models, plugins, memory, cloud-save state, and Workshop data. `get_plugins_directory()` is also exported as a package-level convenience function.

## Threading and error behavior

Filesystem methods such as `load_json_config()`, `save_json_config()`, `load_characters()`, and `save_characters()` are synchronous. Do not call them directly on a latency-sensitive event loop. Character and core reads provide async wrappers implemented with `asyncio.to_thread()`.

Missing files can use caller-provided defaults. Invalid JSON, atomic-write failures, write-fence violations, and unusable local-state directories remain visible to the caller; they are not silently converted into successful writes.
