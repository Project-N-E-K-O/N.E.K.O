from __future__ import annotations

import json
import gzip
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
STATIC_DIR = PLUGIN_DIR / "static"
SURFACES_DIR = PLUGIN_DIR / "surfaces"
LOCALES = ["zh-CN", "zh-TW", "en", "es", "ja", "ko", "pt", "ru"]
REQUIRED_STATIC_UI_KEYS = [
    "ui.eyebrow",
    "ui.label.study_companion_workspace",
    "ui.label.study_controls",
    "ui.label.study_state",
    "ui.label.study_hub",
    "ui.label.duration",
    "ui.label.goal",
    "ui.onboarding.label",
    "ui.onboarding.title",
    "ui.button.skip",
    "ui.diagnosis.default.label",
    "ui.diagnosis.default.title",
    "ui.diagnosis.default.body",
    "ui.label.quick_panels",
    "ui.quick.focus",
    "ui.quick.due",
    "ui.quick.checkin",
    "ui.quick.focus_default",
    "ui.feature.nav_label",
    "ui.feature.memory.title",
    "ui.feature.memory.body",
    "ui.feature.review.title",
    "ui.feature.review.body",
    "ui.feature.knowledge.title",
    "ui.feature.knowledge.body",
    "ui.feature.pomodoro.title",
    "ui.feature.pomodoro.body",
    "ui.feature.checkin.title",
    "ui.feature.checkin.body",
    "ui.feature.export.title",
    "ui.feature.export.body",
    "ui.surface_drawer.label",
    "ui.surface_drawer.title",
    "ui.button.close",
    "ui.status.pending",
    "ui.label.study_workspace",
    "ui.label.explain_input",
    "ui.label.practice_flow",
    "ui.practice.title",
    "ui.practice.context_label",
    "ui.practice.context_loading",
    "ui.practice.context_loading_body",
    "ui.practice.empty_question",
    "ui.label.answer_panel",
    "ui.practice.feedback_title",
    "ui.label.reply_panel",
    "ui.button.advanced_settings",
    "ui.label.advanced_settings",
    "ui.settings.tab.study",
    "ui.settings.tab.knowledge",
    "ui.settings.tab.memory",
    "ui.settings.tab.habit",
    "ui.settings.tab.data",
    "ui.settings.ocr.title",
    "ui.settings.ocr.summary",
    "ui.settings.default_mode.label",
    "ui.settings.ocr_enabled.label",
    "ui.settings.ocr_languages.label",
    "ui.settings.llm.title",
    "ui.settings.llm.summary",
    "ui.settings.llm_timeout.label",
    "ui.settings.dependencies.title",
    "ui.settings.dependencies.summary",
    "ui.button.save_settings",
    "ui.settings.knowledge.summary",
    "ui.button.open_knowledge_map",
    "ui.button.contribution_settings",
    "ui.settings.memory.summary",
    "ui.button.open_decks",
    "ui.button.import_memory",
    "ui.button.due_reviews",
    "ui.settings.checkin.title",
    "ui.settings.checkin.summary",
    "ui.button.open_habit_dashboard",
    "ui.settings.pomodoro.title",
    "ui.settings.pomodoro.summary",
    "ui.button.open_pomodoro",
    "ui.settings.supervision.title",
    "ui.settings.supervision.summary",
    "ui.button.edit_daily_goal",
    "ui.settings.data.summary",
    "ui.button.session_summary",
    "ui.button.export_notes",
]
REQUIRED_DYNAMIC_UI_KEYS = [
    "ui.settings.ocr.ready_summary",
    "ui.settings.ocr.no_status",
    "ui.settings.dependencies.ready_summary",
    "ui.settings.dependencies.no_status",
    "ui.settings.knowledge.loaded_summary",
    "ui.settings.knowledge.empty_summary",
    "ui.settings.memory.loaded_summary",
    "ui.status.checkin_done",
    "ui.status.checkin_pending",
    "ui.status.config_loading",
    "ui.status.config_loaded",
    "ui.status.config_saving",
    "ui.status.config_saved",
    "ui.status.config_load_failed",
    "ui.status.config_save_failed",
    "ui.knowledge.zoom_controls",
    "ui.knowledge.zoom_status",
    "ui.knowledge.zoom_out",
    "ui.knowledge.zoom_reset",
    "ui.knowledge.zoom_in",
]


def _css_variables(source: str) -> dict[str, str]:
    return {
        match.group("name"): re.sub(r"\s+", " ", match.group("value").strip())
        for match in re.finditer(
            r"--(?P<name>[a-zA-Z0-9_-]+)\s*:\s*(?P<value>[^;]+);",
            source,
        )
    }


