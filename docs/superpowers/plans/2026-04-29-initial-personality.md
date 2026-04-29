# Initial Personality Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a TDD-first initial personality flow that appears after storage selection and tutorial, stores per-character personality overrides, lets settings reopen or clear the override, and makes the selected personality affect runtime behavior without rewriting locale files or mixing raw profile fields with hidden prompt blocks.

**Architecture:** Add two small backend helpers: one for fixed personality presets and one for first-run onboarding state. Teach `ConfigManager.get_character_data()` to expose an effective character profile by overlaying the selected preset onto the runtime view only, then add narrow `/api/characters/*` endpoints for preset listing, onboarding state, and per-character override CRUD. On the frontend, load a dedicated onboarding module after tutorial completion, plus a small control surface inside the character panel for reselection and clearing.

**Tech Stack:** FastAPI routers, `utils/config_manager.py`, small JSON state helpers, Playwright frontend tests, pytest unit tests, existing i18next locale files with minimal synchronized key additions.

---

## File Map

- Create: `utils/persona_presets.py`
- Create: `utils/initial_personality_state.py`
- Create: `tests/unit/test_persona_presets.py`
- Create: `tests/unit/test_initial_personality_state.py`
- Create: `tests/unit/test_character_persona_router.py`
- Create: `tests/frontend/test_initial_personality_onboarding.py`
- Create: `static/js/character_personality_onboarding.js`
- Create: `static/css/character_personality_onboarding.css`
- Modify: `config/__init__.py`
- Modify: `utils/config_manager.py`
- Modify: `main_routers/characters_router.py`
- Modify: `static/js/character_card_manager.js`
- Modify: `templates/index.html`
- Modify: `static/locales/en.json`
- Modify: `static/locales/zh-CN.json`
- Modify: `static/locales/es.json`
- Modify: `static/locales/pt.json`
- Modify: other locale files in the exact same nearby section and line range if new keys require it

### Task 1: Presets And Onboarding State

**Files:**
- Create: `utils/persona_presets.py`
- Create: `utils/initial_personality_state.py`
- Test: `tests/unit/test_persona_presets.py`
- Test: `tests/unit/test_initial_personality_state.py`

- [ ] **Step 1: Write the failing preset tests**

```python
from utils.persona_presets import (
    PERSONA_OVERRIDE_FIELDS,
    get_persona_preset,
    list_persona_presets,
)


def test_list_persona_presets_returns_three_fixed_presets():
    presets = list_persona_presets()

    assert [preset["preset_id"] for preset in presets] == [
        "classic_genki",
        "tsundere_helper",
        "elegant_butler",
    ]
    assert presets[0]["profile"]["性格原型"] == "经典元气猫娘"


def test_get_persona_preset_returns_copy():
    preset = get_persona_preset("classic_genki")
    preset["profile"]["性格"] = "changed"

    fresh = get_persona_preset("classic_genki")
    assert fresh["profile"]["性格"] != "changed"
    assert set(PERSONA_OVERRIDE_FIELDS) >= {"性格原型", "性格", "口癖", "爱好", "雷点", "隐藏设定", "一句话台词"}
```

```python
from utils.initial_personality_state import (
    get_initial_personality_state_path,
    load_initial_personality_state,
    mark_initial_personality_state,
)


class DummyConfig:
    def __init__(self, root):
        self.local_state_dir = root / "state"
        self.local_state_dir.mkdir(parents=True, exist_ok=True)


def test_initial_personality_state_defaults_to_pending(tmp_path):
    config = DummyConfig(tmp_path)

    state = load_initial_personality_state(config)

    assert state["status"] == "pending"
    assert get_initial_personality_state_path(config).name == "initial_personality_prompt.json"


def test_initial_personality_state_persists_completed_and_skipped(tmp_path):
    config = DummyConfig(tmp_path)

    completed = mark_initial_personality_state("completed", config_manager=config, now_iso="2026-04-29T12:00:00Z")
    skipped = mark_initial_personality_state("skipped", config_manager=config, now_iso="2026-04-29T12:05:00Z")

    assert completed["status"] == "completed"
    assert skipped["status"] == "skipped"
    assert load_initial_personality_state(config)["handled_at"] == "2026-04-29T12:05:00Z"
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/unit/test_persona_presets.py tests/unit/test_initial_personality_state.py -q
```

