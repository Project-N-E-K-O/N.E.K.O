from __future__ import annotations

from pathlib import Path

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")
Page = playwright_sync_api.Page
expect = playwright_sync_api.expect

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _bootstrap_page(mock_page: Page) -> None:
    mock_page.route(
        "**/persona-onboarding-harness",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<!doctype html><html><body><div id='app'></div></body></html>",
        ),
    )
    mock_page.goto("http://neko.test/persona-onboarding-harness")
    mock_page.evaluate(
        """
        () => {
            window.t = function(key, fallbackOrOptions) {
                if (typeof fallbackOrOptions === 'string') {
                    return fallbackOrOptions;
                }
                if (fallbackOrOptions && typeof fallbackOrOptions.defaultValue === 'string') {
                    return fallbackOrOptions.defaultValue;
                }
                return key;
            };
            window.waitForStorageLocationStartupBarrier = async function() {};
            window.universalTutorialManager = {
                isTutorialRunning: true,
            };
            window.__tutorialPromptState = 'completed';
            window.__tutorialPromptStatePayload = null;
            window.__tutorialPromptFetchFailuresRemaining = 0;
            window.__personaOnboardingState = {
                status: 'pending',
                manual_reselect_character_name: '',
                manual_reselect_requested_at: '',
            };
            window.__requestLog = [];
            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const method = String((options && options.method) || 'GET').toUpperCase();
                let body = null;
                if (options && typeof options.body === 'string' && options.body) {
                    body = JSON.parse(options.body);
                }
                window.__requestLog.push({ url: requestUrl, method, body });

                if (requestUrl === '/api/characters/persona-onboarding-state') {
                    if (method === 'POST') {
                        const nextStatus = (body && typeof body.status === 'string')
                            ? body.status
                            : window.__personaOnboardingState.status;
                        window.__personaOnboardingState = {
                            ...window.__personaOnboardingState,
                            status: nextStatus,
                        };
                        return new Response(JSON.stringify({
                            success: true,
                            state: window.__personaOnboardingState,
                        }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' },
                        });
                    }
                    return new Response(JSON.stringify({
                        success: true,
                        state: window.__personaOnboardingState,
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }

                if (requestUrl === '/api/characters/current_catgirl') {
                    return new Response(JSON.stringify({
                        current_catgirl: '小天',
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }

                if (requestUrl === '/api/characters/persona-presets') {
                    return new Response(JSON.stringify({
                        success: true,
                        presets: [{
                            preset_id: 'classic_genki',
                            display_name: '经典元气猫娘',
                            summary_fallback: '元气满满',
                            preview_line: '太棒了喵！',
                            profile: {
                                '性格原型': '经典元气猫娘',
                                '口癖': '太棒了喵！ / 喵呜~',
                                '爱好': '陪伴 / 温暖',
                                '雷点': '冷漠敷衍 / 否定感受',
                            },
                        }],
                    }), {
                        status: 200,
                            headers: { 'Content-Type': 'application/json' },
                        });
                    }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    if (window.__tutorialPromptFetchFailuresRemaining > 0) {
                        window.__tutorialPromptFetchFailuresRemaining -= 1;
                        throw new Error('tutorial prompt unavailable');
                    }
                    const payload = window.__tutorialPromptStatePayload;
                    return new Response(JSON.stringify({
                        success: true,
                        state: {
                            status: (payload && payload.status) || window.__tutorialPromptState || 'completed',
                            deferred_until: (payload && payload.deferred_until) || 0,
                            never_remind: !!(payload && payload.never_remind),
                            user_cohort: (payload && payload.user_cohort) || 'unknown',
                        },
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }

                if (requestUrl === '/api/characters/persona-reselect-current') {
                    if (method === 'DELETE') {
                        window.__personaOnboardingState.manual_reselect_character_name = '';
                        window.__personaOnboardingState.manual_reselect_requested_at = '';
                    }
                    return new Response(JSON.stringify({
                        success: true,
                        state: window.__personaOnboardingState,
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }

                if (requestUrl === '/api/characters/character/%E5%B0%8F%E5%A4%A9/persona-selection' && method === 'PUT') {
                    if (body && body.source === 'onboarding') {
                        window.__personaOnboardingState = {
                            ...window.__personaOnboardingState,
                            status: 'completed',
                        };
                    } else if (body && body.source === 'manual_reselect') {
                        window.__personaOnboardingState = {
                            ...window.__personaOnboardingState,
                            manual_reselect_character_name: '',
                            manual_reselect_requested_at: '',
                        };
                    }
                    return new Response(JSON.stringify({
                        success: true,
                        selection: {
                            mode: 'override',
                            preset_id: body.preset_id,
                        },
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }

                throw new Error('Unexpected request: ' + method + ' ' + requestUrl);
            };
        }
        """
    )


