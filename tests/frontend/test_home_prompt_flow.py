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