Expected:
- `ModuleNotFoundError` or import failure for the new helper modules

- [ ] **Step 3: Write minimal preset/state implementation**

```python
# utils/persona_presets.py
from __future__ import annotations

from copy import deepcopy

PERSONA_OVERRIDE_FIELDS = (
    "性格原型",
    "性格",
    "口癖",
    "爱好",
    "雷点",
    "隐藏设定",
    "一句话台词",
)

_PRESETS = (
    {
        "preset_id": "classic_genki",
        "profile": {
            "性格原型": "经典元气猫娘",
            "性格": "...",
            "口癖": "...",
            "爱好": "...",
            "雷点": "...",
            "隐藏设定": "...",
            "一句话台词": "...",
        },
        "summary_key": "memory.characterSelection.classic_genki.desc",
        "preview_line": "太棒了喵！今天也让我陪着你吧。",
        "prompt_guidance": "Speak like an energetic, affectionate cat companion who prioritizes emotional warmth, quick encouragement, and playful softness.",
    },
)


def list_persona_presets():
    return deepcopy(list(_PRESETS))


def get_persona_preset(preset_id: str):
    for preset in _PRESETS:
        if preset["preset_id"] == str(preset_id or "").strip():
            return deepcopy(preset)
    return None
```

```python
# utils/initial_personality_state.py
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from utils.file_utils import atomic_write_json

DEFAULT_INITIAL_PERSONALITY_STATE = {
    "version": 1,
    "status": "pending",
    "handled_at": "",
}


def get_initial_personality_state_path(config_manager) -> Path:
    return Path(config_manager.local_state_dir) / "initial_personality_prompt.json"


def load_initial_personality_state(config_manager) -> dict:
    path = get_initial_personality_state_path(config_manager)
    if not path.exists():
        return deepcopy(DEFAULT_INITIAL_PERSONALITY_STATE)
    import json
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    state = deepcopy(DEFAULT_INITIAL_PERSONALITY_STATE)
    state.update(raw if isinstance(raw, dict) else {})
    return state


def mark_initial_personality_state(status: str, *, config_manager, now_iso: str | None = None) -> dict:
    state = load_initial_personality_state(config_manager)
    state["status"] = status
    state["handled_at"] = now_iso or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    atomic_write_json(get_initial_personality_state_path(config_manager), state, ensure_ascii=False, indent=2)
    return state
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/unit/test_persona_presets.py tests/unit/test_initial_personality_state.py -q
```

Expected:
- both test files pass

- [ ] **Step 5: Commit**

```bash
git add utils/persona_presets.py utils/initial_personality_state.py tests/unit/test_persona_presets.py tests/unit/test_initial_personality_state.py
git commit -m "feat: add personality preset and onboarding state helpers"
```

### Task 2: Backend Effective Personality And APIs

**Files:**
- Modify: `config/__init__.py`
- Modify: `utils/config_manager.py`
- Modify: `main_routers/characters_router.py`
- Test: `tests/unit/test_character_persona_router.py`
- Test: `tests/unit/test_character_memory_regression.py` (reuse existing fixture style if a regression case belongs there)

- [ ] **Step 1: Write the failing backend tests**