def _has_playwright_browser() -> bool:
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return False

    try:
        with sync_playwright() as playwright:
            return Path(playwright.chromium.executable_path).exists()
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _has_playwright_browser(),
    reason="requires Playwright browser binaries",
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """This browser-only test does not need the repo-level mock memory server."""
    yield


@pytest.mark.frontend
def test_onboarding_waits_for_tutorial_completion_before_showing(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_have_count(0)

    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: { page: 'home' }
            }));
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()


@pytest.mark.frontend
def test_onboarding_waits_for_tutorial_prompt_settlement_before_showing(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'observing';
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.wait_for_timeout(200)
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_have_count(0)

    mock_page.evaluate(
        """
        () => {
            window.__tutorialPromptState = 'started';
            window.universalTutorialManager.isTutorialRunning = true;
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: { page: 'home', source: 'idle_prompt' }
            }));
        }
        """
    )

    mock_page.wait_for_timeout(100)
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_have_count(0)

    mock_page.evaluate(
        """
        () => {
            window.__tutorialPromptState = 'completed';
            window.universalTutorialManager.isTutorialRunning = false;
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: { page: 'home', source: 'idle_prompt' }
            }));
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()


@pytest.mark.frontend
def test_onboarding_does_not_preempt_new_user_tutorial_prompt_flow(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static" / "css" / "character_personality_onboarding.css"))
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptStatePayload = {
                status: 'observing',
                deferred_until: 0,
                never_remind: false,
                user_cohort: 'new',
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.wait_for_timeout(3400)
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_have_count(0)

    mock_page.evaluate(
        """
        () => {
            window.__tutorialPromptStatePayload = {
                status: 'completed',
                deferred_until: 0,
                never_remind: false,
                user_cohort: 'new',
            };
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: { page: 'home', source: 'idle_prompt' }
            }));
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()