def _html_i18n_keys(source: str) -> set[str]:
    return set(re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"', source))


def _hex_rgb(value: str) -> tuple[float, float, float]:
    match = re.fullmatch(r"#([0-9a-fA-F]{6})", value.strip())
    assert match is not None, value
    raw = match.group(1)
    return tuple(int(raw[index : index + 2], 16) / 255 for index in (0, 2, 4))


def _relative_luminance(rgb: tuple[float, float, float]) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = (channel(value) for value in rgb)
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _contrast_ratio(foreground: str, background: str) -> float:
    first = _relative_luminance(_hex_rgb(foreground))
    second = _relative_luminance(_hex_rgb(background))
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def _diagnosis_background_sample(
    style_css: str,
    severity: str,
) -> tuple[float, float, float]:
    pattern = (
        rf'\.primary-diagnosis\[data-severity="{re.escape(severity)}"\]\s*'
        r'\{[^}]*background:\s*linear-gradient\(180deg,\s*'
        r'rgba\((?P<r>\d+),\s*(?P<g>\d+),\s*(?P<b>\d+),\s*(?P<a>[0-9.]+)\)'
    )
    match = re.search(pattern, style_css, flags=re.DOTALL)
    assert match is not None, severity
    alpha = float(match.group("a"))
    return tuple(
        ((int(match.group(channel)) / 255) * alpha) + (1 - alpha)
        for channel in ("r", "g", "b")
    )


def _simulate_protanopia(rgb: tuple[float, float, float]) -> tuple[float, float, float]:
    red, green, blue = rgb
    return (
        (0.56667 * red) + (0.43333 * green),
        (0.55833 * red) + (0.44167 * green),
        (0.24167 * green) + (0.75833 * blue),
    )


def _has_playwright_chromium() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False

    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        # Playwright exposes install/runtime failures through provider-specific exceptions.
        return False


def test_study_companion_quick_cards_follow_adaptive_practice() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    practice_panel_start = index_html.index('id="practicePanel"')
    practice_panel_end = index_html.index("</details>", practice_panel_start)
    memory_panel_start = index_html.index('id="memoryPanel"')
    memory_tag_start = index_html.rfind("<details", practice_panel_end, memory_panel_start)
    explain_panel_start = index_html.index('id="explainPanel"')

    assert practice_panel_end < memory_panel_start < explain_panel_start
    assert not index_html[practice_panel_end + len("</details>") : memory_tag_start].strip()


def test_study_input_area_label_is_localized() -> None:
    expected = {
        "zh-CN": "输入区",
        "zh-TW": "輸入區",
        "en": "Input area",
        "es": "Área de entrada",
        "ja": "入力エリア",
        "ko": "입력 영역",
        "pt": "Área de entrada",
        "ru": "Область ввода",
    }

    for locale, label in expected.items():
        bundle = json.loads((PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        assert bundle["ui.label.text"] == label


def test_study_reply_is_combined_with_the_input_module() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    input_start = index_html.index('id="explainPanel"')
    input_end = index_html.index("\n            </section>\n          </div>", input_start)
    input_module = index_html[input_start:input_end]

    assert input_module.index('id="studyInput"') < input_module.index('id="replyPanel"')
    assert '<section id="replyPanel" class="reply-panel"' in input_module
    assert index_html.count('id="replyPanel"') == 1
    assert index_html.count('id="replyText"') == 1


def test_memory_card_first_save_prompts_for_deck_and_supports_skip() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    controller_js = (STATIC_DIR / "quick-card-controller.js").read_text(encoding="utf-8")
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")

    assert 'id="memoryItemTypeSelect"' in index_html
    for item_type in ("custom", "word", "cloze", "sentence", "paragraph"):
        assert f'<option value="{item_type}" data-i18n="ui.memory.item_type.{item_type}">' in index_html
    assert 'id="memoryDeckSelect"' in index_html
    assert 'id="memoryCreateDeckBtn"' in index_html
    assert "#memoryDeckSelect {" in style_css
    assert "#memoryDeckSelect:hover:not(:disabled)" in style_css
    assert "#memoryDeckSelect:focus-visible" in style_css
    assert "#memoryDeckSelect:disabled" in style_css
    assert 'id="memoryDeckDialog"' in index_html
    assert 'id="memoryDeckNameInput"' in index_html
    assert 'id="memoryDeckTypeSelect"' in index_html
    for deck_type in ("word", "passage", "formula", "custom"):
        assert f'<option value="{deck_type}" data-i18n="ui.memory.deck_type.{deck_type}">' in index_html
    assert 'id="memoryDeckCreateBtn"' in index_html
    assert 'id="memoryDeckSkipBtn"' in index_html
    load_call = (
        "memoryDecks = await window.StudyCompanionSurfacePanels."
        "loadDecks({ callPlugin }, memoryAddBtn);"
    )
    assert load_call in main_js
    assert "async function loadDeckOptions()" in main_js
    state_start = main_js.index("function setMemoryDeckState(")
    state_end = main_js.index("\nfunction memoryDeckDisplayName", state_start)
    assert "void loadDeckOptions().catch" in main_js[state_start:state_end]
    assert "async function chooseDeckForFirstCard()" in main_js
    choose_start = main_js.index("async function chooseDeckForFirstCard()")
    choose_end = main_js.index("\nfunction setStatusLine", choose_start)
    choose_source = main_js[choose_start:choose_end]
    assert "await loadDeckOptions();" in choose_source
    assert "callPlugin('study_memory_create_deck'" in main_js
    assert "deck_type: deckType" in main_js
    assert "deck_id: deckId" in main_js
    assert "item_type: quickCardController?.getItemType()" in main_js
    assert "word: 'word'" in controller_js
    assert "passage: 'paragraph'" in controller_js
    assert "formula: 'custom'" in controller_js
    assert "custom: 'custom'" in controller_js
    assert "itemTypeOverridden" in controller_js
    assert "applyDeckDefault(selectedDeck()?.deck_type);" in controller_js
    assert "applyDeckDefault(selectedDeck()?.deck_type, true);" not in controller_js
    assert "option.textContent = label" in controller_js
    assert "event.stopImmediatePropagation()" in controller_js
    assert '<script src="./quick-card-controller.js?v=study-quick-card-types-20260812"></script>' in index_html
    assert "memory-deck-dialog" in style_css


def test_quick_card_controller_preserves_manual_item_type_override() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")
    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip("frontend/plugin-manager node_modules with happy-dom is not installed")

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const staticDir = process.env.STUDY_COMPANION_STATIC_DIR;
const html = fs.readFileSync(path.join(staticDir, 'index.html'), 'utf8');
const source = fs.readFileSync(path.join(staticDir, 'quick-card-controller.js'), 'utf8');
const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
document.write(html);
document.close();
window.eval(source);

const decks = [
  { id: 'word-deck', name: 'Vocabulary', deck_type: 'word' },
  { id: 'passage-deck', name: 'Reading', deck_type: 'passage' },
];
const deckSelect = document.getElementById('memoryDeckSelect');
for (const deck of decks) {
  const option = document.createElement('option');
  option.value = deck.id;
  option.textContent = deck.name;
  deckSelect.appendChild(option);
}
const itemTypeSelect = document.getElementById('memoryItemTypeSelect');
const controller = window.StudyQuickCardController.create({
  t: (_key, fallback) => fallback,
  getDecks: () => decks,
});
controller.decorateDeckOptions();
if (deckSelect.options[0].textContent !== 'Vocabulary / Word') {
  throw new Error(`deck label was not decorated: ${deckSelect.options[0].textContent}`);
}
deckSelect.value = 'word-deck';
deckSelect.dispatchEvent(new window.Event('change'));
if (itemTypeSelect.value !== 'word') throw new Error('word deck default was not applied');

itemTypeSelect.value = 'cloze';
itemTypeSelect.dispatchEvent(new window.Event('change'));
deckSelect.value = 'passage-deck';
deckSelect.dispatchEvent(new window.Event('change'));
if (itemTypeSelect.value !== 'cloze') throw new Error('manual item type override was lost');
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env={**os.environ, "STUDY_COMPANION_STATIC_DIR": str(STATIC_DIR)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_memory_deck_management_can_expand_concrete_cards() -> None:
    fallback = (STATIC_DIR / "surface-panels.js").read_text(encoding="utf-8")
    hosted = (SURFACES_DIR / "memory_deck_list.tsx").read_text(encoding="utf-8")

    for source in (fallback, hosted):
        assert "study_memory_list_deck_items" in source
        assert "ui.button.view_cards" in source
        assert "ui.button.hide_cards" in source
        assert "ui.memory.empty_deck" in source

    required_keys = {
        "ui.memory.default_deck_name",
        "ui.memory.deck_summary",
        "ui.memory.deck_count",
        "ui.memory.card_count",
        "ui.memory.choose_deck",
        "ui.memory.first_deck_title",
        "ui.memory.first_deck_body",
        "ui.memory.deck_name_placeholder",
        "ui.memory.empty_deck",
        "ui.button.view_cards",
        "ui.button.hide_cards",
        "ui.button.create_and_save",
        "ui.button.skip_use_default_deck",
    }
    bundles = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((PLUGIN_DIR / "i18n").glob("*.json"))
    ]
    assert len(bundles) == 8
    for bundle in bundles:
        assert required_keys <= bundle.keys()


def test_study_companion_static_ui8_visual_accessibility_and_csp_contract() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    knowledge_map_js = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")

    csp_match = re.search(
        r'http-equiv="Content-Security-Policy"\s+content="([^"]+)"',
        index_html,
    )
    assert csp_match is not None
    csp = csp_match.group(1)
    directives = {
        directive.strip().split()[0]: directive.strip().split()[1:]
        for directive in csp.split(";")
        if directive.strip()
    }
    assert directives["script-src"] == ["'self'"]
    assert directives["style-src"] == ["'self'"]
    assert directives["style-src-attr"] == ["'unsafe-inline'"]
    assert "connect-src 'self'" in csp
    assert ":*" not in csp
    assert "frame-ancestors" not in csp
    assert "meta CSP cannot express dynamic localhost ports" in index_html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1" />' in index_html
    assert 'style="' not in index_html
    assert "<style" not in index_html

    assert "@media (min-width: 1360px)" in style_css
    assert "responsive" not in index_html.lower()
    assert "responsive" not in style_css.lower()
    assert "responsive" not in main_js.lower()
    assert "mobile" not in index_html.lower()
    assert "mobile" not in style_css.lower()
    assert "mobile" not in main_js.lower()
    assert "mode-strip" not in index_html
    assert "addEventListener('resize'" not in main_js
    assert 'addEventListener("resize"' not in main_js
    assert "visibilitychange" not in main_js
    assert "modeSwitch.offsetParent === null" in main_js
    assert "getBoundingClientRect()" in main_js
    assert "modeSwitch.style.setProperty('--indicator-left'" in main_js
    assert "modeSwitch.style.setProperty('--indicator-width'" in main_js
    assert ".style.removeProperty" not in main_js
    assert "reviewCompleted: 'neko-study-review-completed'" in main_js
    assert "refreshSummary: 'neko-study-refresh-summary'" in main_js
    assert "requestStudyStatusRefresh()" in main_js
    assert "let refreshPending = false;" in main_js
    assert ".finally(() => {" in main_js
    assert "SECURITY: renderMathInText MUST HTML-escape all non-math text." in main_js
    assert "window.location.origin" in main_js
    assert "const modeSelect = $id('modeSelect');" in main_js
    assert "function handleModeShortcut(event)" in main_js
    assert "modeSelect.addEventListener('change'" in main_js
    assert "document.addEventListener('keydown', handleModeShortcut);" in main_js

    assert 'class="hero"' in index_html
    assert 'class="study-hub"' in index_html
    assert 'id="firstRunGuide"' in index_html
    assert 'id="primaryDiagnosis"' in index_html
    assert 'id="modeSelect"' in index_html
    assert '<select id="modeSelect" class="sr-only"' in index_html
    assert 'aria-keyshortcuts="Alt+1"' in index_html
    assert 'aria-keyshortcuts="Alt+2"' in index_html
    assert 'aria-keyshortcuts="Alt+3"' in index_html
    assert 'role="tablist"' in index_html
    assert 'role="tabpanel"' in index_html
    assert 'aria-live="polite"' in index_html
    assert 'aria-expanded="false"' in index_html
    assert ".sr-only" in style_css
    assert re.search(
        r"button:focus-visible,\s*textarea:focus-visible,\s*input:focus-visible,\s*select:focus-visible,\s*a:focus-visible",
        style_css,
    )
    assert ".mode-btn:hover" in style_css
    assert "button:hover" in style_css
    assert "button:active" in style_css
    assert "transform: scale(0.97)" in style_css
    assert "@supports not (backdrop-filter: blur(16px))" in style_css
    assert "@media (prefers-reduced-motion: reduce)" in style_css
    assert "transition-duration: 1ms !important" in style_css
    assert "function prefersReducedMotion()" in main_js
    assert "matchMedia('(prefers-reduced-motion: reduce)')" in main_js
    assert "if (prefersReducedMotion())" in main_js
    assert "role', 'dialog'" in main_js
    assert "aria-modal', 'true'" in main_js
    assert "learning-profile-modal" in style_css
    assert "knowledge-stage-selector" in style_css
    assert "knowledgeMapActiveStage" in knowledge_map_js
    assert '<link rel="stylesheet" href="./style.css?v=study-knowledge-window-size-20260813" />' in index_html
    assert '<script src="./knowledge-map.js?v=study-knowledge-window-size-20260813"></script>' in index_html
    outcome_formatters_script = (
        '<script src="./outcome-formatters.js?v=study-hotfix-20260812"></script>'
    )
    main_script = '<script src="./main.js?v=study-ocr-fallback-notice-20260820"></script>'
    assert outcome_formatters_script in index_html
    assert "solution-narration.js" not in index_html
    assert index_html.index(outcome_formatters_script) < index_html.index(main_script)
    assert '<span class="hero-paw" aria-hidden="true">🐾</span>' in index_html
    assert '<span class="hero-title__cat" aria-hidden="true">🐱</span>' in index_html
    assert '<span data-i18n="ui.title">Study Companion</span>' in index_html
    assert ".hero-paw" in style_css
    assert ".hero-title__cat" in style_css
    assert "@keyframes pawBounce" not in style_css
    assert "🐾" in index_html
    assert "🐱" in index_html
    assert '.memory-card[data-empty="true"]::before' in style_css
    assert "memoryDueCard.dataset.empty = 'true';" in main_js
    assert "delete memoryDueCard.dataset.empty;" in main_js
    assert "(=^・ω・^=)" in style_css

    assert len(index_html.splitlines()) <= 1000
    assert len(style_css.splitlines()) <= 2500
    assert len(main_js.encode("utf-8")) <= 95000
    assert len(gzip.compress(main_js.encode("utf-8"))) <= 22000


def test_static_scope_read_failure_clears_the_stale_question_context() -> None:
    source = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")
    load_start = source.index("async function loadPracticeScope")
    load_end = source.index("async function activateKnowledgePracticeScope", load_start)
    load_scope = source[load_start:load_end]

    catch_start = load_scope.index("catch (error)")
    catch_body = load_scope[catch_start:]
    assert "setPracticeScopeState({ active: false" in catch_body
    assert "setQuestionContext({ selection_reason: 'no_data', no_data: true })" in catch_body


def test_static_memory_review_uses_exact_item_id_for_custom_decks() -> None:
    source = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    review = source[
        source.index("async function reviewMemoryCard") : source.index(
            "async function setMode"
        )
    ]

    assert "currentMemoryCard?.item_id" in review
    assert "study_memory_review_item" in review
    assert "item_id: itemId" in review
    assert review.index("study_memory_review_item") < review.index(
        "study_memory_card_review"
    )


def test_study_companion_static_ui_browser_smoke_desktop_reduced_motion() -> None:
    playwright_sync_api = pytest.importorskip("playwright.sync_api")
    if not _has_playwright_chromium():
        pytest.skip("Playwright chromium is not installed")

    expect = playwright_sync_api.expect
    sync_playwright = playwright_sync_api.sync_playwright
    static_files = {
        "index.html": ("text/html", (STATIC_DIR / "index.html").read_text(encoding="utf-8")),
        "style.css": ("text/css", (STATIC_DIR / "style.css").read_text(encoding="utf-8")),
        "i18n.js": ("text/javascript", (STATIC_DIR / "i18n.js").read_text(encoding="utf-8")),
        "surface-panels.js": ("text/javascript", (STATIC_DIR / "surface-panels.js").read_text(encoding="utf-8")),
        "knowledge-map.js": ("text/javascript", (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")),
            "outcome-formatters.js": ("text/javascript", (STATIC_DIR / "outcome-formatters.js").read_text(encoding="utf-8")),
            "model-runtime.js": ("text/javascript", (STATIC_DIR / "model-runtime.js").read_text(encoding="utf-8")),
            "request-utils.js": ("text/javascript", (STATIC_DIR / "request-utils.js").read_text(encoding="utf-8")),
        "document-controller.js": ("text/javascript", (STATIC_DIR / "document-controller.js").read_text(encoding="utf-8")),
        "dependency-controller.js": ("text/javascript", (STATIC_DIR / "dependency-controller.js").read_text(encoding="utf-8")),
        "quick-card-controller.js": ("text/javascript", (STATIC_DIR / "quick-card-controller.js").read_text(encoding="utf-8")),
        "main.js": ("text/javascript", (STATIC_DIR / "main.js").read_text(encoding="utf-8")),
        "katex.min.js": ("text/javascript", (STATIC_DIR / "katex.min.js").read_text(encoding="utf-8")),
        "katex-render.js": ("text/javascript", (STATIC_DIR / "katex-render.js").read_text(encoding="utf-8")),
        "katex.min.css": ("text/css", (STATIC_DIR / "katex.min.css").read_text(encoding="utf-8")),
    }
    en_bundle = json.loads((PLUGIN_DIR / "i18n" / "en.json").read_text(encoding="utf-8"))
    status_payload = {
        "status": "ready",
        "active_mode": "companion",
        "is_first_run": True,
        "dependencies": {
            "rapidocr": {"available": True},
            "tesseract": {"available": True},
            "dxcam": {"available": True},
        },
        "knowledge_summary": {"topic_count": 4, "edge_count": 3},
        "habit": {
            "available": True,
            "checkin": {"checked_in": False},
            "pomodoro": {"state": "idle"},
            "summary": {"total_focus_minutes": 24, "completed_goal_count": 2, "goal_count": 4},
        },
        "memory_deck": {"card_count": 12, "due_count": 3, "due_cards": []},
    }
    knowledge_payload = {
        "summary": {"topic_count": 2, "edge_count": 0},
        "nodes": [
            {
                "id": "college_cs_arrays",
                "name": "Arrays",
                "stage": "college",
                "subject": "computer_science",
                "course_family": "c_programming",
                "chapter": "C Language",
                "unit": "Arrays and pointers",
                "depth": 2,
            },
            {
                "id": "college_cs_pointers",
                "name": "Pointers",
                "stage": "college",
                "subject": "computer_science",
                "course_family": "c_programming",
                "chapter": "C Language",
                "unit": "Arrays and pointers",
                "depth": 3,
            },
        ],
        "edges": [],
    }

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 1100},
            reduced_motion="reduce",
        )
        page = context.new_page()
        console_errors: list[str] = []
        page_errors: list[str] = []
        run_ids: list[str] = []
        run_entries: dict[str, tuple[str, dict]] = {}
        entry_calls: list[str] = []
        active_scope: dict = {}

        page.on(
            "console",
            lambda msg: console_errors.append(msg.text) if msg.type == "error" else None,
        )
        page.on("pageerror", lambda exc: page_errors.append(str(exc)))

        def route_handler(route):
            request = route.request
            url = request.url
            path = url.split("://", 1)[1].split("/", 1)[1].split("?", 1)[0]
            if path == "plugin/study_companion/ui":
                path = "plugin/study_companion/ui/"
            if path == "plugin/study_companion/ui/":
                content_type, body = static_files["index.html"]
                route.fulfill(status=200, content_type=content_type, body=body)
                return
            if path.startswith("plugin/study_companion/ui/"):
                file_name = path.rsplit("/", 1)[-1]
                if file_name in static_files:
                    content_type, body = static_files[file_name]
                    route.fulfill(status=200, content_type=content_type, body=body)
                    return
                if path.startswith("plugin/study_companion/ui/assets/yui/"):
                    asset = STATIC_DIR / path.removeprefix("plugin/study_companion/ui/")
                    route.fulfill(status=200, content_type="image/webp", body=asset.read_bytes())
                    return
            if path == "plugin/study_companion/ui-api/i18n/en.json":
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(en_bundle),
                )
                return
            if path == "runs" and request.method == "POST":
                run_id = f"run-{len(run_ids) + 1}"
                run_ids.append(run_id)
                request_payload = request.post_data_json
                entry_id = str(request_payload.get("entry_id") or "")
                args = request_payload.get("args") or {}
                run_entries[run_id] = (entry_id, args)
                entry_calls.append(entry_id)
                if entry_id == "study_set_practice_scope":
                    requested = dict(args.get("scope") or {})
                    active_scope.clear()
                    active_scope.update(
                        {
                            **requested,
                            "scope_key": "ps1_browser_scope",
                            "scope_revision": 1,
                            "display_path": [
                                "college",
                                "computer_science",
                                "c_programming",
                                "C Language",
                                "Arrays and pointers",
                            ],
                        }
                    )
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"run_id": run_id, "status": "queued"}),
                )
                return
            run_match = re.fullmatch(r"runs/(run-\d+)(/export)?", path)
            if run_match and not run_match.group(2):
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps({"status": "succeeded"}),
                )
                return
            if run_match and run_match.group(2):
                entry_id, _args = run_entries[run_match.group(1)]
                if entry_id == "study_knowledge_map":
                    response_data = knowledge_payload
                elif entry_id == "study_get_practice_scope":
                    response_data = {
                        "active": bool(active_scope),
                        "scope": dict(active_scope),
                        "scope_revision": 1 if active_scope else 0,
                    }
                elif entry_id == "study_set_practice_scope":
                    response_data = {
                        "active": True,
                        "scope": dict(active_scope),
                        "scope_revision": 1,
                    }
                elif entry_id == "study_clear_practice_scope":
                    active_scope.clear()
                    response_data = {"active": False, "scope": {}, "scope_revision": 2}
                elif entry_id == "study_question_context" and active_scope:
                    response_data = {
                        "selection_context_id": "selection-browser-scope",
                        "selected_topic_id": "college_cs_arrays",
                        "selected_topic_name": "Arrays",
                        "selection_reason": "scope_seed",
                        "scope_key": "ps1_browser_scope",
                        "scope_revision": 1,
                        "practice_scope": dict(active_scope),
                    }
                elif entry_id == "study_generate_targeted_question":
                    response_data = {
                        "question_id": "question-browser-scope",
                        "attempt_id": "attempt-browser-scope",
                        "question": "Explain how an array relates to a pointer.",
                        "selected_topic_id": "college_cs_arrays",
                        "selected_topic_name": "Arrays",
                        "difficulty": 0.5,
                    }
                elif entry_id == "study_question_context":
                    response_data = {"no_data": True, "selection_reason": "no_data"}
                else:
                    response_data = status_payload
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "items": [
                                {
                                    "type": "json",
                                    "json": {"success": True, "data": response_data},
                                }
                            ]
                        }
                    ),
                )
                return
            route.fulfill(status=404, body=f"Unhandled test route: {path}")

        page.route("**/*", route_handler)
        page.goto("http://neko.test/plugin/study_companion/ui/?locale=en", wait_until="networkidle")

        expect(page).to_have_title(en_bundle["ui.title"])
        expect(page.locator(".hero")).to_be_visible()
        expect(page.locator(".study-hub")).to_be_visible()
        expect(page.locator("#firstRunGuide")).to_be_visible(timeout=5000)
        expect(page.locator("#primaryDiagnosis")).to_have_attribute("data-severity", "ok")
        expect(page.locator("#modeSwitch")).to_have_attribute("data-ready", "true")
        expect(page.locator("#nekoCoachRecommendation")).to_be_visible()
        expect(page.locator("#nekoCoachPrimaryAction")).to_be_visible()
        expect(page.locator("#nekoCoachSecondaryAction")).to_be_visible()
        expect(page.locator(".neko-coach__stage")).to_have_count(0)

        practice_panel = page.locator("#practicePanel")
        practice_toggle = page.locator("#practicePanel > summary")
        page.evaluate("closeLearningProfileModal()")
        expect(practice_panel).not_to_have_attribute("open", "")
        expect(page.locator("#questionContextCard")).to_be_hidden()

        practice_toggle.click()
        expect(practice_panel).to_have_attribute("open", "")
        expect(page.locator("#questionContextCard")).to_be_visible()

        practice_toggle.click()
        expect(practice_panel).not_to_have_attribute("open", "")
        expect(page.locator("#questionContextCard")).to_be_hidden()

        page.evaluate("handleFeatureAction('practice')")
        expect(practice_panel).to_have_attribute("open", "")
        expect(page.locator("#generateQuestionBtn")).to_be_focused()

        practice_toggle.click()
        expect(practice_panel).not_to_have_attribute("open", "")
        page.evaluate("generateQuestion().catch(() => undefined)")
        expect(practice_panel).to_have_attribute("open", "")

        metrics = page.evaluate(
            """() => {
                const paint = performance.getEntriesByType('paint')
                    .find((entry) => entry.name === 'first-contentful-paint');
                const navigation = performance.getEntriesByType('navigation')[0];
                const shell = document.querySelector('.page-shell').getBoundingClientRect();
                const hero = document.querySelector('.hero').getBoundingClientRect();
                const hub = document.querySelector('.study-hub').getBoundingClientRect();
                const modeSwitch = document.querySelector('#modeSwitch').getBoundingClientRect();
                const coach = document.querySelector('#nekoCoachPanel').getBoundingClientRect();
                const coachBody = document.querySelector('.neko-coach__body').getBoundingClientRect();
                const transitionDuration = getComputedStyle(
                    document.querySelector('#modeSwitch'),
                    '::before'
                ).transitionDuration;
                return {
                    fcp: paint ? paint.startTime : null,
                    domContentLoaded: navigation ? navigation.domContentLoadedEventEnd : performance.now(),
                    shellWidth: shell.width,
                    shellRight: shell.right,
                    coachGap: coach.left - shell.right,
                    heroWidth: hero.width,
                    coachLeft: coach.left,
                    coachRightGap: window.innerWidth - coach.right,
                    coachTop: coach.top,
                    coachWidth: coach.width,
                    coachBodyTop: coachBody.top,
                    hubTop: hub.top,
                    heroTop: hero.top,
                    modeSwitchWidth: modeSwitch.width,
                    viewportWidth: window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                    reducedMotion: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
                    transitionDuration,
                };
            }"""
        )
        paint_or_dom_ready = metrics["fcp"] or metrics["domContentLoaded"]
        assert paint_or_dom_ready <= 1200, metrics
        assert metrics["reducedMotion"] is True
        assert metrics["transitionDuration"] in {"0.001s", "1ms"}, metrics
        assert metrics["shellWidth"] >= 690, metrics
        assert metrics["heroWidth"] >= 690, metrics
        assert metrics["coachWidth"] >= 580, metrics
        assert metrics["coachLeft"] >= metrics["shellRight"], metrics
        assert 82 <= metrics["coachGap"] <= 86, metrics
        assert 48 <= metrics["coachRightGap"] <= 52, metrics
        assert 0 <= metrics["coachBodyTop"] - metrics["coachTop"] <= 64, metrics
        assert metrics["hubTop"] > metrics["heroTop"], metrics
        assert metrics["modeSwitchWidth"] >= 360, metrics
        assert metrics["scrollWidth"] <= metrics["viewportWidth"] + 1, metrics
        assert console_errors == []
        assert page_errors == []

        original_url = page.url
        page.evaluate("openHostedSurface('knowledge-map', 'knowledge')")
        surface_drawer = page.locator("#surfaceDrawer")
        expect(surface_drawer).to_have_attribute("data-presentation", "dialog")
        expect(surface_drawer).to_have_attribute("role", "dialog")
        expect(surface_drawer).to_have_attribute("aria-modal", "true")
        dialog_panel = page.locator(".surface-drawer__panel")
        zoom_out = page.locator('[data-action="zoom-out"]')
        zoom_reset = page.locator('[data-action="zoom-reset"]')
        zoom_in = page.locator('[data-action="zoom-in"]')
        expect(surface_drawer).to_have_attribute("data-window-scale", "100")
        expect(zoom_reset).to_have_text("100%")
        expect(zoom_reset).to_be_disabled()
        expect(zoom_in).to_be_disabled()
        expect(page.locator('.knowledge-map-zoom [role="status"]')).to_have_text("Current window size: 100%")
        size_100 = dialog_panel.evaluate("node => ({ width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })")
        zoom_out.click()
        expect(surface_drawer).to_have_attribute("data-window-scale", "90")
        size_90 = dialog_panel.evaluate("node => ({ width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })")
        assert size_90["width"] < size_100["width"]
        assert size_90["height"] < size_100["height"]
        page.locator('[data-action="zoom-out"]').click()
        expect(surface_drawer).to_have_attribute("data-window-scale", "75")
        size_75 = dialog_panel.evaluate("node => ({ width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })")
        assert size_75["width"] < size_90["width"]
        assert size_75["height"] < size_90["height"]
        page.locator('[data-action="zoom-out"]').click()
        expect(surface_drawer).to_have_attribute("data-window-scale", "60")
        expect(page.locator('[data-action="zoom-out"]')).to_be_disabled()
        page.locator('[data-action="zoom-reset"]').click()
        expect(surface_drawer).to_have_attribute("data-window-scale", "100")
        restored_size = dialog_panel.evaluate("node => ({ width: node.getBoundingClientRect().width, height: node.getBoundingClientRect().height })")
        assert abs(restored_size["width"] - size_100["width"]) <= 1
        assert abs(restored_size["height"] - size_100["height"]) <= 1

        # Real Static flow: stage -> module -> chapter -> unit -> scope CTA. Setting
        # scope must only focus the explicit Generate button; it must never call LLM.
        page.locator('.knowledge-stage-selector button[data-stage="college"]').click()
        page.locator('.knowledge-subject-selector button[data-subject="computer_science"]').click()
        arrays_topic = page.get_by_role("button", name="Arrays", exact=True)
        arrays_topic.click()
        expect(page.locator(".knowledge-node-detail-dialog")).to_be_visible()
        page.locator(".knowledge-node-detail-dialog__close").click()
        expect(arrays_topic).to_be_focused()
        module_field = page.locator(".knowledge-hierarchy-picker__field").filter(
            has_text=en_bundle["ui.knowledge.course_family_label"]
        )
        module_field.locator("select").select_option("c_programming")
        chapter_field = page.locator(".knowledge-hierarchy-picker__field").filter(
            has_text=en_bundle["ui.knowledge.chapter_label"]
        )
        chapter_field.locator("select").select_option("C Language")
        unit_field = page.locator(".knowledge-hierarchy-picker__field").filter(
            has_text=en_bundle["ui.knowledge.unit_label"]
        )
        unit_field.locator("select").select_option("Arrays and pointers")
        calls_before_scope = len(entry_calls)
        page.get_by_role(
            "button", name=en_bundle["ui.knowledge.practice_current_scope"]
        ).first.click()
        expect(surface_drawer).to_have_attribute("aria-hidden", "true")
        expect(practice_panel).to_have_attribute("open", "")
        expect(page.locator("#generateQuestionBtn")).to_be_focused()
        scope_calls = entry_calls[calls_before_scope:]
        assert scope_calls == ["study_set_practice_scope"]
        assert "study_generate_targeted_question" not in scope_calls
        set_run = next(
            run_id
            for run_id in reversed(run_ids)
            if run_entries[run_id][0] == "study_set_practice_scope"
        )
        submitted_scope = run_entries[set_run][1]["scope"]
        assert submitted_scope == {
            "schema_version": 1,
            "mode": "explicit_scope",
            "stage": "college",
            "subject": "computer_science",
            "course_family": "c_programming",
            "chapter": "C Language",
            "unit": "Arrays and pointers",
        }
        expect(page.locator("#practiceScopePath")).to_contain_text("Arrays and pointers")

        calls_before_generate = len(entry_calls)
        page.locator("#generateQuestionBtn").click()
        expect(page.locator("#questionText")).to_have_text(
            "Explain how an array relates to a pointer."
        )
        generated_calls = entry_calls[calls_before_generate:]
        assert generated_calls[:3] == [
            "study_get_practice_scope",
            "study_question_context",
            "study_generate_targeted_question",
        ]
        assert generated_calls[3:] == ["study_status"]

        page.evaluate("openHostedSurface('knowledge-map', 'knowledge')")
        expect(surface_drawer).to_have_attribute("aria-hidden", "false")
        page.evaluate("closeLearningProfileModal()")
        page.set_viewport_size({"width": 375, "height": 900})
        expect(page.locator(".knowledge-map-zoom")).to_be_visible()
        controls_fit = page.locator(".knowledge-map-zoom").evaluate(
            "node => { const rect = node.getBoundingClientRect(); return rect.left >= 0 && rect.right <= innerWidth; }"
        )
        assert controls_fit is True
        page.set_viewport_size({"width": 1440, "height": 1100})
        page.wait_for_timeout(700)
        page.evaluate("closeLearningProfileModal()")
        dialog_metrics = page.locator(".surface-drawer__panel").evaluate(
            """panel => {
                const rect = panel.getBoundingClientRect();
                return {
                    left: rect.left,
                    right: window.innerWidth - rect.right,
                    top: rect.top,
                    bottom: window.innerHeight - rect.bottom,
                };
            }"""
        )
        assert abs(dialog_metrics["left"] - dialog_metrics["right"]) <= 1, dialog_metrics
        assert abs(dialog_metrics["top"] - dialog_metrics["bottom"]) <= 1, dialog_metrics
        assert page.url == original_url
        assert len(context.pages) == 1

        page.locator("#surfaceDrawerCloseBtn").click()
        expect(surface_drawer).to_have_attribute("aria-hidden", "true")
        page.evaluate("openSurfaceDrawer('pomodoro-panel')")
        expect(surface_drawer).to_have_attribute("data-presentation", "drawer")
        expect(surface_drawer).not_to_have_attribute("role", "dialog")
        expect(surface_drawer).not_to_have_attribute("aria-modal", "true")
        page.wait_for_timeout(20)
        drawer_metrics = page.locator(".surface-drawer__panel").evaluate(
            """panel => {
                const rect = panel.getBoundingClientRect();
                return {
                    left: rect.left,
                    right: window.innerWidth - rect.right,
                };
            }"""
        )
        assert 12 <= drawer_metrics["right"] <= 16, drawer_metrics
        assert drawer_metrics["left"] > drawer_metrics["right"], drawer_metrics
        page.locator("#surfaceDrawerCloseBtn").click()

        page.set_viewport_size({"width": 480, "height": 900})
        expect(page.locator("#nekoCoachPanel")).to_be_visible()
        expect(page.locator("#nekoCoachRecommendation")).to_be_visible()
        expect(page.locator("#nekoCoachPrimaryAction")).to_be_visible()
        expect(page.locator("#nekoCoachSecondaryAction")).to_be_visible()
        expect(page.locator(".neko-coach__stage")).to_have_count(0)

        narrow_metrics = page.evaluate(
            """() => {
                const coach = document.querySelector('#nekoCoachPanel').getBoundingClientRect();
                const coachBody = document.querySelector('.neko-coach__body').getBoundingClientRect();
                return {
                    coachPosition: getComputedStyle(document.querySelector('#nekoCoachPanel')).position,
                    coachLeft: coach.left,
                    coachRight: coach.right,
                    coachTop: coach.top,
                    coachBodyTop: coachBody.top,
                    viewportWidth: window.innerWidth,
                    scrollWidth: document.documentElement.scrollWidth,
                };
            }"""
        )
        assert narrow_metrics["coachPosition"] == "relative", narrow_metrics
        assert narrow_metrics["coachLeft"] >= 0, narrow_metrics
        assert narrow_metrics["coachRight"] <= narrow_metrics["viewportWidth"] + 1, narrow_metrics
        assert 0 <= narrow_metrics["coachBodyTop"] - narrow_metrics["coachTop"] <= 64, narrow_metrics
        assert narrow_metrics["scrollWidth"] <= narrow_metrics["viewportWidth"] + 1, narrow_metrics
        assert console_errors == []
        assert page_errors == []

        context.close()
        browser.close()


