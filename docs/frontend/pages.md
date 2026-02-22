# Pages & Templates

## Template rendering

Pages are rendered with Jinja2 on the server side. Templates are located in the `templates/` directory.

## Page list

| Path | Template | Description |
|------|----------|-------------|
| `/` | `index.html` | Main chat interface with Live2D/VRM rendering |
| `/chara_manager` | `chara_manager.html` | Character personality and settings editor |
| `/api_key` | `api_key_settings.html` | API key configuration panel |
| `/model_manager` | `model_manager.html` | Model browsing and management |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D model parameter fine-tuning |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D emotion mapping |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM emotion mapping |
| `/voice_clone` | `voice_clone.html` | Voice cloning interface |
| `/memory_browser` | `memory_browser.html` | Memory browsing and editing |
| `/steam_workshop_manager` | `steam_workshop_manager.html` | Steam Workshop management |

## Dark mode

Dark mode is managed by `static/theme-manager.js`:

- Toggle via UI button
- Persisted in `localStorage`
- CSS variables defined in `static/css/dark-mode.css`
- Respects system preference (`prefers-color-scheme`)

## Static file serving

| Mount point | Directory | Content |
|-------------|-----------|---------|
| `/static` | `static/` | JS, CSS, images, locales |
| `/user_live2d` | User documents | User-imported Live2D models |
| `/user_vrm` | User documents | User-imported VRM models |
| `/workshop` | Steam Workshop | Workshop-subscribed models |
