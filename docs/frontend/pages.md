# Pages and templates

## Rendering model

`main_routers/pages_router.py` renders Jinja2 templates from `templates/`. Shared template context includes asset versions; pages that can initialize VRM also receive the backend-owned lighting defaults.

Use root-relative asset URLs (`/static/...`). Electron windows and nested routes do not share the same URL depth, and relative asset paths can resolve differently.

## Public page routes

| Route | Template | Purpose |
| --- | --- | --- |
| `/`, `/{lanlan_name}` | `index.html` | Main UI and avatar renderer |
| `/model_manager`, legacy `/l2d` | `model_manager.html` | Live2D, VRM, MMD, and PNGTuber model management |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D parameter editing |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D motion/expression mapping |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM expression mapping |
| `/mmd_emotion_manager` | `mmd_emotion_manager.html` | MMD morph mapping |
| `/character_card_manager`, legacy `/chara_manager` | `character_card_manager.html` | Character cards and character settings |
| `/api_key` | `api_key_settings.html` | Provider and API-key settings |
| `/voice_clone` | `voice_clone.html` | Voice-cloning workflow |
| `/cloudsave_manager` | `cloudsave_manager.html` | Cloud-save management |
| `/memory_browser` | `memory_browser.html` | Recent conversation memory review and processing settings |
| `/cookies_login` | `cookies_login.html` | Cookie-based login flow |
| `/chat` | `chat.html` | Compact standalone React chat surface |
| `/chat_full` | `chat.html` | Full standalone React chat surface |
| `/web_chat_compact` | `index.html` | Main-page template forced into compact chat mode |
| `/subtitle` | `subtitle.html` | Standalone subtitle window |
| `/agenthud` | `agenthud.html` | Agent task HUD |
| `/card_maker` | `card_maker.html` | Character-card creation |
| `/jukebox`, `/jukebox/manager` | `jukebox.html`, `jukebox_manager.html` | Jukebox and its manager |
| `/toast` | `toast.html` | Standalone toast surface |
| `/soccer_demo`, `/badminton_demo` | matching demo templates | Mini-game development pages |

`/chara_manager` redirects to `/character_card_manager`. `/l2d` is only a compatibility route; it is not a separate Live2D implementation.

The memory browser edits the recent conversation file exposed by the main server. It does not directly edit facts, reflections, persona data, archive shards, or retrieval indexes owned by the memory system.

## Chat and subtitle windows

`index.html` and `chat.html` both mount the same React chat bundle into `#react-chat-window-root`. Their remaining hidden DOM nodes exist for compatibility with shared voice, session, and screenshot scripts; they are not a second chat UI.

Electron uses preload-provided APIs for native window behavior. `chat.html` checks `window.nekoChatWindow`; `subtitle-window.js` uses `window.nekoSubtitle` when present and otherwise remains usable as a web page. Inter-page state uses `BroadcastChannel('neko_page_channel')` and same-origin `postMessage` fallbacks. The Electron main process and preload implementation live outside this repository.

## Theme

`static/theme-manager.js` initializes the theme before most page content to avoid a flash. Theme styles are in `static/css/dark-mode.css`; pages that need dark mode must load both resources and use the shared data attributes/CSS variables.

## Static mounts

| URL prefix | Content |
| --- | --- |
| `/static` | Versioned application JS, CSS, images, bundled libraries, and locale JSON |
| `/user_live2d` | Active user Live2D directory |
| `/user_live2d_local` | Writable local shadow when the active Live2D directory differs |
| `/user_vrm`, `/user_vrm/animation` | User VRM models and VRMA animations |
| `/user_mmd`, `/user_mmd/animation` | User MMD models and VMD animations |
| `/user_pngtuber` | Normalized PNGTuber packages |
| `/user_mods` | Configured local mod directory |
| `/workshop` | Steam Workshop content, mounted during startup when available |

Mounts are conditional on their backing directories. API responses already return public URLs; clients must not reconstruct them from local paths.