def test_study_companion_math_and_mastery_colors_meet_contrast_contract() -> None:
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    surface_utils = (SURFACES_DIR / "study_surface_utils.ts").read_text(
        encoding="utf-8"
    )
    variables = _css_variables(style_css)

    assert "#replyText .katex" in style_css
    assert ".study-panel__math-reply .katex" in style_css
    assert "#replyText .study-reply-section" in style_css
    assert ".study-panel__math-reply .study-reply-section" in style_css
    assert ".study-reply-section--analysis" in style_css
    assert ".study-reply-section--process" in style_css
    assert ".study-reply-section--answer" in style_css
    assert ".study-reply-section--transfer" in style_css
    assert ".study-reply-section__title" in style_css
    assert re.search(
        r"#replyText \.katex,\s*\.study-panel__math-reply \.katex \{[^}]*color: var\(--ink\);",
        style_css,
        flags=re.DOTALL,
    )
    assert ".study-panel__math-reply .katex" in surface_utils
    assert ".study-panel__math-reply .study-reply-section" in surface_utils
    assert ".study-panel__math-reply .study-reply-section--analysis" in surface_utils
    assert ".study-panel__math-reply .study-reply-section--process" in surface_utils
    assert ".study-panel__math-reply .study-reply-section--answer" in surface_utils
    assert ".study-panel__math-reply .study-reply-section--transfer" in surface_utils
    assert re.search(
        r"\.study-panel__math-reply \.katex \{[^}]*color: var\(--ink\);",
        surface_utils,
        flags=re.DOTALL,
    )

    assert _contrast_ratio(variables["ink"], "#ffffff") >= 4.5
    for name in [
        "mastery-new",
        "mastery-weak",
        "mastery-progress",
        "mastery-good",
        "mastery-mastered",
    ]:
        assert _contrast_ratio(variables["ink"], variables[name]) >= 4.5, name

    mastery_to_var = {
        "new": "mastery-new",
        "weak": "mastery-weak",
        "progress": "mastery-progress",
        "good": "mastery-good",
        "mastered": "mastery-mastered",
    }
    assert ".knowledge-node {" in surface_utils
    assert re.search(r"\.knowledge-node \{[^}]*color: var\(--ink\);", surface_utils, flags=re.DOTALL)
    for mastery, variable in mastery_to_var.items():
        assert re.search(
            rf'\.knowledge-node\[data-mastery="{mastery}"\] \{{[^}}]*background: var\(--{variable}\);',
            surface_utils,
            flags=re.DOTALL,
        ), mastery


