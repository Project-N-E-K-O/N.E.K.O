# Check Modules

Module to file mappings for i18n-check.

## Module Definitions

| Module | HTML Files | JS Files |
|--------|------------|----------|
| `main` | index.html | app.js, main.js |
| `live2d` | live2d*.html | live2d*.js, pixi*.js |
| `voice` | voice*.html | voice*.js, tts*.js, asr*.js |
| `steam` | steam*.html | steam*.js |
| `settings` | settings*.html, config*.html | settings*.js, config*.js |
| `chat` | chat*.html, memory_browser.html, chara_manager.html | chat*.js, memory_browser.js, chara_manager.js |
| `common` | viewer.html | common_dialogs.js, common_ui.js, i18n-i18next.js, universal-tutorial-manager.js |

## Custom Module

Use `--files` to specify custom files:

```bash
/i18n-check custom --files=templates/test.html,static/test.js
```

## Directories

- **Templates**: `templates/`
- **Static JS**: `static/`, `static/js/`
- **Exclude**: `static/libs/`, `static/locales/`
