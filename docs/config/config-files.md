# Config Files

Configuration files are stored in the user's documents directory under `N.E.K.O/`.

## File locations

| File | Purpose |
|------|---------|
| `core_config.json` | API keys, provider selection, custom endpoints |
| `characters.json` | Character definitions and personality data |
| `user_preferences.json` | UI preferences, model choices |
| `voice_storage.json` | Custom voice configurations |
| `workshop_config.json` | Steam Workshop settings |

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
  "master": {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥"
  },
  "catgirl": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "T酱, 小T",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  }
}
```

Character fields are flexible — any key-value pair can be added and will be included in the character's context.

## File discovery

The `ConfigManager` class (`utils/config_manager.py`) handles file discovery:

1. Check the user's documents directory (`~/Documents/N.E.K.O/`)
2. Fall back to the project's `config/` directory
3. Create default files if none exist

On Windows, the documents directory is resolved via the Windows API. On macOS/Linux, it uses `~/Documents/`.