@pytest.mark.frontend
def test_onboarding_does_not_treat_tutorial_prompt_fetch_failure_as_settled(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptFetchFailuresRemaining = 2;
            window.__tutorialPromptState = 'completed';
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.wait_for_timeout(200)
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_have_count(0)

    mock_page.wait_for_function("() => window.__tutorialPromptFetchFailuresRemaining === 0")
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()


@pytest.mark.frontend
def test_onboarding_started_state_waits_without_busy_fetch_loop(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'started';
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.wait_for_timeout(350)
    const_requests = mock_page.evaluate(
        "() => window.__requestLog.filter((entry) => entry.url === '/api/tutorial-prompt/state').length"
    )
    assert const_requests <= 3
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_have_count(0)


@pytest.mark.frontend
def test_onboarding_restores_pointer_events_for_clickable_overlay(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static" / "css" / "character_personality_onboarding.css"))
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'completed';
            document.body.style.pointerEvents = 'none';
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()
    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator("[data-testid='character-personality-confirm']")).to_be_visible()

    mock_page.locator("[data-testid='character-personality-back']").click()
    mock_page.locator("[data-testid='character-personality-skip']").click()
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_hidden()
    assert mock_page.evaluate("() => document.body.style.pointerEvents") == 'none'


@pytest.mark.frontend
def test_onboarding_marks_overlay_controls_as_no_drag_for_desktop_clicks(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.add_style_tag(path=str(PROJECT_ROOT / "static" / "css" / "character_personality_onboarding.css"))
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator("[data-testid='character-personality-confirm']")).to_be_visible()

    app_regions = mock_page.evaluate(
        """
        () => {
            const read = (selector) => {
                const node = document.querySelector(selector);
                return node ? getComputedStyle(node).getPropertyValue('-webkit-app-region').trim() : null;
            };
            return {
                overlay: read("[data-testid='character-personality-overlay']"),
                shell: read('.character-personality-shell'),
                skip: read("[data-testid='character-personality-skip']"),
                confirm: read("[data-testid='character-personality-confirm']"),
                card: read("[data-testid='character-personality-preset-classic_genki']"),
            };
        }
        """
    )

    assert app_regions == {
        "overlay": "no-drag",
        "shell": "no-drag",
        "skip": "no-drag",
        "confirm": "no-drag",
        "card": "no-drag",
    }


@pytest.mark.frontend
def test_manual_character_personality_reselect_opens_directly_on_home_refresh(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'observing';
            window.__personaOnboardingState = {
                status: 'completed',
                handled_at: '2026-04-29T12:00:00Z',
                manual_reselect_character_name: '小天',
                manual_reselect_requested_at: '2026-04-29T12:10:00Z',
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()
    assert not any(
        entry["url"] == "/api/tutorial-prompt/state"
        for entry in mock_page.evaluate("() => window.__requestLog")
    )


@pytest.mark.frontend
def test_manual_character_personality_reselect_skip_clears_manual_pending_state(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'completed';
            window.__personaOnboardingState = {
                status: 'completed',
                handled_at: '2026-04-29T12:00:00Z',
                manual_reselect_character_name: '小天',
                manual_reselect_requested_at: '2026-04-29T12:10:00Z',
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()
    mock_page.locator("[data-testid='character-personality-skip']").click()
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_hidden()

    request_log = mock_page.evaluate("() => window.__requestLog")
    assert any(
        entry["url"] == "/api/characters/persona-reselect-current"
        and entry["method"] == "DELETE"
        for entry in request_log
    )


@pytest.mark.frontend
def test_onboarding_skip_persists_state_and_closes_overlay(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_visible()
    mock_page.locator("[data-testid='character-personality-skip']").click()
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_hidden()

    request_log = mock_page.evaluate("() => window.__requestLog")
    assert any(
        entry["url"] == "/api/characters/persona-onboarding-state"
        and entry["method"] == "POST"
        and entry["body"] == {"status": "skipped"}
        for entry in request_log
    )


@pytest.mark.frontend
def test_onboarding_confirm_dispatches_character_update_event(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.__personalityEventDetail = null;
            window.addEventListener('neko:character-personality-updated', (event) => {
                window.__personalityEventDetail = event.detail;
            });
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    mock_page.locator("[data-testid='character-personality-confirm']").click()
    mock_page.wait_for_function("() => window.__personalityEventDetail !== null")

    event_detail = mock_page.evaluate("() => window.__personalityEventDetail")
    assert event_detail == {
        "characterName": "小天",
        "presetId": "classic_genki",
    }

    request_log = mock_page.evaluate("() => window.__requestLog")
    assert sum(
        1
        for entry in request_log
        if entry["url"] == "/api/characters/character/%E5%B0%8F%E5%A4%A9/persona-selection"
        and entry["method"] == "PUT"
    ) == 1
    assert not any(
        entry["url"] == "/api/characters/persona-onboarding-state"
        and entry["method"] == "POST"
        and entry["body"] == {"status": "completed"}
        for entry in request_log
    )


@pytest.mark.frontend
def test_onboarding_confirm_preserves_event_detail_during_pending_back_navigation(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.__personalityEventDetail = null;
            window.__releaseDelayedPersonaSelection = null;
            const originalFetch = window.fetch.bind(window);
            window.fetch = function(url, options) {
                const requestUrl = String(url);
                const method = String((options && options.method) || 'GET').toUpperCase();
                const pathname = new URL(requestUrl, window.location.origin).pathname;
                if (
                    method === 'PUT' &&
                    /^\\/api\\/characters\\/character\\/[^/]+\\/persona-selection$/.test(pathname)
                ) {
                    return new Promise((resolve) => {
                        window.__releaseDelayedPersonaSelection = () => resolve(originalFetch(url, options));
                    });
                }
                return originalFetch(url, options);
            };
            window.addEventListener('neko:character-personality-updated', (event) => {
                window.__personalityEventDetail = event.detail;
            });
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    mock_page.locator("[data-testid='character-personality-confirm']").click()
    mock_page.wait_for_function("() => typeof window.__releaseDelayedPersonaSelection === 'function'")
    mock_page.locator("[data-testid='character-personality-back']").click()
    mock_page.evaluate("() => window.__releaseDelayedPersonaSelection()")
    mock_page.wait_for_function("() => window.__personalityEventDetail !== null")

    event_detail = mock_page.evaluate("() => window.__personalityEventDetail")
    assert event_detail == {
        "characterName": "小天",
        "presetId": "classic_genki",
    }


@pytest.mark.frontend
def test_manual_reselect_confirm_does_not_send_followup_delete(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'completed';
            window.__personaOnboardingState = {
                status: 'completed',
                handled_at: '2026-04-29T12:00:00Z',
                manual_reselect_character_name: '小天',
                manual_reselect_requested_at: '2026-04-29T12:10:00Z',
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    mock_page.locator("[data-testid='character-personality-confirm']").click()
    expect(mock_page.locator("[data-testid='character-personality-overlay']")).to_be_hidden()

    request_log = mock_page.evaluate("() => window.__requestLog")
    assert sum(
        1
        for entry in request_log
        if entry["url"] == "/api/characters/character/%E5%B0%8F%E5%A4%A9/persona-selection"
        and entry["method"] == "PUT"
    ) == 1
    assert not any(
        entry["url"] == "/api/characters/persona-reselect-current"
        and entry["method"] == "DELETE"
        for entry in request_log
    )


@pytest.mark.frontend
def test_onboarding_hides_context_warning_for_first_time_flow(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-warning']")).to_have_count(0)


@pytest.mark.frontend
def test_manual_reselect_shows_context_warning_in_both_steps(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.__tutorialPromptState = 'completed';
            window.__personaOnboardingState = {
                status: 'completed',
                handled_at: '2026-04-29T12:00:00Z',
                manual_reselect_character_name: '小天',
                manual_reselect_requested_at: '2026-04-29T12:10:00Z',
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    warning = mock_page.locator("[data-testid='character-personality-warning']:visible")
    expect(warning).to_have_count(1)
    expect(warning).to_contain_text("当前角色")

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator("[data-testid='character-personality-warning']:visible")).to_have_count(1)
    expect(mock_page.locator("[data-testid='character-personality-warning']:visible")).to_contain_text("当前角色")


@pytest.mark.frontend
def test_onboarding_preview_streams_selected_personality_copy(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator("[data-testid='character-personality-preview-stream']")).to_be_visible()
    expect(mock_page.locator("[data-testid='character-personality-preview-stream']")).to_contain_text("太棒了喵", timeout=5000)


@pytest.mark.frontend
def test_onboarding_translate_falls_back_when_window_t_returns_raw_key(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.t = function(key) {
                return key;
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-role='title']")).to_have_text("你想让我变成哪种陪着你的样子喵？")
    expect(mock_page.locator("[data-testid='character-personality-skip']")).to_have_text("先跳过喵")
    expect(mock_page.locator("[data-role='current-character']")).to_have_text("小天")


@pytest.mark.frontend
def test_onboarding_state_post_mock_persists_updated_status(mock_page: Page):
    _bootstrap_page(mock_page)

    persisted_status = mock_page.evaluate(
        """
        async () => {
            await window.fetch('/api/characters/persona-onboarding-state', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ status: 'completed' }),
            });
            const response = await window.fetch('/api/characters/persona-onboarding-state');
            const payload = await response.json();
            return payload.state.status;
        }
        """
    )

    assert persisted_status == "completed"


@pytest.mark.frontend
def test_onboarding_uses_i18n_copy_for_user_visible_text(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.t = function(key, fallbackOrOptions) {
                const translations = {
                    'memory.characterSelection.onboardingEyebrow': 'Starter personality',
                    'memory.characterSelection.chooseTitle': 'How should I show up for you?',
                    'memory.characterSelection.chooseHint': 'You can revisit this from settings later.',
                    'memory.characterSelection.currentCharacter': 'Current character: {{name}}',
                    'memory.characterSelection.stageOneIntro': 'Pick the vibe first, then preview how I will sound.',
                    'memory.characterSelection.classic_genki.name': 'Sunny Spark',
                    'memory.characterSelection.classic_genki.desc': 'Bright, affectionate, and always on your side.',
                    'memory.characterSelection.classic_genki.previewLine': 'Yay, let me stay by your side today too.',
                    'memory.characterSelection.classic_genki.tag1': 'High empathy',
                    'memory.characterSelection.classic_genki.tag2': 'Cozy energy',
                    'memory.characterSelection.classic_genki.tag3': 'Emotional recharge',
                    'memory.characterSelection.previewLabel': 'Voice preview',
                    'memory.characterSelection.previewLead': 'If you pick {{name}} for me, this is how I will sound.',
                    'memory.characterSelection.classic_genki.profileSummary': 'A bright little sun who notices your mood fast and cheers you on.',
                    'memory.characterSelection.classic_genki.hiddenRule': 'Emotional reassurance comes first in every interaction.',
                    'memory.characterSelection.detailSpeechHabits': 'Signature phrases',
                    'memory.characterSelection.detailHobbies': 'Favorite moods',
                    'memory.characterSelection.detailBoundaries': 'Hard boundaries',
                    'memory.characterSelection.classic_genki.speechHabits': 'yay / nyan / you are amazing',
                    'memory.characterSelection.classic_genki.hobbies': 'company / snacks / cheering you on',
                    'memory.characterSelection.classic_genki.boundaries': 'cold replies / dismissing the user',
                };
                const options = (
                    fallbackOrOptions && typeof fallbackOrOptions === 'object' && !Array.isArray(fallbackOrOptions)
                ) ? fallbackOrOptions : null;
                const fallback = typeof fallbackOrOptions === 'string'
                    ? fallbackOrOptions
                    : (options && typeof options.defaultValue === 'string' ? options.defaultValue : key);
                const template = Object.prototype.hasOwnProperty.call(translations, key)
                    ? translations[key]
                    : fallback;
                return String(template).replace(/{{\\s*(\\w+)\\s*}}/g, (_, name) => {
                    if (options && Object.prototype.hasOwnProperty.call(options, name)) {
                        return String(options[name]);
                    }
                    return '';
                });
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator("[data-role='eyebrow']")).to_have_text("Starter personality")
    expect(mock_page.locator("[data-role='title']")).to_have_text("How should I show up for you?")
    expect(mock_page.locator("[data-role='current-character']")).to_have_text("小天")
    expect(mock_page.locator(".character-personality-intro")).to_have_text(
        "Pick the vibe first, then preview how I will sound."
    )
    expect(mock_page.locator(".character-personality-card-name")).to_have_text("Sunny Spark")

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator(".stage-two-title")).to_have_text("Voice preview")
    expect(mock_page.locator(".character-personality-preview-label")).to_have_text("Voice preview")
    expect(mock_page.locator("[data-testid='character-personality-preview-stream']")).to_contain_text(
        "If you pick Sunny Spark for me",
        timeout=5000,
    )
    expect(mock_page.locator(".detail-group-title").first).to_have_text("Signature phrases")
    expect(mock_page.locator("#detailCatchphrases .detail-pill").nth(0)).to_have_text("yay")
    expect(mock_page.locator("#detailCatchphrases .detail-pill").nth(1)).to_have_text("nyan")
    expect(mock_page.locator("#detailCatchphrases .detail-pill").nth(2)).to_have_text("you are amazing")


@pytest.mark.frontend
def test_settings_uses_i18n_copy_for_warning_and_user_visible_text(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.t = function(key, fallbackOrOptions) {
                const translations = {
                    'memory.characterSelection.settingsEyebrow': 'Persona retune',
                    'memory.characterSelection.settingsTitle': 'Let me switch into a new little vibe for you.',
                    'memory.characterSelection.settingsHint': 'I will only override this character\\'s active personality, promise.',
                    'memory.characterSelection.currentCharacter': 'Current character: {{name}}',
                    'memory.characterSelection.stageOneIntro': 'Pick the mood first, then listen to how I sound, nya.',
                    'memory.characterSelection.contextWarning': 'Heads up, nya: switching my personality clears this character\\'s recent chat context.',
                    'memory.characterSelection.classic_genki.name': 'Sunny Spark',
                    'memory.characterSelection.classic_genki.desc': 'Bright, affectionate, and always on your side.',
                    'memory.characterSelection.classic_genki.previewLine': 'Yay, let me stay by your side today too.',
                    'memory.characterSelection.previewLabel': 'Voice preview',
                    'memory.characterSelection.previewLead': 'If you pick {{name}} for me, this is how I will sound.',
                    'memory.characterSelection.classic_genki.profileSummary': 'A bright little sun who notices your mood fast and cheers you on.',
                    'memory.characterSelection.classic_genki.hiddenRule': 'Emotional reassurance comes first in every interaction.',
                    'memory.characterSelection.detailSpeechHabits': 'Signature phrases',
                    'memory.characterSelection.detailHobbies': 'Favorite moods',
                    'memory.characterSelection.detailBoundaries': 'Hard boundaries',
                    'memory.characterSelection.classic_genki.speechHabits': 'yay / nyan / you are amazing',
                    'memory.characterSelection.classic_genki.hobbies': 'company / snacks / cheering you on',
                    'memory.characterSelection.classic_genki.boundaries': 'cold replies / dismissing the user',
                    'memory.characterSelection.back': 'Pick again',
                    'memory.characterSelection.confirmGreeting': 'Use this vibe',
                    'memory.characterSelection.skip': 'Maybe later',
                };
                const options = (
                    fallbackOrOptions && typeof fallbackOrOptions === 'object' && !Array.isArray(fallbackOrOptions)
                ) ? fallbackOrOptions : null;
                const fallback = typeof fallbackOrOptions === 'string'
                    ? fallbackOrOptions
                    : (options && typeof options.defaultValue === 'string' ? options.defaultValue : key);
                const template = Object.prototype.hasOwnProperty.call(translations, key)
                    ? translations[key]
                    : fallback;
                return String(template).replace(/{{\\s*(\\w+)\\s*}}/g, (_, name) => {
                    if (options && Object.prototype.hasOwnProperty.call(options, name)) {
                        return String(options[name]);
                    }
                    return '';
                });
            };
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        async () => {
            await window.CharacterPersonalityOnboarding.openFromSettings('小天');
        }
        """
    )

    expect(mock_page.locator("[data-role='eyebrow']")).to_have_text("Persona retune")
    expect(mock_page.locator("[data-role='title']")).to_have_text("Let me switch into a new little vibe for you.")
    expect(mock_page.locator("[data-role='hint']")).to_have_text(
        "I will only override this character's active personality, promise."
    )
    expect(mock_page.locator(".character-personality-intro")).to_have_text(
        "Pick the mood first, then listen to how I sound, nya."
    )
    expect(mock_page.locator("[data-testid='character-personality-warning']:visible")).to_have_text(
        "Heads up, nya: switching my personality clears this character's recent chat context."
    )
    expect(mock_page.locator("[data-testid='character-personality-skip']")).to_have_text("Maybe later")

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()
    expect(mock_page.locator("[data-testid='character-personality-warning']:visible")).to_have_text(
        "Heads up, nya: switching my personality clears this character's recent chat context."
    )
    expect(mock_page.locator("[data-testid='character-personality-back']")).to_have_text("Pick again")
    expect(mock_page.locator("[data-testid='character-personality-confirm']")).to_have_text("Use this vibe")


@pytest.mark.frontend
def test_onboarding_uses_dynamic_character_name_and_split_detail_pills(mock_page: Page):
    _bootstrap_page(mock_page)
    mock_page.evaluate("() => { window.universalTutorialManager.isTutorialRunning = false; }")
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "js" / "character_personality_onboarding.js"))

    mock_page.evaluate(
        """
        () => {
            window.CharacterPersonalityOnboarding.bootstrap();
        }
        """
    )

    expect(mock_page.locator(".cph-badge")).to_have_text("小天")

    mock_page.locator("[data-testid='character-personality-preset-classic_genki']").click()

    expect(mock_page.locator("#previewTitleBadge")).to_have_text("经典元气猫娘")
    expect(mock_page.locator(".preview-avatar")).to_have_text("小天")

    speech_pills = mock_page.locator("#detailCatchphrases .detail-pill")
    expect(speech_pills).to_have_count(2)
    expect(speech_pills.nth(0)).to_have_text("太棒了喵！")
    expect(speech_pills.nth(1)).to_have_text("喵呜~")

    hobby_pills = mock_page.locator("#detailAtmosphere .detail-pill")
    expect(hobby_pills).to_have_count(2)
    expect(hobby_pills.nth(0)).to_have_text("陪伴")
    expect(hobby_pills.nth(1)).to_have_text("温暖")


@pytest.mark.frontend
def test_character_panel_exposes_personality_actions(mock_page: Page, running_server: str):
    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("networkidle")
    mock_page.wait_for_selector("body")

    mock_page.evaluate(
        """
        () => {
            const host = document.createElement('div');
            host.id = 'personality-panel-host';
            document.body.appendChild(host);

            buildCatgirlDetailForm('测试角色', {
                '档案名': '测试角色',
                '昵称': '测试',
                '_reserved': {
                    'persona_override': {
                        'preset_id': 'classic_genki',
                        'profile': {
                            '性格原型': '经典元气猫娘'
                        }
                    }
                }
            }, false, host);
        }
        """
    )

    expect(mock_page.locator("[data-testid='character-personality-select']")).to_be_visible()
    expect(mock_page.locator("[data-testid='character-personality-clear']")).to_be_visible()


@pytest.mark.frontend
def test_character_panel_personality_select_opens_onboarding(mock_page: Page, running_server: str):
    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("networkidle")
    mock_page.wait_for_selector("body")

    mock_page.evaluate(
        """
        () => {
            window.__openedPersonalityFor = '';
            window.CharacterPersonalityOnboarding = {
                openFromSettings(characterName) {
                    window.__openedPersonalityFor = characterName;
                }
            };

            const host = document.createElement('div');
            host.id = 'personality-panel-host-open';
            document.body.appendChild(host);

            buildCatgirlDetailForm('测试角色', {
                '档案名': '测试角色',
                '昵称': '测试',
            }, false, host);
        }
        """
    )

    mock_page.locator("[data-testid='character-personality-select']").click()
    assert mock_page.evaluate("() => window.__openedPersonalityFor") == "测试角色"


@pytest.mark.frontend
def test_new_character_panel_disables_personality_select_until_saved(mock_page: Page, running_server: str):
    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("networkidle")
    mock_page.wait_for_selector("body")

    mock_page.evaluate(
        """
        () => {
            window.__openedPersonalityFor = '';
            window.CharacterPersonalityOnboarding = {
                openFromSettings(characterName) {
                    window.__openedPersonalityFor = characterName;
                }
            };

            const host = document.createElement('div');
            host.id = 'personality-panel-host-new';
            document.body.appendChild(host);

            buildCatgirlDetailForm(null, {
                '档案名': '',
                '昵称': '',
            }, true, host);
        }
        """
    )

    select_button = mock_page.locator("[data-testid='character-personality-select']")
    expect(select_button).to_be_visible()
    expect(select_button).to_be_disabled()

    select_button.click(force=True)
    assert mock_page.evaluate("() => window.__openedPersonalityFor") == ""


@pytest.mark.frontend
def test_character_panel_close_removes_personality_update_listener(mock_page: Page, running_server: str):
    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("networkidle")
    mock_page.wait_for_selector("body")

    fetch_count = mock_page.evaluate(
        """
        async () => {
            window.cancelWorkshopPreviewLoads = () => {};
            window.disposeWorkshopVrm = async () => {};
            window.disposeWorkshopMmd = async () => {};
            window.destroyLive2DPreviewContext = async () => {};

            let personaSelectionFetches = 0;
            const originalFetch = window.fetch.bind(window);
            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                if (requestUrl.includes('/api/characters/character/') && requestUrl.endsWith('/persona-selection')) {
                    personaSelectionFetches += 1;
                    return new Response(JSON.stringify({
                        success: true,
                        selection: { mode: 'default', preset_id: '', profile: {} }
                    }), {
                        status: 200,
                        headers: { 'Content-Type': 'application/json' },
                    });
                }
                return originalFetch(url, options);
            };

            const overlay = document.createElement('div');
            overlay.className = 'catgirl-panel-overlay active';
            const wrapper = document.createElement('div');
            wrapper.className = 'catgirl-panel-wrapper';
            const host = document.createElement('div');
            host.id = 'personality-panel-host-close';
            wrapper.appendChild(host);
            overlay.appendChild(wrapper);
            document.body.appendChild(overlay);

            buildCatgirlDetailForm('测试角色', {
                '档案名': '测试角色',
                '昵称': '测试',
            }, false, host);

            await closeCatgirlPanel();

            window.dispatchEvent(new CustomEvent('neko:character-personality-updated', {
                detail: { characterName: '测试角色', presetId: 'classic_genki' }
            }));

            await new Promise(resolve => setTimeout(resolve, 50));
            return personaSelectionFetches;
        }
        """
    )

    assert fetch_count == 0


@pytest.mark.frontend
def test_character_panel_close_drops_late_personality_refresh_callback(mock_page: Page, running_server: str):
    mock_page.goto(f"{running_server}/character_card_manager")
    mock_page.wait_for_load_state("networkidle")
    mock_page.wait_for_selector("body")

    result = mock_page.evaluate(
        """
        async () => {
            window.cancelWorkshopPreviewLoads = () => {};
            window.disposeWorkshopVrm = async () => {};
            window.disposeWorkshopMmd = async () => {};
            window.destroyLive2DPreviewContext = async () => {};

            let resolveFetch;
            let personaSelectionFetches = 0;
            const originalFetch = window.fetch.bind(window);
            window.fetch = function(url, options) {
                const requestUrl = String(url);
                if (requestUrl.includes('/api/characters/character/') && requestUrl.endsWith('/persona-selection')) {
                    personaSelectionFetches += 1;
                    return new Promise((resolve) => {
                        resolveFetch = () => resolve(new Response(JSON.stringify({
                            success: true,
                            selection: { mode: 'default', preset_id: '', profile: {} }
                        }), {
                            status: 200,
                            headers: { 'Content-Type': 'application/json' },
                        }));
                    });
                }
                return originalFetch(url, options);
            };

            const overlay = document.createElement('div');
            overlay.className = 'catgirl-panel-overlay active';
            const wrapper = document.createElement('div');
            wrapper.className = 'catgirl-panel-wrapper';
            const host = document.createElement('div');
            host.id = 'personality-panel-host-race';
            wrapper.appendChild(host);
            overlay.appendChild(wrapper);
            document.body.appendChild(overlay);

            buildCatgirlDetailForm('测试角色', {
                '档案名': '测试角色',
                '昵称': '测试',
            }, false, host);

            window.dispatchEvent(new CustomEvent('neko:character-personality-updated', {
                detail: { characterName: '测试角色', presetId: 'classic_genki' }
            }));

            await new Promise(resolve => setTimeout(resolve, 20));
            await closeCatgirlPanel();
            resolveFetch();
            await new Promise(resolve => setTimeout(resolve, 80));

            window.dispatchEvent(new CustomEvent('neko:character-personality-updated', {
                detail: { characterName: '测试角色', presetId: 'classic_genki' }
            }));
            await new Promise(resolve => setTimeout(resolve, 50));

            return {
                personaSelectionFetches,
                hostConnected: host.isConnected,
            };
        }
        """
    )

    assert result["personaSelectionFetches"] == 1
    assert result["hostConnected"] is False
