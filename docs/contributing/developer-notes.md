# Developer Notes

Critical rules and gotchas that every N.E.K.O. contributor must know. These are distilled from hard-won project experience.

## Core rules

::: danger MUST follow
These rules are enforced across the entire codebase.
:::

### 1. Always use `uv` to run anything

All Python commands must go through `uv`:

```bash
# ✅ Correct
uv run python main_server.py
uv run pytest tests/

# ❌ Wrong
python main_server.py
pytest tests/
```

### 2. i18n is mandatory for all user-facing text

The project supports 5 languages (`en`, `zh-CN`, `zh-TW`, `ja`, `ko`). All user-visible strings must go through the i18n system.

- **HTML**: Use `data-i18n` attributes
- **JS**: Use `window.t('key')` with Chinese fallback
- Locale files live in `static/locales/`

See [Internationalization](/frontend/i18n) for the full guide.

### 3. Privacy-sensitive logs: `print()` only

Any log that could contain **raw user conversation data** must use `print()`, never `logger`. This ensures sensitive data stays out of persistent log files.

```python
# ✅ User conversation data
print(f"User said: {user_message}")

# ✅ System events use logger
logger.info("Session started for character: %s", lanlan_name)

# ❌ Never log user conversations with logger
logger.info(f"User said: {user_message}")  # BAD!
```

### 4. Preserve system prompt watermark when translating

When translating system prompts (for any reason), always preserve the marker `======以上为`. This is an internal watermark used for prompt boundary detection.

### 5. Steam achievements are irreversible

Once a Steam achievement is unlocked, it **cannot be revoked** via code. Always test achievement logic thoroughly with console commands before deploying:

```javascript
// Test in browser console
await window.unlockAchievement('ACH_NAME');
window.getAchievementStats();
```

## Frontend gotchas

### i18n kills HTML icons

When i18next updates element text via `textContent`, it destroys any `<img>` or `<span>` tags inside the element. If your translation string contains HTML, the i18n system detects this and uses `innerHTML` instead. If you're adding icons to translatable elements, include the HTML in the locale JSON:

```json
{
  "button.save": "<img src='icon.svg'> Save"
}
```

### `overflow: hidden` breaks `<select>` dropdowns

The capsule UI system uses large border-radius, which often leads developers to add `overflow: hidden` to containers. This clips native `<select>` dropdowns. Fix:

```css
/* Any container with a <select> inside */
.field-row-with-select {
  overflow: visible !important;
}
```

### Button interaction formula

All buttons must follow this interaction pattern for consistent feel:

```css
.button:hover {
  transform: translateY(-1px);
  /* enhanced shadow */
}
.button:active {
  transform: translateY(1px) scale(0.98);
}
```

### Vanilla JS race conditions (DOM lazy loading)

Since N.E.K.O. uses vanilla JavaScript without a reactive framework, DOM elements may not exist when your code runs — especially popups and HUD components that are created lazily on first click.

::: warning Never use fixed `setTimeout` for DOM binding
A hardcoded `setTimeout(..., 100)` will miss elements that haven't been created yet. Use self-terminating recursive polling instead:
:::

```javascript
const bindEvents = () => {
    const getEl = (ids) => {
        for (let id of ids) {
            const el = document.getElementById(id);
            if (el) return el;
        }
        return null;
    };

    const targetEl = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);

    if (!targetEl) {
        setTimeout(bindEvents, 500); // Retry until DOM exists
        return;
    }

    // Found — bind and stop polling
    targetEl.addEventListener('change', myLogic);
    myLogic(); // Trigger first check
};

setTimeout(bindEvents, 100); // Start polling
```

**Optimistic UI conflicts**: When a toggle button is clicked, the UI optimistically flips to "on" while a backend request is in flight. If another component (e.g., a polling loop) reads the DOM during this window, it may see stale state. Guard against this by checking whether the element is in a loading/disabled state before trusting its value.

### UI design system: Capsule UI + Neko Blue

The project has a strict visual system:

| Token | Value | Usage |
|-------|-------|-------|
| `--color-n-main` | `#40C5F1` | Brand blue: titles, primary buttons, active states |
| `--color-n-deep` | `#22b3ff` | Stroke/deep blue: text outlines, focus glow |
| `--color-n-light` | `#e3f4ff` | Light background blue |
| `--color-n-border` | `#b3e5fc` | Border blue: capsule borders, dividers |
| `--radius-capsule` | `50px` | All interactive elements |
| `--radius-card` | `20px` | Cards and containers |