```python
import importlib
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.shared_state import init_shared_state
from utils.config_manager import ConfigManager


def _make_config_manager(tmp_root: Path):
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), \
         patch.object(ConfigManager, "_get_standard_data_directory_candidates", return_value=[tmp_root]), \
         patch.object(ConfigManager, "get_legacy_app_root_candidates", return_value=[]), \
         patch.object(ConfigManager, "_get_project_root", return_value=tmp_root):
        return ConfigManager("N.E.K.O")


@pytest.mark.unit
def test_get_character_data_uses_persona_override_in_runtime_view():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        characters = cm.load_characters()
        current_name = characters["当前猫娘"]
        characters["猫娘"][current_name].setdefault("_reserved", {})["persona_override"] = {
            "preset_id": "classic_genki",
            "profile": {
                "性格原型": "经典元气猫娘",
                "性格": "永远元气满格的小太阳",
                "口癖": "太棒了喵！",
                "爱好": "陪伴、温暖",
                "雷点": "冷漠敷衍",
                "隐藏设定": "情感价值优先",
                "一句话台词": "今天也让我陪着你吧。",
            },
            "prompt_guidance": "Speak like an energetic, affectionate cat companion.",
        }
        cm.save_characters(characters)

        _, _, _, character_data, _, prompt_map, _, _, _ = cm.get_character_data()

        assert character_data[current_name]["性格原型"] == "经典元气猫娘"
        assert character_data[current_name]["一句话台词"] == "今天也让我陪着你吧。"
        assert "energetic, affectionate cat companion" in prompt_map[current_name]


@pytest.mark.unit
def test_persona_selection_routes_save_and_clear_override(tmp_path, monkeypatch):
    config = _make_config_manager(tmp_path)
    router_module = importlib.import_module("main_routers.characters_router")
    monkeypatch.setattr(router_module, "get_config_manager", lambda: config)

    async def _noop(*args, **kwargs):
        return None

    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=config,
        logger=None,
        initialize_character_data=_noop,
        switch_current_catgirl_fast=_noop,
        init_one_catgirl=_noop,
        remove_one_catgirl=_noop,
    )

    app = FastAPI()
    app.include_router(router_module.router)
    client = TestClient(app)

    current_name = config.load_characters()["当前猫娘"]
    response = client.put(
        f"/api/characters/catgirl/{current_name}/persona-selection",
        json={"preset_id": "classic_genki", "source": "onboarding"},
    )
    assert response.status_code == 200
    assert response.json()["success"] is True

    characters = config.load_characters()
    override = characters["猫娘"][current_name]["_reserved"]["persona_override"]
    assert override["preset_id"] == "classic_genki"

    clear_response = client.delete(f"/api/characters/catgirl/{current_name}/persona-selection")
    assert clear_response.status_code == 200
    assert clear_response.json()["success"] is True
    assert "persona_override" not in config.load_characters()["猫娘"][current_name].get("_reserved", {})
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
pytest tests/unit/test_character_persona_router.py -q
```

Expected:
- failure because runtime merge helpers and routes do not exist yet

- [ ] **Step 3: Write minimal backend implementation**

```python
# config/__init__.py
RESERVED_FIELD_SCHEMA = {
    "voice_id": str,
    "system_prompt": str,
    "persona_override": {
        "preset_id": str,
        "selected_at": str,
        "source": str,
        "prompt_guidance": str,
        "profile": dict,
    },
    "character_origin": {
        "source": str,
        "source_id": str,
        "display_name": str,
        "model_ref": str,
    },
    ...
}
```

```python
# utils/config_manager.py
from utils.persona_presets import PERSONA_OVERRIDE_FIELDS


def _get_persona_override_profile(character_payload: dict) -> dict:
    reserved = character_payload.get("_reserved")
    if not isinstance(reserved, dict):
        return {}
    override = reserved.get("persona_override")
    if not isinstance(override, dict):
        return {}
    profile = override.get("profile")
    return dict(profile) if isinstance(profile, dict) else {}


def _merge_effective_character_profile(character_payload: dict) -> dict:
    result = deepcopy(character_payload)
    override_profile = _get_persona_override_profile(character_payload)
    for field in PERSONA_OVERRIDE_FIELDS:
        value = str(override_profile.get(field) or "").strip()
        if value:
            result[field] = value
    return result


def _append_persona_guidance(base_prompt: str, character_payload: dict) -> str:
    reserved = character_payload.get("_reserved")
    override = reserved.get("persona_override") if isinstance(reserved, dict) else None
    guidance = str(override.get("prompt_guidance") or "").strip() if isinstance(override, dict) else ""
    if not guidance:
        return base_prompt
    return f"{base_prompt}\\n\\nAdditional role guidance: {guidance}"
```

