# Pages Router

Serves HTML pages for the Web UI. All pages are rendered with Jinja2 templates.

## Routes

| Path | Template | Description |
|------|----------|-------------|
| `/` | `index.html` | Main chat interface |
| `/model_manager` | `model_manager.html` | Live2D/VRM model management |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D parameter fine-tuning |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D emotion-animation mapping |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM emotion-animation mapping |
| `/chara_manager` | `chara_manager.html` | Character settings editor |
| `/voice_clone` | `voice_clone.html` | Voice cloning interface |
| `/api_key` | `api_key_settings.html` | API key configuration |
| `/steam_workshop_manager` | `steam_workshop_manager.html` | Steam Workshop management |
| `/memory_browser` | `memory_browser.html` | Memory browsing and editing |
| `/{lanlan_name}` | `index.html` | Character-specific chat (catch-all) |

::: info
The `/{lanlan_name}` catch-all route serves the same main interface but pre-selects a specific character.
:::