Fonts:
- **Latin**: `'Comic Neue'`, `'Segoe UI'`, `Arial`
- **CJK**: `'Source Han Sans CN'`, `'Noto Sans SC'`
- **Monospace** (API keys, IDs): `'Courier New', monospace`

See the full design system in `.agent/skills/ui-system-refactor/references/design-system.md`.

## Backend gotchas

### Gemini API response format

Gemini may wrap JSON responses in markdown code blocks:

````
```json
{"emotion": "happy"}
```
````

Always strip markdown wrapping before parsing:

```python
if result_text.startswith("```"):
    lines = result_text.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    result_text = "\n".join(lines).strip()
```

### Gemini `extra_body` requires double nesting

When controlling Gemini's thinking mode via OpenAI-compatible API, the `extra_body` must be double-nested:

```python
# ✅ Correct: double nesting
extra_body = {
    "extra_body": {
        "google": {
            "thinking_config": {
                "thinking_budget": 0  # Disable thinking for 2.5
            }
        }
    }
}

# ❌ Wrong: single nesting (causes "Unknown name 'google'" error)
extra_body = {
    "google": {
        "thinking_config": {"thinking_budget": 0}
    }
}
```

### Thinking mode varies by provider

Each LLM provider has a different format for disabling extended reasoning:

| Provider | Format |
|----------|--------|
| Qwen, Step, DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

The `config/__init__.py` module handles this mapping automatically — check `MODELS_EXTRA_BODY_MAP`.

## VRM model gotchas

### SpringBone physics explosion

VRM physics uses `vrm.update(delta)` where `delta` must be in **seconds**, not milliseconds. If hair/clothing flies upward on load:

```javascript
let delta = clock.getDelta();
delta = Math.min(delta, 0.05); // Clamp to prevent explosion on tab switch
vrm.update(delta);
```

### Oversized colliders (affects ~100% of VRM models)

VRM models exported from VRoid Studio/UniVRM have a known bug where collider radii are ~2x too large ([UniVRM #673](https://github.com/vrm-c/UniVRM/issues/673)). This causes hair to appear stuck horizontally.

**Fix**: Reduce all collider radii by 50% after loading:

```javascript
const COLLIDER_REDUCTION = 0.5;
springBoneManager.colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        collider._originalRadius = collider.shape.radius;
        collider.shape.radius *= COLLIDER_REDUCTION;
    }
});
```

### MToon outline thickness

When VRM models are scaled, MToon outlines become disproportionately thick. Switch to screen-space outlines:

```javascript
material.outlineWidthMode = 'screenCoordinates';
material.outlineWidthFactor = 0.005; // Thin, consistent outline
material.needsUpdate = true;
```

### 3D camera: pixel-to-world mapping

When implementing drag/zoom for VRM models, **never use a fixed pan speed**. Calculate the pixel-to-world mapping dynamically based on camera distance:

```javascript
const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
const pixelToWorld = worldHeight / screenHeight;
// Mouse delta * pixelToWorld = world space movement
```

## Testing

### Test structure

```
tests/
├── unit/          # OmniOffline/Realtime clients, provider connectivity
├── frontend/      # Playwright tests for each Web UI page
├── e2e/           # Full user journey (8 stages, needs --run-e2e flag)
└── utils/         # LLM-based response quality evaluator
```

### Running tests

```bash
# All tests (excluding e2e)
uv run pytest tests/ -s

# Unit tests only
uv run pytest tests/unit -s

# Frontend tests (requires Playwright browsers)
uv run playwright install
uv run pytest tests/frontend -s

# E2E tests (requires explicit flag)
uv run pytest tests/e2e --run-e2e -s
```

### API keys for tests

Copy `tests/api_keys.json.template` to `tests/api_keys.json` and fill in your keys. This file is gitignored.

## Issue templates

When filing bugs or requesting features, use the GitHub issue templates:

- **Bug report**: Include reproduction steps, expected vs actual behavior, and environment info
- **Feature request**: Describe the feature, its use case, and any relevant context