```python
# main_routers/characters_router.py
@router.get("/persona-presets")
async def list_persona_presets_route():
    return _json_no_store_response({"success": True, "presets": list_persona_presets()})


@router.get("/persona-onboarding-state")
async def get_persona_onboarding_state():
    config = get_config_manager()
    return _json_no_store_response({"success": True, "state": load_initial_personality_state(config)})


@router.post("/persona-onboarding-state")
async def set_persona_onboarding_state(request: Request):
    payload = await request.json()
    config = get_config_manager()
    state = mark_initial_personality_state(str(payload.get("status") or "").strip(), config_manager=config)
    return {"success": True, "state": state}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/unit/test_character_persona_router.py -q
```

Expected:
- route tests and runtime merge test pass

- [ ] **Step 5: Commit**

```bash
git add config/__init__.py utils/config_manager.py main_routers/characters_router.py tests/unit/test_character_persona_router.py
git commit -m "feat: add per-character personality override APIs"
```

### Task 3: Frontend Onboarding Flow

**Files:**
- Create: `static/js/character_personality_onboarding.js`
- Create: `static/css/character_personality_onboarding.css`
- Modify: `templates/index.html`
- Test: `tests/frontend/test_initial_personality_onboarding.py`

- [ ] **Step 1: Write the failing frontend onboarding tests**

```python
import pytest


@pytest.mark.frontend
def test_initial_personality_waits_for_tutorial_then_opens(mock_page):
    mock_page.route("**/persona-onboarding-harness", lambda route: route.fulfill(
        status=200,
        content_type="text/html",
        body="<!doctype html><html><body><div id='character-personality-overlay' hidden></div></body></html>",
    ))
    mock_page.goto("http://neko.test/persona-onboarding-harness")
    mock_page.add_script_tag(path="static/js/character_personality_onboarding.js")
    mock_page.evaluate(
        """
        () => {
            window.waitForStorageLocationStartupBarrier = async () => {};
            window.universalTutorialManager = { isTutorialRunning: true };
            window.fetch = async (url) => {
                if (url === '/api/characters/persona-onboarding-state') {
                    return new Response(JSON.stringify({ success: true, state: { status: 'pending' } }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                if (url === '/api/characters/current_catgirl') {
                    return new Response(JSON.stringify({ current_catgirl: '小天' }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                if (url === '/api/characters/persona-presets') {
                    return new Response(JSON.stringify({ success: true, presets: [] }), { status: 200, headers: { 'Content-Type': 'application/json' } });
                }
                throw new Error('unexpected ' + url);
            };
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    assert mock_page.locator("#character-personality-overlay").is_hidden()
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; window.dispatchEvent(new CustomEvent('neko:tutorial-completed', { detail: { page: 'home' } })); }")
    mock_page.wait_for_function("() => !document.getElementById('character-personality-overlay').hidden")
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/frontend/test_initial_personality_onboarding.py -q
```

Expected:
- failure because onboarding script is missing

- [ ] **Step 3: Write minimal onboarding implementation**