def test_study_companion_diagnosis_states_are_distinguishable_under_protanopia() -> None:
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")

    assert "? '\\u2713'" in main_js
    assert "diagnosis.severity === 'error' ? '\\u26A0'" in main_js
    assert "diagnosis.severity === 'warning' ? '!'" in main_js
    assert ": 'i'))" in main_js

    simulated_luminance = {
        severity: _relative_luminance(
            _simulate_protanopia(_diagnosis_background_sample(style_css, severity))
        )
        for severity in ("ok", "warning", "error")
    }
    for first, second in (("ok", "warning"), ("ok", "error"), ("warning", "error")):
        delta = abs(simulated_luminance[first] - simulated_luminance[second])
        assert delta >= 0.02, (first, second, simulated_luminance)


def test_study_companion_static_ui_copy_is_i18n_backed() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    knowledge_map_js = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")
    dynamic_ui_source = main_js + knowledge_map_js
    html_keys = _html_i18n_keys(index_html)

    for key in REQUIRED_STATIC_UI_KEYS:
        assert key in html_keys, key
    for key in REQUIRED_DYNAMIC_UI_KEYS:
        assert key in dynamic_ui_source, key

    for locale in LOCALES:
        bundle = json.loads((PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8"))
        missing = sorted(key for key in html_keys | set(REQUIRED_DYNAMIC_UI_KEYS) if not bundle.get(key))
        assert missing == [], f"{locale}: {missing}"
        if locale != "en":
            broken = sorted(
                key
                for key in REQUIRED_STATIC_UI_KEYS + REQUIRED_DYNAMIC_UI_KEYS
                if "??" in bundle.get(key, "")
            )
            assert broken == [], f"{locale}: {broken}"


def test_study_companion_static_dependency_ui_contract() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    dependency_js = (STATIC_DIR / "dependency-controller.js").read_text(encoding="utf-8")

    assert "const DEPENDENCY_KEYS = Object.freeze(['rapidocr', 'tesseract', 'dxcam']);" in main_js
    assert "Object.values(deps).filter" not in main_js
    assert "Object.values(dependencies).filter" not in main_js
    assert "dependencies.ocr_readiness || {}" in main_js
    assert "ui.diagnosis.text_ready.title" in main_js
    assert "ui.diagnosis.ocr_unavailable.title" in main_js
    assert "ui.diagnosis.knowledge_empty.title" in main_js
    assert "ui.diagnosis.multiple_issues.title" in main_js

    for dependency in ("rapidocr", "tesseract", "dxcam"):
        assert f'data-dependency="{dependency}"' in index_html
    assert 'data-dependency-action="tesseract"' in index_html
    assert 'data-dependency-action="rapidocr_models"' in index_html
    assert "item.can_download_models === true" in dependency_js
    assert "String(item.detail || '').toLowerCase() === 'missing_model_files'" in dependency_js
    assert "/ui-api/tesseract/install" in dependency_js
    assert "/ui-api/rapidocr-models" in dependency_js
    assert "new EventSource" in dependency_js
    assert "while (state.busy && state.kind === kind && state.taskId === taskId)" in dependency_js
    assert "consecutiveFailures >= 3" in dependency_js
    assert "await refreshStatus({ updateReply: false });" in main_js
    assert "StudyDependencyController?.initialize" in main_js
    assert ".dependency-progress[hidden]" in style_css


def test_reviewed_settings_scope_and_dialog_contracts_are_isolated() -> None:
    main = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    model_runtime = (STATIC_DIR / "model-runtime.js").read_text(encoding="utf-8")
    knowledge_map = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")
    quick_card = (STATIC_DIR / "quick-card-controller.js").read_text(encoding="utf-8")
    interactive_capture = (
        STATIC_DIR.parent / "interactive_screenshot.py"
    ).read_text(encoding="utf-8")

    assert "StudyModelRuntime.refresh(callPlugin, t, tf)" in main
    assert "await loadPracticeScope({ silent: true }).catch(() => null);" in main
    assert "if (!settingsCommunicationEnabled) return;" in main
    assert "Object.assign(Object.create(null)" in model_runtime
    assert "STATUS_FALLBACKS[key]" in model_runtime
    assert "model_unavailable:" in model_runtime
    assert "unit: chapter ?" in knowledge_map
    assert "let dialogPromise = null;" in quick_card
    assert "if (dialogPromise) return dialogPromise;" in quick_card
    assert 'if error_code == "bridge_error":' in interactive_capture
    assert 'error_code = "no_renderer"' in interactive_capture

def test_study_companion_neko_coach_actions_avoid_stale_ocr_and_unused_scene_cache() -> None:
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")

    assert "NEKO_COACH_SCENE_RECOMMENDATIONS" not in main_js
    assert "nekoCoachCurrentScene" not in main_js
    assert "async function runOcr(options = {})" in main_js
    assert "options.clearWhenEmpty && studyInput" in main_js
    assert "studyInput.value = '';" in main_js
    assert "return data;" in main_js
    assert "const ocrData = await runOcr({ clearWhenEmpty: true });" in main_js
    assert "String(ocrData?.text || '').trim() || studyInputImageValue" in main_js


def test_study_companion_feature_dock_opens_knowledge_map_in_centered_dialog() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    knowledge_map_js = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")
    surface_panels_js = (STATIC_DIR / "surface-panels.js").read_text(encoding="utf-8")

    feature_dock = re.search(
        r'<nav class="feature-dock"(?P<body>.*?)</nav>',
        index_html,
        flags=re.DOTALL,
    )
    assert feature_dock is not None
    feature_html = feature_dock.group("body")
    for action in (
        "memory",
        "review",
        "knowledge",
        "pomodoro",
        "checkin",
        "export",
    ):
        assert f'data-feature-action="{action}"' in feature_html
    assert 'data-feature-action="memory" data-open-surface="memory-deck-list"' in feature_html
    assert 'data-feature-action="practice"' not in feature_html
    assert 'data-feature-action="explain"' not in feature_html

    quick_panel = re.search(
        r'<div class="quick-panels"(?P<body>.*?)</div>',
        index_html,
        flags=re.DOTALL,
    )
    assert quick_panel is not None
    quick_panel_html = quick_panel.group("body")

    expected_surfaces = {
        "pomodoro-panel",
        "due-review-panel",
        "habit-dashboard",
    }
    for surface_id in expected_surfaces:
        assert f'data-open-surface="{surface_id}"' in quick_panel_html

    assert "const surfaceOpenButtons = Array.from(document.querySelectorAll('[data-open-surface]'));" in main_js
    assert "const featureActionButtons = Array.from(document.querySelectorAll('[data-feature-action]'));" in main_js
    assert "const surfaceDrawerBody = $id('surfaceDrawerBody');" in main_js
    assert "renderSurfaceDrawerBody(surfaceId)" in main_js
    assert "surfaceDrawerBody.replaceChildren" in main_js
    assert "StudyCompanionSurfacePanels" in main_js
    assert "surface-panels.js" in index_html
    assert "study_knowledge_map" in main_js
    assert "loadKnowledgeMapIntoDrawer" in main_js
    assert "const isDialog = surfaceId === 'knowledge-map';" in main_js
    assert "surfaceDrawer.dataset.presentation = isDialog ? 'dialog' : 'drawer';" in main_js
    assert "surfaceDrawer.setAttribute('role', 'dialog');" in main_js
    assert "surfaceDrawer.setAttribute('aria-modal', 'true');" in main_js
    assert "surfaceDrawer.removeAttribute('role');" in main_js
    assert "surfaceDrawer.removeAttribute('aria-modal');" in main_js
    assert "window.open(" not in main_js
    assert "window.close(" not in main_js
    assert "dedicatedSurfaceId" not in main_js
    assert "study-panel surface-shell" in main_js
    assert "knowledge-node" in knowledge_map_js
    assert "renderKnowledgeZoomControls" in knowledge_map_js
    assert "surfaceDrawer.dataset.windowScale = String(level)" in knowledge_map_js
    for entry_id in (
        "study_memory_due_reviews",
        "study_memory_list_decks",
        "study_pomodoro_status",
        "study_checkin_status",
        "study_export_notes",
    ):
        assert entry_id in surface_panels_js
    assert "pomodoro-ring" in surface_panels_js
    assert "pomodoro-ring__time" in surface_panels_js
    assert "pomodoro-actions" in surface_panels_js
    assert "pomodoro-duration" in surface_panels_js
    assert "focus_minutes: Math.min(120" in surface_panels_js
    assert "pomodoro-ring__value" in surface_panels_js
    assert "stroke-dashoffset" in surface_panels_js
    assert ".surface-shell" in (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "window.location.assign(managerUrl)" not in main_js
    assert "window.parent === window" not in main_js
    assert "/ui/plugins" not in main_js
    assert "surfaceDrawerFrame" not in main_js
    assert 'id="surfaceDrawerFrame"' not in index_html
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert '.surface-drawer[data-presentation="dialog"] {' in style_css
    assert 'place-items: center;' in style_css
    assert '.surface-drawer[data-presentation="dialog"] .surface-drawer__panel' in style_css
    assert '.surface-drawer[data-presentation="dialog"][data-open="true"] .surface-drawer__panel' in style_css
    assert 'data-dedicated-surface' not in style_css
    assert ".knowledge-map-zoom" in style_css
    for scale in (60, 75, 90, 100):
        assert f'.surface-drawer[data-presentation="dialog"][data-window-scale="{scale}"] .surface-drawer__panel' in style_css
    assert "knowledge-map-viewport" not in style_css


def test_study_companion_advanced_settings_surface_entries_are_complete() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    panel_expectations = {
        "panel-knowledge": {
            "knowledge-map",
            "knowledge-contribution-settings",
        },
        "panel-memory": {
            "memory-deck-list",
            "memory-importer",
            "due-review-panel",
        },
        "panel-habit": {
            "habit-dashboard",
            "pomodoro-panel",
            "daily-goal-editor",
        },
        "panel-data": {
            "session-summary",
            "note-exporter",
        },
    }
    for panel_id, surface_ids in panel_expectations.items():
        panel_match = re.search(
            rf'<div id="{panel_id}"(?P<body>.*?)</div>\s*</div>',
            index_html,
            flags=re.DOTALL,
        )
        assert panel_match is not None, panel_id
        panel_html = panel_match.group("body")
        for surface_id in surface_ids:
            assert f'data-open-surface="{surface_id}"' in panel_html, panel_id


def test_static_knowledge_contribution_settings_drawer_toggles_opt_in() -> None:
    if shutil.which("node") is None:
        pytest.skip("node is not installed")

    frontend_dir = Path(__file__).resolve().parents[4] / "frontend" / "plugin-manager"
    if not (frontend_dir / "node_modules" / "happy-dom").is_dir():
        pytest.skip("frontend/plugin-manager node_modules with happy-dom is not installed")

    script = r"""
import { Window } from 'happy-dom';
import fs from 'node:fs';
import path from 'node:path';

const source = fs.readFileSync(path.join(process.env.STUDY_COMPANION_STATIC_DIR, 'surface-panels.js'), 'utf8');
const window = new Window({ url: 'http://testserver/plugin/study_companion/ui/?locale=en' });
const { document } = window;
const calls = [];
let rejectToggle = false;

window.eval(source);
const root = window.StudyCompanionSurfacePanels.render('knowledge-contribution-settings', {
  t: (_key, fallback) => fallback,
  label: () => 'Contribution Settings',
  callPlugin: async (entryId, args = {}) => {
    calls.push({ entryId, args });
    if (entryId === 'study_anonymous_knowledge_preview') {
      return { opt_in: false, summary: { total: 5, queue_count: 2 } };
    }
    if (entryId === 'study_set_knowledge_contribution_opt_in') {
      if (rejectToggle) throw new Error('opt-in persistence failed');
      return { opt_in: args.opt_in, summary: { total: 6, queue_count: 3 } };
    }
    throw new Error(`unexpected entry call: ${entryId}`);
  },
});
document.body.appendChild(root);

for (let attempt = 0; attempt < 20; attempt += 1) {
  if (calls.some((call) => call.entryId === 'study_anonymous_knowledge_preview')) break;
  await new Promise((resolve) => window.setTimeout(resolve, 0));
}
await new Promise((resolve) => window.setTimeout(resolve, 0));

if (!root.textContent.includes('Disabled') || !root.textContent.includes('5')) {
  throw new Error(`initial contribution state missing: ${root.textContent}`);
}

const toggle = root.querySelector('[data-surface-action="knowledge-contribution-toggle"]');
if (!toggle) {
  throw new Error(`contribution toggle missing: ${root.outerHTML}`);
}
toggle.click();
for (let attempt = 0; attempt < 20; attempt += 1) {
  if (calls.some((call) => call.entryId === 'study_set_knowledge_contribution_opt_in')) break;
  await new Promise((resolve) => window.setTimeout(resolve, 0));
}
await new Promise((resolve) => window.setTimeout(resolve, 0));

const toggleCall = calls.find((call) => call.entryId === 'study_set_knowledge_contribution_opt_in');
if (!toggleCall || toggleCall.args.opt_in !== true) {
  throw new Error(`toggle call missing opt_in=true: ${JSON.stringify(calls)}`);
}
if (!root.textContent.includes('Enabled') || !root.textContent.includes('6')) {
  throw new Error(`updated contribution state missing: ${root.textContent}`);
}

rejectToggle = true;
const retryToggle = root.querySelector('[data-surface-action="knowledge-contribution-toggle"]');
if (!retryToggle || retryToggle.disabled) {
  throw new Error(`retry contribution toggle unavailable: ${root.outerHTML}`);
}
retryToggle.click();
for (let attempt = 0; attempt < 20; attempt += 1) {
  if (root.textContent.includes('opt-in persistence failed')) break;
  await new Promise((resolve) => window.setTimeout(resolve, 0));
}
await new Promise((resolve) => window.setTimeout(resolve, 0));

const recoveredToggle = root.querySelector('[data-surface-action="knowledge-contribution-toggle"]');
if (!root.textContent.includes('opt-in persistence failed')) {
  throw new Error(`toggle failure message missing: ${root.textContent}`);
}
if (!recoveredToggle || recoveredToggle.disabled) {
  throw new Error(`toggle did not recover after failure: ${root.outerHTML}`);
}
"""
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=frontend_dir,
        env={**os.environ, "STUDY_COMPANION_STATIC_DIR": str(STATIC_DIR)},
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_study_companion_brand_variables_stay_in_sync_between_static_and_tsx() -> None:
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    surface_utils = (SURFACES_DIR / "study_surface_utils.ts").read_text(
        encoding="utf-8"
    )
    static_vars = _css_variables(style_css)
    hosted_vars = _css_variables(surface_utils)

    shared_variables = [
        "bg",
        "paper",
        "paper-strong",
        "ink",
        "muted",
        "line",
        "brand",
        "brand-strong",
        "accent",
        "accent-strong",
        "warning",
        "warning-strong",
        "warning-bg",
        "study-companion",
        "study-interactive",
        "study-teaching",
        "mastery-new",
        "mastery-weak",
        "mastery-progress",
        "mastery-good",
        "mastery-mastered",
        "pomodoro-focus",
        "pomodoro-break-short",
        "pomodoro-break-long",
        "fsrs-again",
        "fsrs-hard",
        "fsrs-good",
        "fsrs-easy",
        "shadow",
        "shadow-strong",
        "radius",
        "radius-sm",
        "transition-fast",
        "transition-normal",
        "transition-slow",
        "study-content-font-size",
        "study-math-font-size",
    ]

    for name in shared_variables:
        assert hosted_vars[name] == static_vars[name], name


def test_study_companion_brand_contract_rejects_legacy_neutral_theme() -> None:
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    style_css = (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    surface_utils = (SURFACES_DIR / "study_surface_utils.ts").read_text(
        encoding="utf-8"
    )
    combined = "\n".join([index_html, style_css, surface_utils])
    variables = _css_variables(style_css)

    assert variables["brand"] == "#2f7d57"
    assert '"Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif' in style_css
    assert '"Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif' in surface_utils
    assert not re.search(r"font-family\s*:[^;]*\bInter\b", combined)
    for legacy_color in (
        "#f6f7f9",
        "#d8dde6",
        "#6a7484",
        "#40c5f1",
        "#f08c99",
        "#3da5d9",
    ):
        assert legacy_color not in combined.lower(), legacy_color


def test_study_companion_memory_deck_load_is_shared_and_blocks_early_save() -> None:
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    index_html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    surface_panels_js = (STATIC_DIR / "surface-panels.js").read_text(encoding="utf-8")
    load_start = main_js.index("async function loadDeckOptions()")
    load_end = main_js.index("\nfunction finishMemoryDeckDialog", load_start)
    load_source = main_js[load_start:load_end]

    assert "function loadDecks(ctx, readyTarget)" in surface_panels_js
    assert "if (!initialMemoryDecks)" in surface_panels_js
    assert "loadDecks({ callPlugin }, memoryAddBtn)" in load_source
    assert re.search(r'<button id="memoryAddBtn"[^>]*\bdisabled\b', index_html)
    assert "readyTarget.disabled = false;" in surface_panels_js
    assert "initialMemoryDecks = null;" in surface_panels_js


def test_study_companion_memory_deck_cache_only_reuses_inflight_load() -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")

    script = r"""
const fs = require('node:fs');
global.window = {};
eval(fs.readFileSync(process.env.SURFACE_PANELS_JS, 'utf8'));

(async () => {
  let calls = 0;
  const ctx = {
    callPlugin: async () => ({
      decks: [{ id: `deck-${++calls}` }],
      has_more: false,
    }),
  };
  const firstLoad = window.StudyCompanionSurfacePanels.loadDecks(ctx);
  const sharedLoad = window.StudyCompanionSurfacePanels.loadDecks(ctx);
  const [first, shared] = await Promise.all([firstLoad, sharedLoad]);
  const refreshed = await window.StudyCompanionSurfacePanels.loadDecks(ctx);
  console.log(JSON.stringify({ calls, first, shared, refreshed }));
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
"""
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        encoding="utf-8",
        env={
            **os.environ,
            "SURFACE_PANELS_JS": str(STATIC_DIR / "surface-panels.js"),
        },
        timeout=10,
    )
    payload = json.loads(result.stdout)

    assert payload == {
        "calls": 2,
        "first": [{"id": "deck-1"}],
        "shared": [{"id": "deck-1"}],
        "refreshed": [{"id": "deck-2"}],
    }


def test_study_companion_fetch_timeout_honors_preaborted_signal_and_cleans_listener() -> None:
    main_js = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    request_utils_js = (STATIC_DIR / "request-utils.js").read_text(encoding="utf-8")
    fetch_start = main_js.index("async function fetchWithTimeout(")
    fetch_end = main_js.index("\nfunction setSettingsConfigStatus", fetch_start)
    fetch_source = main_js[fetch_start:fetch_end]

    assert "StudyRequestUtils.fetchWithTimeout" in fetch_source
    assert "if (signal?.aborted) relayAbort();" in request_utils_js
    assert "signal?.addEventListener('abort', relayAbort);" in request_utils_js
    assert "signal?.removeEventListener('abort', relayAbort);" in request_utils_js


def test_static_pomodoro_honors_disabled_custom_duration() -> None:
    source = (STATIC_DIR / "surface-panels.js").read_text(encoding="utf-8")
    start = source.index("function renderPomodoro")
    end = source.index("function renderHabit", start)
    pomodoro = source[start:end]

    assert "status.config?.allow_custom_duration !== false" in pomodoro
    assert "durationInput.disabled = isRunning || !allowCustomDuration;" in pomodoro
    assert "allowCustomDuration ? { focus_minutes:" in pomodoro
    assert "} : {})" in pomodoro
