from __future__ import annotations

import json
import gzip
import re
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

    assert "@media (min-width: 1180px)" in style_css
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
    assert "const modeSelect = document.getElementById('modeSelect');" in main_js
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
    assert '<script src="./knowledge-map.js?v=study-hotfix-20260714-review"></script>' in index_html
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
                route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "items": [
                                {
                                    "type": "json",
                                    "json": {"success": True, "data": status_payload},
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
                const transitionDuration = getComputedStyle(
                    document.querySelector('#modeSwitch'),
                    '::before'
                ).transitionDuration;
                return {
                    fcp: paint ? paint.startTime : null,
                    domContentLoaded: navigation ? navigation.domContentLoadedEventEnd : performance.now(),
                    shellWidth: shell.width,
                    shellRight: shell.right,
                    heroWidth: hero.width,
                    coachLeft: coach.left,
                    coachWidth: coach.width,
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
        assert metrics["shellWidth"] >= 1000, metrics
        assert metrics["heroWidth"] >= 1000, metrics
        assert metrics["coachWidth"] >= 300, metrics
        assert metrics["coachLeft"] >= metrics["shellRight"], metrics
        assert metrics["hubTop"] > metrics["heroTop"], metrics
        assert metrics["modeSwitchWidth"] >= 360, metrics
        assert metrics["scrollWidth"] <= metrics["viewportWidth"] + 1, metrics
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
    html_keys = _html_i18n_keys(index_html)

    for key in REQUIRED_STATIC_UI_KEYS:
        assert key in html_keys, key
    for key in REQUIRED_DYNAMIC_UI_KEYS:
        assert key in main_js, key

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


def test_study_companion_feature_dock_and_quick_panels_open_in_page_drawer() -> None:
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
    assert "const surfaceDrawerBody = document.getElementById('surfaceDrawerBody');" in main_js
    assert "renderSurfaceDrawerBody(surfaceId)" in main_js
    assert "surfaceDrawerBody.replaceChildren" in main_js
    assert "StudyCompanionSurfacePanels" in main_js
    assert "surface-panels.js" in index_html
    assert "study_knowledge_map" in main_js
    assert "loadKnowledgeMapIntoDrawer" in main_js
    assert "study-panel surface-shell" in main_js
    assert "knowledge-node" in knowledge_map_js
    for entry_id in (
        "study_memory_due_reviews",
        "study_memory_list_decks",
        "study_pomodoro_status",
        "study_checkin_status",
        "study_export_notes",
    ):
        assert entry_id in surface_panels_js
    assert "pomodoro-ring" in surface_panels_js
    assert ".surface-shell" in (STATIC_DIR / "style.css").read_text(encoding="utf-8")
    assert "window.location.assign(managerUrl)" not in main_js
    assert "window.parent === window" not in main_js
    assert "/ui/plugins" not in main_js
    assert "surfaceDrawerFrame" not in main_js
    assert 'id="surfaceDrawerFrame"' not in index_html


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
