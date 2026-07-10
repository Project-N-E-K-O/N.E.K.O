# Pages & Templates

## Template rendering

Pages are rendered with Jinja2 on the server side. Templates are located in the `templates/` directory.

## Page list

| Path | Template | Description |
|------|----------|-------------|
| `/` | `index.html` | Main chat interface with Live2D/VRM rendering |
| `/character_card_manager` | `character_card_manager.html` | Character personality and settings editor |
| `/api_key` | `api_key_settings.html` | API key configuration panel |
| `/model_manager` | `model_manager.html` | Model browsing and management |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D model parameter fine-tuning |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D emotion mapping |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM emotion mapping |
| `/mmd_emotion_manager` | `mmd_emotion_manager.html` | MMD emotion (morph) mapping |
| `/voice_clone` | `voice_clone.html` | Voice cloning interface |
| `/memory_browser` | `memory_browser.html` | Memory browsing and editing |

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
| `/user_mmd` | user data | User-imported MMD models (PMX/PMD) |
| `/user_mmd/animation` | user data | User-imported VMD animations |
| `/user_pngtuber` | user data | User-imported PNGTuber packages |
| `/workshop` | Steam Workshop | Workshop-subscribed models |
