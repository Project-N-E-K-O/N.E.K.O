import pytest
from playwright.sync_api import Page, expect
from pathlib import Path


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
    """This browser-only prompt test does not need the repo-level mock memory server."""
    yield


@pytest.mark.frontend
def test_home_prompt_queue_serializes_tutorial_and_autostart_prompts(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__requestLog = [];
            window.nekoLocalMutationSecurity = {
                getMutationHeaders: async function() {
                    return { 'X-CSRF-Token': 'test-token' };
                },
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'backend',
                    };
                },
                enable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: true,
                        authoritative: true,
                        provider: 'backend',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return false;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function(source) {
                    this.isTutorialRunning = true;
                    window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                        detail: {
                            page: 'home',
                            source: source || 'manual',
                        },
                    }));
                    return true;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                const method = String(requestOptions.method || 'GET').toUpperCase();
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }
                window.__requestLog.push({
                    url: requestUrl,
                    method: method,
                    body: body,
                });

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: true,
                        prompt_reason: 'idle_timeout',
                        prompt_token: 'tutorial-token',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/shown') {
                    return jsonResponse({
                        ok: true,
                        already_acknowledged: false,
                        state: {
                            status: 'prompted',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/decision') {
                    return jsonResponse({
                        ok: true,
                        state: {
                            status: body && body.result === 'started' ? 'started' : 'prompted',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: body && body.result === 'started',
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/tutorial-started') {
                    return jsonResponse({
                        ok: true,
                        tutorial_run_token: 'tutorial-run-token',
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: true,
                        prompt_reason: 'usage_timeout',
                        prompt_token: 'autostart-token',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/shown') {
                    return jsonResponse({
                        ok: true,
                        already_acknowledged: false,
                        state: {
                            status: 'prompted',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/decision') {
                    return jsonResponse({
                        ok: true,
                        state: {
                            status: 'deferred',
                            never_remind: false,
                            deferred_until: Date.now() + 60000,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + method + ' ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "common_dialogs.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    tutorial_title = mock_page.locator(".modal-title")
    expect(tutorial_title).to_have_text("要不要开始主页新手引导？", timeout=5000)
    expect(mock_page.locator(".modal-overlay")).to_have_count(1)

    mock_page.get_by_role("button", name="开始引导").click()

    expect(tutorial_title).to_have_text("要不要让 N.E.K.O 开机自动启动？", timeout=5000)
    expect(mock_page.locator(".modal-overlay")).to_have_count(1)

    mock_page.get_by_role("button", name="稍后再说").click()
    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)

    request_log = mock_page.evaluate("() => window.__requestLog")
    requested_urls = [entry["url"] for entry in request_log]

    assert "/api/tutorial-prompt/heartbeat" in requested_urls
    assert "/api/tutorial-prompt/tutorial-started" in requested_urls
    assert "/api/autostart-prompt/heartbeat" in requested_urls
    assert "/api/autostart-prompt/decision" in requested_urls


@pytest.mark.frontend
def test_tutorial_prompt_prefers_window_t_over_safe_t(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.t = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.safeT = function(key) {
                return key;
            };
            window.showStatusToast = function() {};
            window.nekoLocalMutationSecurity = {
                getMutationHeaders: async function() {
                    return { 'X-CSRF-Token': 'test-token' };
                },
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: false,
                        enabled: false,
                        authoritative: false,
                        provider: 'backend',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return false;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url) {
                const requestUrl = String(url);

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: true,
                        prompt_reason: 'idle_timeout',
                        prompt_token: 'tutorial-token',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/shown') {
                    return jsonResponse({
                        ok: true,
                        already_acknowledged: false,
                        state: {
                            status: 'prompted',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        prompt_reason: 'provider_unsupported',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "common_dialogs.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    expect(mock_page.locator(".modal-title")).to_have_text("要不要开始主页新手引导？", timeout=5000)


@pytest.mark.frontend
def test_tutorial_started_event_retries_failed_sync_on_heartbeat(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__tutorialStartedBodies = [];
            window.__tutorialCompletedBodies = [];
            window.__tutorialHeartbeatBodies = [];
            window.nekoLocalMutationSecurity = {
                getMutationHeaders: async function() {
                    return { 'X-CSRF-Token': 'test-token' };
                },
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: false,
                        enabled: false,
                        authoritative: false,
                        provider: 'backend',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: true,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body, status) {
                return new Response(JSON.stringify(body), {
                    status: status || 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    window.__tutorialHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/tutorial-started') {
                    window.__tutorialStartedBodies.push(body);
                    if (window.__tutorialStartedBodies.length === 1) {
                        return jsonResponse({
                            ok: false,
                            error: 'temporary_failure',
                        }, 500);
                    }
                    return jsonResponse({
                        ok: true,
                        tutorial_run_token: 'tutorial-run-token',
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/tutorial-completed') {
                    window.__tutorialCompletedBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function(
        "() => window.__tutorialHeartbeatBodies.length > 0",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: {
                    page: 'home',
                    source: 'manual',
                },
            }));
        }
        """
    )

    mock_page.wait_for_function(
        "() => window.__tutorialStartedBodies.length === 2",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: {
                    page: 'home',
                    source: 'manual',
                },
            }));
        }
        """
    )

    mock_page.wait_for_function(
        "() => window.__tutorialCompletedBodies.length === 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            tutorialStartedBodies: window.__tutorialStartedBodies.slice(),
            tutorialCompletedBodies: window.__tutorialCompletedBodies.slice(),
            tutorialHeartbeatBodies: window.__tutorialHeartbeatBodies.slice(),
        })
        """
    )

    assert len(result["tutorialStartedBodies"]) == 2
    assert result["tutorialStartedBodies"][0]["source"] == "manual"
    assert result["tutorialStartedBodies"][1]["source"] == "manual"
    assert len(result["tutorialCompletedBodies"]) == 1
    assert result["tutorialCompletedBodies"][0]["tutorial_run_token"] == "tutorial-run-token"
    assert len(result["tutorialHeartbeatBodies"]) >= 2


@pytest.mark.frontend
def test_tutorial_heartbeat_does_not_report_completed_while_tutorial_is_running(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__tutorialHeartbeatBodies = [];
            window.nekoLocalMutationSecurity = {
                getMutationHeaders: async function() {
                    return { 'X-CSRF-Token': 'test-token' };
                },
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: false,
                        enabled: false,
                        authoritative: false,
                        provider: 'backend',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: true,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    window.__tutorialHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function(
        "() => window.__tutorialHeartbeatBodies.length === 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => window.__tutorialHeartbeatBodies[0]
        """
    )

    assert result["manual_home_tutorial_viewed"] is True
    assert result["home_tutorial_completed"] is False


@pytest.mark.frontend
def test_autostart_provider_enable_syncs_prompt_heartbeat_state(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__requestLog = [];
            window.__autostartHeartbeatBodies = [];
            window.nekoAutostart = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                enable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: true,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                disable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return false;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                const method = String(requestOptions.method || 'GET').toUpperCase();
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }
                window.__requestLog.push({
                    url: requestUrl,
                    method: method,
                    body: body,
                });

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    window.__autostartHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: body && body.autostart_enabled ? 'completed' : 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: !!(body && body.autostart_enabled),
                        },
                    });
                }

                throw new Error('Unexpected request: ' + method + ' ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function("() => window.__autostartHeartbeatBodies.length > 0")

    mock_page.evaluate("() => window.nekoAutostartProvider.enable()")

    mock_page.wait_for_function(
        """
        () => window.__autostartHeartbeatBodies.some(function (body) {
            return !!(
                body
                && body.autostart_enabled === true
                && body.autostart_provider === 'neko-pc'
                && body.autostart_status_authoritative === true
            );
        })
        """,
        timeout=5000,
    )


@pytest.mark.frontend
def test_autostart_heartbeat_preserves_last_known_enabled_state_on_status_pull_failure(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__autostartHeartbeatBodies = [];
            window.nekoAutostart = {
                getStatus: async function() {
                    throw new Error('temporary_status_failure');
                },
                enable: async function() {
                    throw new Error('enable should not be called');
                },
                disable: async function() {
                    throw new Error('disable should not be called');
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    window.__autostartHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: body && body.autostart_enabled ? 'completed' : 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: !!(body && body.autostart_enabled),
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function(
        """
        () => window.__autostartHeartbeatBodies.some(function (body) {
            return !!(body && body.autostart_enabled === true);
        })
        """,
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => window.__autostartHeartbeatBodies.slice()
        """
    )

    assert len(result) >= 1
    assert result[0]["autostart_enabled"] is True


@pytest.mark.frontend
def test_desktop_autostart_status_event_syncs_prompt_heartbeat_state(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__autostartHeartbeatBodies = [];
            window.nekoAutostart = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                enable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                disable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return false;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: false,
                            home_tutorial_completed: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    window.__autostartHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: body && body.autostart_enabled ? 'completed' : 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: !!(body && body.autostart_enabled),
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function("() => window.__autostartHeartbeatBodies.length > 0")

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:autostart-status-changed', {
                detail: {
                    ok: true,
                    supported: true,
                    enabled: true,
                    authoritative: true,
                    provider: 'neko-pc',
                    platform: 'windows',
                    mechanism: 'electron-login-item',
                },
            }));
        }
        """
    )

    mock_page.wait_for_function(
        """
        () => window.__autostartHeartbeatBodies.some(function (body) {
            return !!(
                body
                && body.autostart_enabled === true
                && body.autostart_provider === 'neko-pc'
                && body.autostart_status_authoritative === true
            );
        })
        """,
        timeout=5000,
    )


@pytest.mark.frontend
def test_desktop_autostart_status_event_preserves_pending_flags_for_heartbeat(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")

    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__autostartHeartbeatBodies = [];
            window.nekoAutostart = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'macos',
                        mechanism: 'electron-login-item',
                    };
                },
                enable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'macos',
                        mechanism: 'electron-login-item',
                    };
                },
                disable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'macos',
                        mechanism: 'electron-login-item',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    window.__autostartHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function("() => window.__autostartHeartbeatBodies.length > 0")

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:autostart-status-changed', {
                detail: {
                    ok: false,
                    supported: true,
                    enabled: false,
                    authoritative: true,
                    provider: 'neko-pc',
                    platform: 'macos',
                    mechanism: 'electron-login-item',
                    requires_approval: true,
                    service_not_found: false,
                },
            }));
        }
        """
    )

    mock_page.wait_for_function(
        """
        () => window.__autostartHeartbeatBodies.some(function (body) {
            return !!(
                body
                && body.autostart_status_authoritative === true
                && body.autostart_requires_approval === true
                && body.autostart_service_not_found === false
            );
        })
        """,
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:autostart-status-changed', {
                detail: {
                    ok: false,
                    supported: true,
                    enabled: false,
                    authoritative: true,
                    provider: 'neko-pc',
                    platform: 'macos',
                    mechanism: 'electron-login-item',
                    requires_approval: false,
                    service_not_found: true,
                },
            }));
        }
        """
    )

    mock_page.wait_for_function(
        """
        () => window.__autostartHeartbeatBodies.some(function (body) {
            return !!(
                body
                && body.autostart_status_authoritative === true
                && body.autostart_requires_approval === false
                && body.autostart_service_not_found === true
            );
        })
        """,
        timeout=5000,
    )


@pytest.mark.frontend
def test_autostart_provider_reports_unsupported_status_when_desktop_bridge_missing(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.__requestLog = [];
            window.fetch = async function(url) {
                window.__requestLog.push(String(url));
                throw new Error('backend autostart API should not be called when desktop bridge is missing');
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    result = mock_page.evaluate(
        """
        async () => {
            const status = await window.nekoAutostartProvider.getStatus();
            const enabled = await window.nekoAutostartProvider.enable();
            const disabled = await window.nekoAutostartProvider.disable();
            const cached = window.nekoAutostartProvider.getCachedStatus();
            return {
                status,
                enabled,
                disabled,
                cached,
                requestLog: window.__requestLog,
            };
        }
        """
    )

    assert result["status"]["provider"] == "backend"
    assert result["status"]["supported"] is False
    assert result["status"]["enabled"] is False
    assert result["status"]["authoritative"] is True
    assert result["status"]["reason"] == "backend_autostart_removed"
    assert result["enabled"]["provider"] == "backend"
    assert result["enabled"]["ok"] is False
    assert result["enabled"]["supported"] is False
    assert result["enabled"]["enabled"] is False
    assert result["enabled"]["error_code"] == "launch_command_unavailable"
    assert result["disabled"]["provider"] == "backend"
    assert result["disabled"]["enabled"] is False
    assert result["disabled"]["ok"] is True
    assert result["cached"]["provider"] == "backend"
    assert result["cached"]["enabled"] is False
    assert result["requestLog"] == []


@pytest.mark.frontend
def test_autostart_provider_prefers_desktop_bridge_over_backend_fallback(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.__requestLog = [];
            window.nekoAutostart = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                enable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: true,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                disable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
            };
            window.fetch = async function(url) {
                window.__requestLog.push(String(url));
                throw new Error('backend fallback should not be called when desktop bridge exists');
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    result = mock_page.evaluate(
        """
        async () => {
            const status = await window.nekoAutostartProvider.getStatus();
            const enabled = await window.nekoAutostartProvider.enable();
            const disabled = await window.nekoAutostartProvider.disable();
            const cached = window.nekoAutostartProvider.getCachedStatus();
            return {
                status,
                enabled,
                disabled,
                cached,
                requestLog: window.__requestLog,
            };
        }
        """
    )

    assert result["status"]["provider"] == "neko-pc"
    assert result["enabled"]["enabled"] is True
    assert result["disabled"]["enabled"] is False
    assert result["cached"]["provider"] == "neko-pc"
    assert result["cached"]["enabled"] is False
    assert result["requestLog"] == []


@pytest.mark.frontend
def test_autostart_provider_desktop_status_event_uses_desktop_defaults_without_provider(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.nekoAutostart = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                enable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: true,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
                disable: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        platform: 'windows',
                        mechanism: 'electron-login-item',
                    };
                },
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    result = mock_page.evaluate(
        """
        async () => {
            await window.nekoAutostartProvider.getStatus();
            window.dispatchEvent(new CustomEvent('neko:autostart-status-changed', {
                detail: {
                    ok: true,
                    enabled: true,
                    authoritative: true,
                },
            }));
            return window.nekoAutostartProvider.getCachedStatus();
        }
        """
    )

    assert result["ok"] is True
    assert result["supported"] is True
    assert result["enabled"] is True
    assert result["authoritative"] is True
    assert result["provider"] == "neko-pc"
    assert result["mechanism"] == "desktop-bridge"


@pytest.mark.frontend
def test_mutation_requests_refresh_csrf_token_once_after_validation_failure(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.pageConfigReady = Promise.resolve({
                success: true,
                autostart_csrf_token: 'stale-token',
            });
            window.__pageConfigFetchCount = 0;
            window.__mutationTokens = [];
            window.__tutorialHeartbeatBodies = [];
            window.__autostartHeartbeatBodies = [];
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body, status) {
                return new Response(JSON.stringify(body), {
                    status: status || 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                const method = String(requestOptions.method || 'GET').toUpperCase();
                const headers = requestOptions.headers || {};
                const csrfToken = headers['X-CSRF-Token'] || headers['x-csrf-token'] || '';
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (method !== 'GET' && method !== 'HEAD') {
                    window.__mutationTokens.push(csrfToken);
                }

                if (requestUrl === '/api/config/page_config') {
                    window.__pageConfigFetchCount += 1;
                    return jsonResponse({
                        success: true,
                        lanlan_name: 'LanLan',
                        master_name: '',
                        master_profile_name: '',
                        master_nickname: '',
                        master_display_name: '',
                        autostart_csrf_token: 'fresh-token',
                        model_path: '',
                        model_type: 'live2d',
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    if (csrfToken !== 'fresh-token') {
                        return jsonResponse({
                            ok: false,
                            error_code: 'csrf_validation_failed',
                            error: 'Request could not be verified',
                        }, 403);
                    }
                    window.__tutorialHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    if (csrfToken !== 'fresh-token') {
                        return jsonResponse({
                            ok: false,
                            error_code: 'csrf_validation_failed',
                            error: 'Request could not be verified',
                        }, 403);
                    }
                    window.__autostartHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + method + ' ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-autostart-provider.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function(
        """
        () => (
            window.__pageConfigFetchCount === 1
            && window.__tutorialHeartbeatBodies.length === 1
            && window.__autostartHeartbeatBodies.length === 1
            && window.__mutationTokens.filter(function(token) {
                return token === 'stale-token';
            }).length >= 2
            && window.__mutationTokens.filter(function(token) {
                return token === 'fresh-token';
            }).length >= 2
        )
        """,
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            pageConfigFetchCount: window.__pageConfigFetchCount,
            mutationTokens: window.__mutationTokens.slice(),
            tutorialHeartbeatBodies: window.__tutorialHeartbeatBodies.slice(),
            autostartHeartbeatBodies: window.__autostartHeartbeatBodies.slice(),
        })
        """
    )

    assert result["pageConfigFetchCount"] == 1
    assert result["mutationTokens"].count("stale-token") >= 2
    assert result["mutationTokens"].count("fresh-token") >= 2
    assert len(result["tutorialHeartbeatBodies"]) == 1
    assert len(result["autostartHeartbeatBodies"]) == 1


@pytest.mark.frontend
def test_autostart_prompt_acceptance_tracks_pending_system_approval_without_failure(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.__toastMessages = [];
            window.showStatusToast = function(message) {
                window.__toastMessages.push(String(message));
            };
            window.__autostartDecisionBodies = [];
            window.nekoLocalMutationSecurity = {
                getMutationHeaders: async function() {
                    return { 'X-CSRF-Token': 'test-token' };
                },
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                    };
                },
                enable: async function() {
                    return {
                        ok: false,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        requires_approval: true,
                        error_code: 'autostart_requires_approval',
                    };
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body, status) {
                return new Response(JSON.stringify(body), {
                    status: status || 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: true,
                        prompt_reason: 'usage_timeout',
                        prompt_token: 'autostart-token',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/shown') {
                    return jsonResponse({
                        ok: true,
                        already_acknowledged: false,
                        state: {
                            status: 'prompted',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/decision') {
                    window.__autostartDecisionBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "common_dialogs.js"))
    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    expect(mock_page.locator(".modal-title")).to_have_text(
        "要不要让 N.E.K.O 开机自动启动？",
        timeout=5000,
    )
    mock_page.get_by_role("button", name="开启自启动").click()

    mock_page.wait_for_function(
        "() => window.__autostartDecisionBodies.length === 1 && window.__toastMessages.length === 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            decisionBody: window.__autostartDecisionBodies[0],
            toastMessages: window.__toastMessages.slice(),
        })
        """
    )

    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)
    assert result["decisionBody"]["decision"] == "accept"
    assert result["decisionBody"]["result"] == "approval_pending"
    assert result["decisionBody"]["autostart_provider"] == "neko-pc"
    assert result["toastMessages"] == ["需要先在系统设置里批准开机自启动，批准后会自动生效"]


@pytest.mark.frontend
def test_autostart_prompt_stays_suppressed_when_provider_reports_blocked_status(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__promptCalls = [];
            window.__requestLog = [];
            window.__autostartStatusCalls = 0;
            window.showDecisionPrompt = async function(options) {
                window.__promptCalls.push(String(options && options.title || ''));
                return null;
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    window.__autostartStatusCalls += 1;
                    return {
                        ok: false,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                        requires_approval: true,
                        service_not_found: false,
                    };
                },
                enable: async function() {
                    throw new Error('enable should not be called when status is blocked');
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body) {
                return new Response(JSON.stringify(body), {
                    status: 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                window.__requestLog.push(requestUrl);

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: true,
                        prompt_reason: 'usage_timeout',
                        prompt_token: 'autostart-token',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function(
        """
        () => (
            window.__autostartStatusCalls > 0
            && window.__requestLog.includes('/api/autostart-prompt/heartbeat')
        )
        """,
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            promptCalls: window.__promptCalls.slice(),
            requestLog: window.__requestLog.slice(),
            autostartStatusCalls: window.__autostartStatusCalls,
        })
        """
    )

    assert result["autostartStatusCalls"] > 0
    assert result["promptCalls"] == []
    assert "/api/autostart-prompt/heartbeat" in result["requestLog"]


@pytest.mark.frontend
def test_autostart_decision_failure_retries_without_reopening_prompt(
    mock_page: Page,
):
    project_root = Path(__file__).resolve().parents[2]

    mock_page.set_content("<!doctype html><html><body></body></html>")
    mock_page.evaluate(
        """
        () => {
            window.safeT = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.showStatusToast = function() {};
            window.__promptTitles = [];
            window.__autostartDecisionBodies = [];
            window.__autostartHeartbeatBodies = [];
            window.nekoLocalMutationSecurity = {
                getMutationHeaders: async function() {
                    return { 'X-CSRF-Token': 'test-token' };
                },
            };
            window.showDecisionPrompt = async function(options) {
                window.__promptTitles.push(String(options && options.title || ''));
                if (options && typeof options.onShown === 'function') {
                    await options.onShown();
                }
                return 'later';
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                    };
                },
                enable: async function() {
                    throw new Error('enable should not be called for later decision');
                },
            };
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                hasSeenTutorial: function() {
                    return true;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };

            const jsonResponse = function(body, status) {
                return new Response(JSON.stringify(body), {
                    status: status || 200,
                    headers: {
                        'Content-Type': 'application/json',
                    },
                });
            };

            window.fetch = async function(url, options) {
                const requestUrl = String(url);
                const requestOptions = options || {};
                let body = null;
                if (typeof requestOptions.body === 'string' && requestOptions.body) {
                    body = JSON.parse(requestOptions.body);
                }

                if (requestUrl === '/api/tutorial-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/state') {
                    return jsonResponse({
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/heartbeat') {
                    window.__autostartHeartbeatBodies.push(body);
                    return jsonResponse({
                        ok: true,
                        should_prompt: true,
                        prompt_reason: 'usage_timeout',
                        prompt_token: 'autostart-token',
                        state: {
                            status: 'observing',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/shown') {
                    return jsonResponse({
                        ok: true,
                        already_acknowledged: false,
                        state: {
                            status: 'prompted',
                            never_remind: false,
                            deferred_until: 0,
                            autostart_enabled: false,
                        },
                    });
                }
                if (requestUrl === '/api/autostart-prompt/decision') {
                    window.__autostartDecisionBodies.push(body);
                    if (window.__autostartDecisionBodies.length === 1) {
                        return jsonResponse({
                            ok: false,
                            error: 'temporary_failure',
                        }, 500);
                    }
                    return jsonResponse({
                        ok: true,
                        state: {
                            status: 'deferred',
                            never_remind: false,
                            deferred_until: Date.now() + 60000,
                            autostart_enabled: false,
                        },
                    });
                }

                throw new Error('Unexpected request: ' + requestUrl);
            };
        }
        """
    )

    mock_page.add_script_tag(path=str(project_root / "static" / "app-tutorial-prompt.js"))
    mock_page.evaluate("() => window.appTutorialPrompt.init()")

    mock_page.wait_for_function(
        """
        () => (
            window.__autostartDecisionBodies.length === 2
            && window.__autostartHeartbeatBodies.length >= 2
        )
        """,
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            promptTitles: window.__promptTitles.slice(),
            decisionBodies: window.__autostartDecisionBodies.slice(),
            heartbeatBodies: window.__autostartHeartbeatBodies.slice(),
        })
        """
    )

    assert result["promptTitles"] == ["要不要让 N.E.K.O 开机自动启动？"]
    assert len(result["decisionBodies"]) == 2
    assert result["decisionBodies"][0]["decision"] == "later"
    assert result["decisionBodies"][1]["decision"] == "later"
    assert len(result["heartbeatBodies"]) >= 2
