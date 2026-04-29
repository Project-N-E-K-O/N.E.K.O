# Character Personality Warning Copy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bilingual-safe warning copy to the personality modal for settings and manual reselect flows, refresh modal text into a light catgirl tone, and keep all user-visible strings on i18n keys.

**Architecture:** Extend the existing `CharacterPersonalityOnboardingManager` with a small “warning copy” branch keyed off `openReason`, render the warning block in both stages, and keep styling isolated in the onboarding stylesheet. Cover the behavior with focused Playwright frontend tests before touching production code, then patch locale files with minimal additions only around the existing `memory.characterSelection` section.

**Tech Stack:** Vanilla browser JavaScript, project locale JSON files, Playwright frontend tests, pytest

---

### Task 1: Lock the new warning behavior with frontend tests

**Files:**
- Modify: `tests/frontend/test_initial_personality_onboarding.py`
- Test: `tests/frontend/test_initial_personality_onboarding.py`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.frontend
def test_onboarding_hides_context_warning_for_first_time_flow(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / 'static' / 'js' / 'character_personality_onboarding.js'))
    mock_page.evaluate("() => { window.CharacterPersonalityOnboarding.bootstrap(); }")

    expect(mock_page.locator("[data-testid='character-personality-warning']")).to_have_count(0)


@pytest.mark.frontend
def test_manual_reselect_shows_context_warning_in_both_steps(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        '''
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__personaOnboardingState.manual_reselect_character_name = '小天';
            window.__personaOnboardingState.manual_reselect_requested_at = '2026-04-29T12:10:00Z';
        }
        '''
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / 'static' / 'js' / 'character_personality_onboarding.js'))
    mock_page.evaluate("() => { window.CharacterPersonalityOnboarding.bootstrap(); }")

    expect(mock_page.locator("[data-testid='character-personality-warning']")).to_have_count(1)
    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator("[data-testid='character-personality-warning']")).to_have_count(1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/frontend/test_initial_personality_onboarding.py -k "context_warning or i18n_copy" -v`
Expected: FAIL because the warning block does not exist yet and the new copy keys are not rendered.

- [ ] **Step 3: Adjust the i18n copy test to assert the refreshed warning text**

```python
expect(mock_page.locator("[data-role='hint']")).to_have_text(
    "No worries, if you change your mind later, we can retune my vibe from settings."
)
expect(mock_page.locator("[data-testid='character-personality-warning']")).to_have_text(
    "Heads up: switching my personality clears this character's recent chat context."
)
```

- [ ] **Step 4: Run the same tests again to confirm the failures are the expected ones**

Run: `uv run pytest tests/frontend/test_initial_personality_onboarding.py -k "context_warning or i18n_copy" -v`
Expected: FAIL on missing/old copy, not on harness errors.

### Task 2: Render warning blocks and refreshed copy in the modal

**Files:**
- Modify: `static/js/character_personality_onboarding.js`
- Modify: `static/css/character_personality_onboarding.css`

- [ ] **Step 1: Add a small warning-copy helper in JavaScript**

```javascript
shouldShowContextWarning() {
    return this.openReason === 'settings' || this.openReason === 'manual_reselect';
}

getContextWarningText() {
    return translate(
        'memory.characterSelection.contextWarning',
        '小提醒喵，切换人格后，我会先忘掉当前角色最近这段聊天上下文，再用新的语气继续陪你。'
    );
}
```

- [ ] **Step 2: Render the warning block in stage one and stage two only when needed**

```javascript
if (this.shouldShowContextWarning()) {
    const warning = createElement(
        'div',
        'character-personality-warning',
        this.getContextWarningText()
    );
    warning.dataset.testid = 'character-personality-warning';
    stageOne.appendChild(warning);
}
```

```javascript
if (this.shouldShowContextWarning()) {
    const warning = createElement(
        'div',
        'character-personality-warning character-personality-warning-stage-two',
        this.getContextWarningText()
    );
    warning.dataset.testid = 'character-personality-warning';
    stageTwo.appendChild(warning);
}
```

- [ ] **Step 3: Refresh the existing visible fallback strings into the new tone**

```javascript
return translate('memory.characterSelection.settingsTitle', '换一种软乎乎陪着你的方式吧喵');
return translate(
    'memory.characterSelection.settingsHint',
    '这里不会弄坏角色卡原本的设定喵，只会暂时覆盖当前角色现在生效的人格。'
);
return translate(
    'memory.characterSelection.stageOneIntro',
    '先挑一个最对味的气质喵，再听听我开口时是不是你想要的感觉。'
);
```

- [ ] **Step 4: Add matching warning styles without disturbing the rest of the modal**

```css
.character-personality-warning {
    margin: 18px 0 24px;
    padding: 14px 16px;
    border: 2px solid var(--jelly-blue-200);
    border-radius: 18px;
    background: linear-gradient(180deg, rgba(255, 255, 255, 0.96), rgba(224, 242, 254, 0.95));
    color: var(--text-main);
    font-size: 14px;
    line-height: 1.7;
}
```

- [ ] **Step 5: Run the targeted frontend tests to verify the code goes green**

Run: `uv run pytest tests/frontend/test_initial_personality_onboarding.py -k "context_warning or i18n_copy" -v`
Expected: PASS

### Task 3: Patch locale keys with minimal diffs and run a final regression slice

**Files:**
- Modify: `static/locales/zh-CN.json`
- Modify: `static/locales/zh-TW.json`
- Modify: `static/locales/en.json`
- Modify: `static/locales/ja.json`
- Modify: `static/locales/ko.json`
- Modify: `static/locales/es.json`
- Modify: `static/locales/pt.json`
- Modify: `static/locales/ru.json`
- Test: `tests/frontend/test_initial_personality_onboarding.py`

- [ ] **Step 1: Add only the missing `memory.characterSelection` keys near the existing block**

```json
"contextWarning": "...",
"manualTitle": "...",
"manualHint": "...",
"settingsTitle": "...",
"settingsHint": "...",
"chooseHint": "...",
"confirmGreeting": "..."
```

- [ ] **Step 2: Keep each locale semantically aligned**

```text
zh-CN / zh-TW: light catgirl tone
en / es / pt / ru / ja / ko: same warning meaning, no weaker wording
```

- [ ] **Step 3: Run the broader onboarding regression slice**

Run: `uv run pytest tests/frontend/test_initial_personality_onboarding.py -v`
Expected: PASS or SKIP only for Playwright-binary gating

- [ ] **Step 4: Inspect the final diff**

Run: `git diff -- tests/frontend/test_initial_personality_onboarding.py static/js/character_personality_onboarding.js static/css/character_personality_onboarding.css static/locales/en.json static/locales/es.json static/locales/ja.json static/locales/ko.json static/locales/pt.json static/locales/ru.json static/locales/zh-CN.json static/locales/zh-TW.json`
Expected: only focused copy/warning additions, no locale reordering.