```javascript
// static/js/character_personality_onboarding.js
(function () {
    'use strict';

    async function fetchJson(url, options) {
        const response = await fetch(url, options);
        return response.json();
    }

    class CharacterPersonalityOnboarding {
        async bootstrap() {
            await this.waitForStartupBarrier();
            await this.waitForTutorial();
            const state = await fetchJson('/api/characters/persona-onboarding-state');
            if (!state || !state.state || state.state.status !== 'pending') return;
            this.showOverlay();
        }

        async waitForStartupBarrier() {
            if (typeof window.waitForStorageLocationStartupBarrier === 'function') {
                await window.waitForStorageLocationStartupBarrier();
            }
        }

        async waitForTutorial() {
            if (!window.universalTutorialManager || !window.universalTutorialManager.isTutorialRunning) return;
            await new Promise((resolve) => {
                window.addEventListener('neko:tutorial-completed', () => resolve(), { once: true });
            });
        }

        showOverlay() {
            const overlay = document.getElementById('character-personality-overlay');
            if (overlay) overlay.hidden = false;
        }
    }

    window.CharacterPersonalityOnboarding = new CharacterPersonalityOnboarding();
})();
```

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/frontend/test_initial_personality_onboarding.py -q
```

Expected:
- onboarding harness test passes

- [ ] **Step 5: Commit**

```bash
git add static/js/character_personality_onboarding.js static/css/character_personality_onboarding.css templates/index.html tests/frontend/test_initial_personality_onboarding.py
git commit -m "feat: add initial personality onboarding flow"
```

### Task 4: Settings Entry, Locale Keys, And Regression Pass

**Files:**
- Modify: `static/js/character_card_manager.js`
- Modify: `static/locales/en.json`
- Modify: `static/locales/zh-CN.json`
- Modify: all same-group locale files in matching nearby line ranges
- Test: `tests/frontend/test_character_card_manager_regressions.py`
- Test: `tests/frontend/test_initial_personality_onboarding.py`

- [ ] **Step 1: Write the failing settings-entry test**

```python
@pytest.mark.frontend
def test_character_panel_exposes_personality_actions(page: Page, running_server: str):
    page.goto(f"{running_server}/character_card_manager")
    page.wait_for_load_state("networkidle")
    page.click(".chara-card-item")
    page.wait_for_selector(".catgirl-panel-wrapper")

    expect(page.locator("[data-testid='character-personality-select']")).to_be_visible()
    expect(page.locator("[data-testid='character-personality-clear']")).to_be_visible()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/frontend/test_initial_personality_onboarding.py tests/frontend/test_character_card_manager_regressions.py -q
```

Expected:
- missing personality controls and missing locale keys

- [ ] **Step 3: Write minimal UI wiring and locale additions**

```javascript
// static/js/character_card_manager.js
const personalityWrapper = document.createElement('div');
personalityWrapper.className = 'field-row-wrapper';
personalityWrapper.dataset.testid = 'character-personality-actions';

const selectButton = document.createElement('button');
selectButton.type = 'button';
selectButton.dataset.testid = 'character-personality-select';
selectButton.textContent = window.t ? window.t('character.personality.select') : '选择人格';

const clearButton = document.createElement('button');
clearButton.type = 'button';
clearButton.dataset.testid = 'character-personality-clear';
clearButton.textContent = window.t ? window.t('character.personality.clear') : '恢复默认人格';
```

```json
// static/locales/zh-CN.json
"personality": {
  "select": "选择人格",
  "clear": "恢复默认人格",
  "usingPreset": "当前：{{name}}（覆盖）",
  "usingDefault": "当前：角色卡默认人格"
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
pytest tests/unit/test_persona_presets.py tests/unit/test_initial_personality_state.py tests/unit/test_character_persona_router.py tests/frontend/test_initial_personality_onboarding.py tests/frontend/test_character_card_manager_regressions.py -q
```

Expected:
- all personality-related tests pass

- [ ] **Step 5: Commit**

```bash
git add static/js/character_card_manager.js static/locales/en.json static/locales/zh-CN.json static/locales/es.json static/locales/pt.json tests/frontend/test_initial_personality_onboarding.py tests/frontend/test_character_card_manager_regressions.py
git commit -m "feat: add personality selection entry to character settings"
```

## Self-Review

### Spec coverage

- First-run order: covered in Task 3 onboarding gate
- Two-step selection: covered by Task 3 onboarding UI
- Per-character override + restore default: covered in Task 2 backend + Task 4 settings entry
- Runtime effect: covered in Task 2 effective profile merge in `get_character_data()`
- Minimal locale diffs: covered in Task 4 localized key discipline

### Placeholder scan

- No `TODO` / `TBD`
- Every task names concrete files and concrete commands
- Tests are specified before implementation in every task

### Type consistency

- Backend helper name: `persona_override`
- Frontend module name: `CharacterPersonalityOnboarding`
- State filename: `initial_personality_prompt.json`
