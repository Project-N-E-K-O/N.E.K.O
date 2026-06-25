# Config Files

Configuration files are stored under an `N.E.K.O/` subdirectory inside the platform's standard application-data directory: `%LOCALAPPDATA%\N.E.K.O\` on Windows, `~/Library/Application Support/N.E.K.O/` on macOS, and `$XDG_DATA_HOME/N.E.K.O/` on Linux (falling back to `~/.local/share/N.E.K.O/` when unset).

## File locations

| File | Purpose |
|------|---------|
| `core_config.json` | API keys, provider selection, custom endpoints |
| `characters.json` | Character definitions and personality data |
| `user_preferences.json` | UI preferences, model choices |
| `voice_storage.json` | Custom voice configurations |
| `workshop_config.json` | Steam Workshop settings |
| `tutorial_prompt_config.json` | Tutorial/onboarding prompt thresholds and flow state |

## `core_config.json`

The primary runtime configuration file.

```json
{
  "coreApiKey": "",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "assistApiKeyQwen": "",
  "assistApiKeyOpenai": "",
  "assistApiKeyGlm": "",
  "assistApiKeyStep": "",
  "assistApiKeySilicon": "",
  "assistApiKeyGemini": "",
  "mcpToken": "",
  "agentModelUrl": "",
  "agentModelId": "",
  "agentModelApiKey": ""
}
```

## `characters.json`

Defines all characters and the master (owner) profile.

```json
{
  "主人": {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥"
  },
  "猫娘": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "T酱, 小T",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  },
  "当前猫娘": "小天"
}
```

The top-level keys are Chinese and the code depends on them verbatim: `主人` (the owner profile), `猫娘` (a map of characters keyed by name), and `当前猫娘` (the name of the currently active character). English keys like `master`/`catgirl` are not recognized and will be ignored.

Character fields are flexible — any key-value pair can be added and will be included in the character's context.

## File discovery

The `ConfigManager` class (`utils/config_manager.py`) handles file discovery:

1. Prefer the platform's standard application-data directory (Windows `%LOCALAPPDATA%`, macOS `~/Library/Application Support`, Linux `$XDG_DATA_HOME` or `~/.local/share`), creating/reading `N.E.K.O/` underneath it.
2. If the standard directory is unavailable, fall back to legacy locations (such as `~/Documents/N.E.K.O/`, the executable's own directory, or the current working directory) — these are used only for reading/importing older data.
3. Fall back to the project's bundled `config/` directory.
4. Create default files if none exist.

The legacy `~/Documents/N.E.K.O/` path (resolved on Windows via the Windows API `SHGetFolderPathW`) is now only a legacy-data import candidate, not the primary storage location.
