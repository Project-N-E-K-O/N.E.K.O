import re
from pathlib import Path

import pytest


playwright_sync_api = pytest.importorskip("playwright.sync_api")
Page = playwright_sync_api.Page
expect = playwright_sync_api.expect

PROJECT_ROOT = Path(__file__).resolve().parents[2]
_UNIVERSAL_TUTORIAL_DEPENDENCIES = (
    "tutorial/core/skip-controller.js",
    "tutorial/avatar/reload-controller.js",
    "tutorial/core/round-prelude-controller.js",
    "tutorial/core/lifecycle-state-store.js",
)
_YUI_OVERLAY_DEPENDENCIES = (
    "tutorial/visual/overlay-renderer.js",
)
_YUI_DIRECTOR_DEPENDENCIES = (
    "tutorial/visual/overlay-renderer.js",
    "tutorial/yui-guide/overlay.js",
    "tutorial/core/interaction-takeover.js",
    "tutorial/avatar/yui-standin.js",
    "tutorial/avatar/standin-controller.js",
    "tutorial/visual/spotlight-controller.js",
    "tutorial/visual/ghost-cursor-controller.js",
    "tutorial/visual/petal-transition-controller.js",
    "tutorial/visual/highlight-controller.js",
    "tutorial/visual/controllers.js",
    "tutorial/visual/resistance-controllers.js",
    "tutorial/core/operation-registry.js",
    "tutorial/core/settings-tour-flow.js",
    "tutorial/core/command-registry.js",
    "tutorial/core/script-normalizer.js",
    "tutorial/core/timeline-engine.js",
    "tutorial/core/visual-runtime.js",
    "tutorial/core/scene-orchestrator.js",
)
_YUI_DAILY_GUIDE_PREFIX = "tutorial/yui-guide/days/day"
_PAGE_BOOTSTRAP_TEMPLATE = """
() => {
    window.safeT = function(key, fallback) {
        return typeof fallback === 'string' ? fallback : key;
    };
    window.showStatusToast = function() {};
    window.pageConfigReady = Promise.resolve({
        success: true,
        autostart_csrf_token: 'test-token',
    });
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

    const jsonResponse = function(body, status) {
        return new Response(JSON.stringify(body), {
            status: status || 200,
            headers: {
                'Content-Type': 'application/json',
            },
        });
    };

__SETUP_JS__

    window.fetch = async function(url, options) {
        const requestUrl = String(url);
        const requestOptions = options || {};
        const method = String(requestOptions.method || 'GET').toUpperCase();
        const headers = requestOptions.headers || {};
        let body = null;
        if (typeof requestOptions.body === 'string' && requestOptions.body) {
            body = JSON.parse(requestOptions.body);
        }

__FETCH_JS__

        throw new Error('Unexpected request: ' + method + ' ' + requestUrl);
    };
}
"""


def _expand_script_dependencies(script_names: tuple[str, ...]) -> tuple[str, ...]:
    expanded = []
    for script_name in script_names:
        if script_name == "tutorial/yui-guide/common.js" and "tutorial/core/guide-helpers.js" not in expanded:
            expanded.append("tutorial/core/guide-helpers.js")
        if script_name == "tutorial/yui-guide/common.js" and "tutorial/core/scoped-resources.js" not in expanded:
            expanded.append("tutorial/core/scoped-resources.js")
        if script_name == "tutorial/yui-guide/common.js" and "tutorial/core/bridge-command-bus.js" not in expanded:
            expanded.append("tutorial/core/bridge-command-bus.js")
        if script_name == "tutorial/yui-guide/common.js" and "tutorial/core/target-geometry-registry.js" not in expanded:
            expanded.append("tutorial/core/target-geometry-registry.js")
        if script_name == "tutorial/yui-guide/common.js" and "tutorial/core/chat-window-adapter.js" not in expanded:
            expanded.append("tutorial/core/chat-window-adapter.js")
        if script_name.startswith(_YUI_DAILY_GUIDE_PREFIX) and "tutorial/core/guide-helpers.js" not in expanded:
            expanded.append("tutorial/core/guide-helpers.js")
        if script_name.startswith(_YUI_DAILY_GUIDE_PREFIX) and "tutorial/core/scoped-resources.js" not in expanded:
            expanded.append("tutorial/core/scoped-resources.js")
        if script_name.startswith(_YUI_DAILY_GUIDE_PREFIX) and "tutorial/core/bridge-command-bus.js" not in expanded:
            expanded.append("tutorial/core/bridge-command-bus.js")
        if script_name.startswith(_YUI_DAILY_GUIDE_PREFIX) and "tutorial/core/target-geometry-registry.js" not in expanded:
            expanded.append("tutorial/core/target-geometry-registry.js")
        if script_name.startswith(_YUI_DAILY_GUIDE_PREFIX) and "tutorial/core/chat-window-adapter.js" not in expanded:
            expanded.append("tutorial/core/chat-window-adapter.js")
        if script_name.startswith(_YUI_DAILY_GUIDE_PREFIX) and "tutorial/yui-guide/common.js" not in expanded:
            expanded.append("tutorial/yui-guide/common.js")
        if script_name == "tutorial/yui-guide/overlay.js":
            for dependency in _YUI_OVERLAY_DEPENDENCIES:
                if dependency not in expanded:
                    expanded.append(dependency)
        if script_name == "tutorial/yui-guide/director.js":
            for dependency in _YUI_DIRECTOR_DEPENDENCIES:
                if dependency not in expanded:
                    expanded.append(dependency)
        if script_name == "tutorial/core/app-prompt.js" and "tutorial/core/lifecycle-state-store.js" not in expanded:
            expanded.append("tutorial/core/lifecycle-state-store.js")
        if script_name == "tutorial/core/universal-manager.js":
            for dependency in _UNIVERSAL_TUTORIAL_DEPENDENCIES:
                if dependency not in expanded:
                    expanded.append(dependency)
        if script_name not in expanded:
            expanded.append(script_name)
    return tuple(expanded)


def _bootstrap_page(
    mock_page: Page,
    *,
    setup_js: str = "",
    fetch_js: str = "",
    script_names: tuple[str, ...] = (),
    init_js: str | None = None,
) -> None:
    mock_page.route(
        "**/home-prompt-harness",
        lambda route: route.fulfill(
            status=200,
            content_type="text/html",
            body="<!doctype html><html><body></body></html>",
        ),
    )
    mock_page.goto("http://neko.test/home-prompt-harness")
    mock_page.evaluate(
        _PAGE_BOOTSTRAP_TEMPLATE
        .replace("__SETUP_JS__", setup_js.strip())
        .replace("__FETCH_JS__", fetch_js.strip())
    )
    for script_name in _expand_script_dependencies(script_names):
        mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / script_name))
    if init_js:
        mock_page.evaluate(init_js)


def _bootstrap_tutorial_prompt_page(
    mock_page: Page,
    *,
    setup_js: str = "",
    fetch_js: str = "",
    include_common_dialogs: bool = False,
    include_autostart_provider: bool = False,
    include_autostart_prompt: bool = False,
) -> None:
    script_names = []
    if include_common_dialogs:
        script_names.append("common_dialogs.js")
    if include_autostart_provider:
        setup_js = setup_js + "\nwindow.nekoAutostartProvider = undefined;"
        script_names.append("app-autostart-provider.js")
    script_names.append("app-prompt-shared.js")
    script_names.append("tutorial/core/app-prompt.js")
    if include_autostart_prompt or include_autostart_provider:
        script_names.append("app-autostart-prompt.js")
    _bootstrap_page(
        mock_page,
        setup_js=setup_js,
        fetch_js=fetch_js,
        script_names=tuple(script_names),
        init_js="""
            () => {
                window.appTutorialPrompt.init();
                if (window.appAutostartPrompt) {
                    window.appAutostartPrompt.init();
                }
            }
        """,
    )


def _bootstrap_autostart_provider_page(
    mock_page: Page,
    *,
    setup_js: str = "",
    fetch_js: str = "",
) -> None:
    _bootstrap_page(
        mock_page,
        setup_js=setup_js,
        fetch_js=fetch_js,
        script_names=("app-autostart-provider.js",),
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


@pytest.mark.frontend
def test_yui_intro_activation_targets_compact_chat_input_shell_without_click_whitelist(mock_page: Page):
    _bootstrap_page(
        mock_page,
        script_names=("tutorial/yui-guide/director.js",),
        init_js="""
            () => {
                document.body.innerHTML = `
                    <div id="react-chat-window-root">
                        <div class="composer-panel">
                            <div class="composer-input-shell">
                                <textarea class="composer-input"></textarea>
                            </div>
                        </div>
                    </div>
                `;

                const buildDirector = function(label) {
                    const director = window.createYuiGuideDirector({ page: 'home' });
                    director.awaitingIntroActivation = true;
                    director._introActivationResolve = function() {
                        window.__activationResults[label].resolved = true;
                    };
                    return director;
                };

                window.__activationResults = {
                    shell: { resolved: false },
                    panel: { resolved: false },
                };

                const shellDirector = buildDirector('shell');
                const shell = document.querySelector('#react-chat-window-root .composer-input-shell');
                window.__activationResults.shell.target =
                    shellDirector.isIntroActivationTarget(shell);
                window.__activationResults.shell.hasClickWhitelist =
                    typeof shellDirector.isAllowedTutorialInteractionTarget === 'function';

                const panelDirector = buildDirector('panel');
                const panel = document.querySelector('#react-chat-window-root .composer-panel');
                window.__activationResults.panel.target =
                    panelDirector.isIntroActivationTarget(panel);
                window.__activationResults.panel.hasClickWhitelist =
                    typeof panelDirector.isAllowedTutorialInteractionTarget === 'function';
            }
        """,
    )

    result = mock_page.evaluate("window.__activationResults")

    assert result == {
        "shell": {"resolved": False, "target": True, "hasClickWhitelist": False},
        "panel": {"resolved": False, "target": True, "hasClickWhitelist": False},
    }


pytestmark = pytest.mark.skipif(
    not _has_playwright_browser(),
    reason="requires Playwright browser binaries",
)


@pytest.fixture(scope="session", autouse=True)
def mock_memory_server():
    """This browser-only prompt test does not need the repo-level mock memory server."""
    yield


@pytest.mark.frontend
def test_changelog_notice_preserves_leading_list_item(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.appState = { dom: {} };
            window.appConst = {};
        """,
        script_names=("app-ui.js",),
    )

    mock_page.evaluate(
        """
        () => {
            window.showProminentNotice({
                kind: 'changelog',
                version: '0.8.2',
                title: '更新内容',
                message: '- **新增**：第一条更新\\n- **修复**：第二条更新',
            });
        }
        """
    )

    items = mock_page.locator(".prominent-notice-changelog-item")
    expect(items).to_have_count(2)
    expect(items.nth(0)).to_contain_text("新增")
    expect(items.nth(0)).to_contain_text("第一条更新")
    expect(items.nth(1)).to_contain_text("第二条更新")


@pytest.mark.frontend
def test_home_prompt_queue_serializes_tutorial_and_autostart_prompts(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        include_autostart_prompt=True,
        setup_js="""
            window.__requestLog = [];
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
        """,
        fetch_js="""
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
        """,
    )

    tutorial_title = mock_page.locator(".modal-title")
    expect(tutorial_title).to_have_text("要不要开始主页新手引导？", timeout=5000)
    expect(mock_page.locator(".modal-overlay")).to_have_count(1)

    mock_page.get_by_role("button", name="开始引导").click()

    expect(tutorial_title).to_have_text("要不要让 N.E.K.O. 开机自动启动？", timeout=5000)
    expect(mock_page.locator(".modal-overlay")).to_have_count(1)
    expect(mock_page.locator(".modal-dialog-autostart-retention")).to_have_count(1)
    expect(mock_page.locator(".exit-retention-cat-character")).to_have_count(1)
    expect(mock_page.locator(".exit-retention-cat-head-group")).to_have_count(1)
    expect(mock_page.locator(".exit-retention-cat-mouth")).to_have_count(1)
    expect(mock_page.locator(".exit-retention-cat-paw")).to_have_count(2)

    dialog = mock_page.locator(".modal-dialog-autostart-retention")
    mock_page.locator(".modal-body").hover()
    expect(dialog).to_have_class(re.compile(r"\bstate-curious\b"))
    mock_page.get_by_role("button", name="开启自启动").hover()
    expect(dialog).to_have_class(re.compile(r"\bstate-happy\b"))
    mock_page.get_by_role("button", name="以后提醒").hover()
    expect(dialog).to_have_class(re.compile(r"\bstate-sad\b"))

    mock_page.get_by_role("button", name="以后提醒").click()
    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)

    request_log = mock_page.evaluate("() => window.__requestLog")
    requested_urls = [entry["url"] for entry in request_log]

    assert "/api/tutorial-prompt/heartbeat" in requested_urls
    assert "/api/tutorial-prompt/tutorial-started" in requested_urls
    assert "/api/autostart-prompt/heartbeat" in requested_urls
    assert "/api/autostart-prompt/decision" in requested_urls


@pytest.mark.frontend
def test_autostart_prompt_offers_never_after_backend_allows_it(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        include_autostart_prompt=True,
        setup_js="""
            window.__requestLog = [];
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
        """,
        fetch_js="""
            window.__requestLog.push({
                url: requestUrl,
                method: method,
                body: body,
            });

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
                    prompt_reason: 'tutorial_completed',
                    prompt_token: null,
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
                        can_never_remind: true,
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
                        can_never_remind: true,
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
                        can_never_remind: true,
                    },
                });
            }
            if (requestUrl === '/api/autostart-prompt/decision') {
                return jsonResponse({
                    ok: true,
                    state: {
                        status: body && body.decision === 'never' ? 'never' : 'deferred',
                        never_remind: body && body.decision === 'never',
                        deferred_until: 0,
                        autostart_enabled: false,
                        can_never_remind: true,
                    },
                });
            }
        """,
    )

    expect(mock_page.locator(".modal-dialog-autostart-retention")).to_have_count(1, timeout=5000)
    expect(mock_page.get_by_role("button", name="不再提示")).to_be_visible()
    expect(mock_page.get_by_role("button", name="以后提醒")).to_be_visible()
    expect(mock_page.get_by_role("button", name="开启自启动")).to_be_visible()
    button_texts = mock_page.locator(".modal-dialog-autostart-retention .modal-btn").all_text_contents()
    assert button_texts == ["以后提醒", "开启自启动", "不再提示"]

    mock_page.get_by_role("button", name="不再提示").click()
    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)

    request_log = mock_page.evaluate("() => window.__requestLog")
    autostart_decisions = [
        entry for entry in request_log
        if entry["url"] == "/api/autostart-prompt/decision"
    ]

    assert autostart_decisions
    assert autostart_decisions[-1]["body"]["decision"] == "never"


@pytest.mark.frontend
def test_home_prompt_later_locally_suppresses_repeat_before_autostart_prompt(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        include_autostart_prompt=True,
        setup_js="""
            window.__requestLog = [];
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
        """,
        fetch_js="""
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
        """,
    )

    prompt_title = mock_page.locator(".modal-title")
    expect(prompt_title).to_have_text("要不要开始主页新手引导？", timeout=5000)

    mock_page.get_by_role("button", name="稍后再说").click()

    expect(prompt_title).to_have_text("要不要让 N.E.K.O. 开机自动启动？", timeout=5000)
    assert mock_page.evaluate("window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart()") is True


@pytest.mark.frontend
def test_completed_home_tutorial_server_state_marks_versioned_home_storage_key_seen(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                getStorageKeysForPage: function(page) {
                    return page === 'home' ? ['neko_tutorial_home_yui_v1'] : [];
                },
                hasSeenTutorial: function() {
                    return false;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/state') {
                return jsonResponse({
                    state: {
                        status: 'completed',
                        completed_at: 1234,
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
                    prompt_reason: 'completed',
                    state: {
                        status: 'completed',
                        completed_at: 1234,
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: true,
                        home_tutorial_completed: true,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function(
        "() => localStorage.getItem('neko_tutorial_home_yui_v1') === 'true'"
    )

    assert mock_page.evaluate(
        """
        () => ({
            preferred: localStorage.getItem('neko_tutorial_home_yui_v1'),
        })
        """
    ) == {
        "preferred": "true",
    }


@pytest.mark.frontend
def test_legacy_home_tutorial_storage_key_is_ignored(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        setup_js="""
            window.__heartbeatBodies = [];
            window.localStorage.setItem('neko_tutorial_home', 'true');
            window.universalTutorialManager = {
                currentPage: 'home',
                isTutorialRunning: false,
                getStorageKeysForPage: function(page) {
                    return page === 'home' ? ['neko_tutorial_home_yui_v1'] : [];
                },
                getStorageKey: function() {
                    return 'neko_tutorial_home_yui_v1';
                },
                hasSeenTutorial: function() {
                    return false;
                },
                logPromptFlow: function() {},
                requestTutorialStart: async function() {
                    return false;
                },
            };
        """,
        fetch_js="""
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
                window.__heartbeatBodies.push(body);
                return jsonResponse({
                    ok: true,
                    should_prompt: true,
                    prompt_reason: 'idle_timeout',
                    prompt_token: 'legacy-ignored-token',
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function("() => window.__heartbeatBodies.length > 0")

    assert mock_page.evaluate("() => window.__heartbeatBodies[0].home_tutorial_completed") is False
    expect(mock_page.locator(".modal-overlay")).to_be_visible()


@pytest.mark.frontend
def test_tutorial_prompt_prefers_window_t_over_safe_t(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        setup_js="""
            window.t = function(key, fallback) {
                return typeof fallback === 'string' ? fallback : key;
            };
            window.safeT = function(key) {
                return key;
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
        """,
        fetch_js="""
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
        """,
    )

    expect(mock_page.locator(".modal-title")).to_have_text("要不要开始主页新手引导？", timeout=5000)


@pytest.mark.frontend
def test_tutorial_started_event_retries_failed_sync_on_heartbeat(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__tutorialStartedBodies = [];
            window.__tutorialCompletedBodies = [];
            window.__tutorialHeartbeatBodies = [];
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
        """,
        fetch_js="""
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
        """,
    )

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
def test_home_tutorial_skip_persists_completion_state(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__tutorialStartedBodies = [];
            window.__tutorialCompletedBodies = [];
            window.getTutorialStorageKeyForPage = function(page) {
                return page === 'home' ? 'neko_tutorial_home_yui_v1' : 'neko_tutorial_' + page;
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
        """,
        fetch_js="""
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
            if (requestUrl === '/api/tutorial-prompt/tutorial-started') {
                window.__tutorialStartedBodies.push(body);
                return jsonResponse({
                    ok: true,
                    tutorial_run_token: 'skip-run-token',
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
        """,
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
        "() => window.__tutorialStartedBodies.length === 1",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
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
            completedBodies: window.__tutorialCompletedBodies.slice(),
            preferredSeen: window.localStorage.getItem('neko_tutorial_home_yui_v1'),
        })
        """
    )

    assert result["completedBodies"][0]["source"] == "manual"
    assert result["completedBodies"][0]["tutorial_run_token"] == "skip-run-token"
    assert result["preferredSeen"] == "true"


@pytest.mark.frontend
def test_home_tutorial_reset_refreshes_stale_csrf_token_once(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.pageConfigReady = Promise.resolve({
                success: true,
                autostart_csrf_token: 'stale-token',
            });
            window.__pageConfigFetchCount = 0;
            window.__resetTokens = [];
            window.__resetBodies = [];
            window.alert = function(message) {
                window.__lastAlert = String(message || '');
            };
        """,
        fetch_js="""
            const csrfToken = headers['X-CSRF-Token'] || headers['x-csrf-token'] || '';
            if (requestUrl === '/api/config/page_config') {
                window.__pageConfigFetchCount += 1;
                return jsonResponse({
                    success: true,
                    autostart_csrf_token: 'fresh-token',
                    model_path: '',
                    model_type: 'live2d',
                });
            }
            if (requestUrl === '/api/tutorial-prompt/reset') {
                window.__resetTokens.push(csrfToken);
                window.__resetBodies.push(body);
                if (csrfToken !== 'fresh-token') {
                    return jsonResponse({
                        ok: false,
                        error_code: 'csrf_validation_failed',
                    }, 403);
                }
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/universal-manager.js"),
    )

    mock_page.evaluate(
        """
        async () => {
            localStorage.setItem('neko_tutorial_home_yui_v1', 'true');
            await resetTutorialForPage('home');
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            pageConfigFetchCount: window.__pageConfigFetchCount,
            resetTokens: window.__resetTokens.slice(),
            resetBodies: window.__resetBodies.slice(),
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            manualIntent: localStorage.getItem('neko_tutorial_home_yui_v1_manual_intent'),
        })
        """
    )

    assert result["pageConfigFetchCount"] >= 1
    assert result["resetTokens"] == ["stale-token", "fresh-token"]
    assert result["resetBodies"][0]["reason"] == "manual_home_tutorial_reset"
    assert result["resetBodies"][1]["reason"] == "manual_home_tutorial_reset"
    assert result["versionedSeen"] is None
    assert result["manualIntent"] == "true"


@pytest.mark.frontend
def test_home_tutorial_reset_without_manager_clears_versioned_home_key(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.pageConfigReady = Promise.resolve({
                success: true,
                autostart_csrf_token: 'test-token',
            });
            window.alert = function(message) {
                window.__lastAlert = String(message || '');
            };
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/reset') {
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/universal-manager.js"),
    )

    mock_page.evaluate(
        """
        async () => {
            window.universalTutorialManager = null;
            localStorage.setItem('neko_tutorial_home_yui_v1', 'true');
            await resetTutorialForPage('home');
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            manualIntent: localStorage.getItem('neko_tutorial_home_yui_v1_manual_intent'),
        })
        """
    )

    assert result["versionedSeen"] is None
    assert result["manualIntent"] == "true"


@pytest.mark.frontend
def test_home_tutorial_reset_still_clears_state_without_custom_event(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.pageConfigReady = Promise.resolve({
                success: true,
                autostart_csrf_token: 'test-token',
            });
            Object.defineProperty(window, 'CustomEvent', {
                configurable: true,
                value: undefined,
            });
            window.alert = function(message) {
                window.__lastAlert = String(message || '');
            };
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/reset') {
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/universal-manager.js"),
    )

    mock_page.evaluate(
        """
        async () => {
            localStorage.setItem('neko_tutorial_home_yui_v1', 'true');
            await resetTutorialForPage('home');
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            manualIntent: localStorage.getItem('neko_tutorial_home_yui_v1_manual_intent'),
        })
        """
    )

    assert result["versionedSeen"] is None
    assert result["manualIntent"] == "true"


@pytest.mark.frontend
def test_home_tutorial_reset_event_prevents_stale_completion_heartbeat(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__heartbeatBodies = [];
            Object.defineProperty(navigator, 'sendBeacon', {
                configurable: true,
                value: null,
            });
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/state') {
                return jsonResponse({
                    state: {
                        status: 'completed',
                        completed_at: 1234,
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: true,
                        home_tutorial_completed: true,
                    },
                });
            }
            if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                window.__heartbeatBodies.push(body);
                return jsonResponse({
                    ok: true,
                    should_prompt: false,
                    prompt_reason: '',
                    prompt_token: null,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function(
        """
        () => localStorage.getItem('neko_tutorial_home_yui_v1') === 'true'
        """,
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
            window.dispatchEvent(new Event('beforeunload'));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__heartbeatBodies.length >= 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            latestHeartbeat: window.__heartbeatBodies[window.__heartbeatBodies.length - 1],
        })
        """
    )

    assert result["versionedSeen"] is None
    assert result["latestHeartbeat"]["home_tutorial_completed"] is False
    assert result["latestHeartbeat"]["manual_home_tutorial_viewed"] is False


@pytest.mark.frontend
def test_home_tutorial_reset_event_re_resets_after_inflight_completed_heartbeat(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__heartbeatBodies = [];
            window.__resetBodies = [];
            window.__resolveHeartbeat = null;
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/state') {
                return jsonResponse({
                    state: {
                        status: 'completed',
                        completed_at: 1234,
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: true,
                        home_tutorial_completed: true,
                    },
                });
            }
            if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                window.__heartbeatBodies.push(body);
                return new Promise((resolve) => {
                    window.__resolveHeartbeat = () => resolve(jsonResponse({
                        ok: true,
                        should_prompt: false,
                        prompt_reason: '',
                        prompt_token: null,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    }));
                });
            }
            if (requestUrl === '/api/tutorial-prompt/reset') {
                window.__resetBodies.push(body);
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function(
        "() => window.__heartbeatBodies.length >= 1 && typeof window.__resolveHeartbeat === 'function'",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
            window.__resolveHeartbeat();
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__resetBodies.length >= 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            staleHeartbeat: window.__heartbeatBodies[0],
            resetBodies: window.__resetBodies.slice(),
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            suppressAutoStart: window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart(),
        })
        """
    )

    assert result["staleHeartbeat"]["home_tutorial_completed"] is True
    assert result["staleHeartbeat"]["manual_home_tutorial_viewed"] is True
    assert result["resetBodies"][0]["reason"] == "manual_home_tutorial_reset"
    assert result["versionedSeen"] is None
    assert result["suppressAutoStart"] is False


@pytest.mark.frontend
def test_home_tutorial_reset_event_re_resets_after_inflight_completion_lifecycle(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__startedBodies = [];
            window.__completedBodies = [];
            window.__resetBodies = [];
            window.__resolveCompletion = null;
        """,
        fetch_js="""
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
                    prompt_reason: '',
                    prompt_token: null,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
            if (requestUrl === '/api/tutorial-prompt/tutorial-started') {
                window.__startedBodies.push(body);
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
                window.__completedBodies.push(body);
                return new Promise((resolve) => {
                    window.__resolveCompletion = () => resolve(jsonResponse({
                        ok: true,
                        state: {
                            status: 'completed',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: true,
                        },
                    }));
                });
            }
            if (requestUrl === '/api/tutorial-prompt/reset') {
                window.__resetBodies.push(body);
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: { page: 'home', source: 'manual' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__startedBodies.length === 1",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: { page: 'home', source: 'manual' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__completedBodies.length === 1 && typeof window.__resolveCompletion === 'function'",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
            window.__resolveCompletion();
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__resetBodies.length >= 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            completedBodies: window.__completedBodies.slice(),
            resetBodies: window.__resetBodies.slice(),
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            suppressAutoStart: window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart(),
        })
        """
    )

    assert result["completedBodies"][0]["tutorial_run_token"] == "tutorial-run-token"
    assert result["resetBodies"][0]["reason"] == "manual_home_tutorial_reset"
    assert result["versionedSeen"] is None
    assert result["suppressAutoStart"] is False


@pytest.mark.frontend
def test_home_tutorial_reset_event_re_resets_after_inflight_started_lifecycle(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__startedBodies = [];
            window.__resetBodies = [];
            window.__resolveStarted = null;
        """,
        fetch_js="""
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
                    prompt_reason: '',
                    prompt_token: null,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
            if (requestUrl === '/api/tutorial-prompt/tutorial-started') {
                window.__startedBodies.push(body);
                return new Promise((resolve) => {
                    window.__resolveStarted = () => resolve(jsonResponse({
                        ok: true,
                        tutorial_run_token: 'stale-start-token',
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    }));
                });
            }
            if (requestUrl === '/api/tutorial-prompt/reset') {
                window.__resetBodies.push(body);
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: { page: 'home', source: 'manual' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__startedBodies.length === 1 && typeof window.__resolveStarted === 'function'",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
            window.__resolveStarted();
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__resetBodies.length >= 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            startedBodies: window.__startedBodies.slice(),
            resetBodies: window.__resetBodies.slice(),
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            suppressAutoStart: window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart(),
        })
        """
    )

    assert result["startedBodies"][0]["source"] == "manual"
    assert result["resetBodies"][0]["reason"] == "manual_home_tutorial_reset"
    assert result["versionedSeen"] is None
    assert result["suppressAutoStart"] is False


@pytest.mark.frontend
def test_home_tutorial_reset_event_ignores_stale_initial_state_response(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__resolveInitialTutorialState = null;
            window.__initialTutorialStateResolved = false;
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/state') {
                return new Promise((resolve) => {
                    window.__resolveInitialTutorialState = () => {
                        window.__initialTutorialStateResolved = true;
                        resolve(jsonResponse({
                            state: {
                                status: 'completed',
                                never_remind: false,
                                deferred_until: 0,
                                manual_home_tutorial_viewed: true,
                                home_tutorial_completed: true,
                            },
                        }));
                    };
                });
            }
            if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                return jsonResponse({
                    ok: true,
                    should_prompt: false,
                    prompt_reason: '',
                    prompt_token: null,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function(
        "() => typeof window.__resolveInitialTutorialState === 'function'",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
            window.__resolveInitialTutorialState();
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__initialTutorialStateResolved === true",
        timeout=5000,
    )
    mock_page.wait_for_timeout(100)

    assert mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            suppressAutoStart: window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart(),
        })
        """
    ) == {
        "versionedSeen": None,
        "suppressAutoStart": False,
    }


@pytest.mark.frontend
def test_home_tutorial_reset_event_clears_seen_prompt_token(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        setup_js="""
            window.__heartbeatCount = 0;
        """,
        fetch_js="""
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
                window.__heartbeatCount += 1;
                if (window.__heartbeatCount > 1) {
                    return jsonResponse({
                        ok: true,
                        should_prompt: false,
                        prompt_reason: '',
                        prompt_token: null,
                        state: {
                            status: 'started',
                            never_remind: false,
                            deferred_until: 0,
                            manual_home_tutorial_viewed: true,
                            home_tutorial_completed: false,
                        },
                    });
                }
                return jsonResponse({
                    ok: true,
                    should_prompt: true,
                    prompt_reason: 'idle_timeout',
                    prompt_token: 'repeat-token',
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
                        status: 'prompted',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    expect(mock_page.locator(".modal-title")).to_have_text("要不要开始主页新手引导？", timeout=5000)
    mock_page.get_by_role("button", name="稍后再说").click()
    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
        }
        """
    )

    mock_page.wait_for_function(
        "() => window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart() === false",
        timeout=5000,
    )


@pytest.mark.frontend
def test_home_tutorial_reset_event_ignores_open_prompt_decision(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        setup_js="""
            window.__decisionBodies = [];
        """,
        fetch_js="""
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
                    prompt_token: 'stale-open-token',
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
                window.__decisionBodies.push(body);
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'deferred',
                        never_remind: false,
                        deferred_until: Date.now() + 60000,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    expect(mock_page.locator(".modal-title")).to_have_text("要不要开始主页新手引导？", timeout=5000)
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:home-tutorial-reset', {
                detail: { page: 'home', source: 'manual_home_tutorial_reset' },
            }));
        }
        """
    )
    mock_page.get_by_role("button", name="稍后再说").click()
    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)

    result = mock_page.evaluate(
        """
        () => ({
            suppressAutoStart: window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart(),
            decisionBodies: window.__decisionBodies.slice(),
        })
        """
    )

    assert result["suppressAutoStart"] is False
    assert result["decisionBodies"] == []


@pytest.mark.frontend
def test_home_tutorial_reset_broadcast_channel_is_closed_on_unload(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__resetBroadcastChannels = [];
            window.BroadcastChannel = class {
                constructor(name) {
                    this.name = name;
                    this.closed = false;
                    this.listeners = {};
                    window.__resetBroadcastChannels.push(this);
                }
                addEventListener(type, listener) {
                    this.listeners[type] = listener;
                }
                close() {
                    this.closed = true;
                }
            };
        """,
        fetch_js="""
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
        """,
    )

    result = mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new Event('beforeunload'));
            return {
                count: window.__resetBroadcastChannels.length,
                closed: window.__resetBroadcastChannels[0] && window.__resetBroadcastChannels[0].closed,
            };
        }
        """
    )

    assert result == {
        "count": 1,
        "closed": True,
    }


@pytest.mark.frontend
def test_cross_window_home_tutorial_reset_event_prevents_stale_completion_heartbeat(mock_page: Page):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__heartbeatBodies = [];
            Object.defineProperty(navigator, 'sendBeacon', {
                configurable: true,
                value: null,
            });
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/state') {
                return jsonResponse({
                    state: {
                        status: 'completed',
                        completed_at: 1234,
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: true,
                        home_tutorial_completed: true,
                    },
                });
            }
            if (requestUrl === '/api/tutorial-prompt/heartbeat') {
                window.__heartbeatBodies.push(body);
                return jsonResponse({
                    ok: true,
                    should_prompt: false,
                    prompt_reason: '',
                    prompt_token: null,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function(
        "() => localStorage.getItem('neko_tutorial_home_yui_v1') === 'true'",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new StorageEvent('storage', {
                key: 'neko_home_tutorial_reset_event',
                newValue: JSON.stringify({
                    page: 'home',
                    source: 'manual_home_tutorial_reset',
                    nonce: 'from-memory-browser-window',
                }),
            }));
            window.dispatchEvent(new Event('beforeunload'));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__heartbeatBodies.length >= 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            latestHeartbeat: window.__heartbeatBodies[window.__heartbeatBodies.length - 1],
        })
        """
    )

    assert result["versionedSeen"] is None
    assert result["latestHeartbeat"]["home_tutorial_completed"] is False
    assert result["latestHeartbeat"]["manual_home_tutorial_viewed"] is False


@pytest.mark.frontend
def test_all_tutorial_reset_without_manager_clears_versioned_home_key(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.pageConfigReady = Promise.resolve({
                success: true,
                autostart_csrf_token: 'test-token',
            });
            window.alert = function(message) {
                window.__lastAlert = String(message || '');
            };
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/reset') {
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/universal-manager.js"),
    )

    mock_page.evaluate(
        """
        async () => {
            window.universalTutorialManager = null;
            localStorage.setItem('neko_tutorial_home_yui_v1', 'true');
            localStorage.setItem('neko_tutorial_model_manager_mmd', 'true');
            await resetAllTutorials();
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            modelManagerMmdSeen: localStorage.getItem('neko_tutorial_model_manager_mmd'),
            manualIntent: localStorage.getItem('neko_tutorial_home_yui_v1_manual_intent'),
        })
        """
    )

    assert result["versionedSeen"] is None
    assert result["modelManagerMmdSeen"] is None
    assert result["manualIntent"] == "true"


@pytest.mark.frontend
def test_home_tutorial_reset_with_manager_clears_versioned_home_key(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.pageConfigReady = Promise.resolve({
                success: true,
                autostart_csrf_token: 'test-token',
            });
            window.alert = function(message) {
                window.__lastAlert = String(message || '');
            };
        """,
        fetch_js="""
            if (requestUrl === '/api/tutorial-prompt/reset') {
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'observing',
                        never_remind: false,
                        deferred_until: 0,
                        manual_home_tutorial_viewed: false,
                        home_tutorial_completed: false,
                    },
                });
            }
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/universal-manager.js"),
    )

    mock_page.evaluate(
        """
        async () => {
            await initUniversalTutorialManager();
            window.universalTutorialManager.getYuiGuideVersionedPageKey = () => null;
            localStorage.setItem('neko_tutorial_home_yui_v1', 'true');
            await resetTutorialForPage('home');
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            versionedSeen: localStorage.getItem('neko_tutorial_home_yui_v1'),
            manualIntent: localStorage.getItem('neko_tutorial_home_yui_v1_manual_intent'),
        })
        """
    )

    assert result["versionedSeen"] is None
    assert result["manualIntent"] == "true"


@pytest.mark.frontend
def test_home_tutorial_skip_restores_temporarily_disabled_galgame_mode(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.localStorage.setItem('neko.reactChatWindow.galgameMode', 'true');
        """,
        script_names=("app/app-react-chat-window.js",),
    )

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: { page: 'home' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === false",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )


@pytest.mark.frontend
def test_home_tutorial_early_end_restores_temporarily_disabled_galgame_mode(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.localStorage.setItem('neko.reactChatWindow.galgameMode', 'true');
        """,
        script_names=("app/app-react-chat-window.js",),
    )

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: { page: 'home' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === false",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.dispatchEvent(new CustomEvent('neko:tutorial-ended-without-completion', {
                detail: { page: 'home', reason: 'page-changed' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )


@pytest.mark.frontend
def test_home_tutorial_input_lock_suppresses_galgame_options_without_tutorial_event(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.localStorage.setItem('neko.reactChatWindow.galgameMode', 'true');
        """,
        script_names=("app/app-react-chat-window.js",),
    )

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.reactChatWindowHost.setHomeTutorialInputLocked(true, 'avatar-floating-guide-day1');
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === false",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.reactChatWindowHost.setHomeTutorialInputLocked(false, 'avatar-floating-guide-day1-complete');
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )


@pytest.mark.frontend
def test_home_tutorial_feature_controller_restores_live_galgame_state_after_legacy_listener(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.localStorage.setItem('neko.reactChatWindow.galgameMode', 'false');
            window.__agentFlagBodies = [];
            window.__agentCommandBodies = [];
        """,
        fetch_js="""
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
            if (requestUrl === '/api/tutorial-prompt/tutorial-started') {
                return jsonResponse({ ok: true, tutorial_run_token: 'run-token' });
            }
            if (requestUrl === '/api/agent/flags' && method === 'GET') {
                return jsonResponse({
                    success: true,
                    analyzer_enabled: true,
                    agent_flags: {
                        computer_use_enabled: true,
                        browser_use_enabled: false,
                        user_plugin_enabled: false,
                        openclaw_enabled: false,
                        openfang_enabled: false,
                    },
                });
            }
            if (requestUrl === '/api/agent/flags' && method === 'POST') {
                window.__agentFlagBodies.push(body);
                return jsonResponse({ success: true });
            }
            if (requestUrl === '/api/agent/command' && method === 'POST') {
                window.__agentCommandBodies.push(body);
                return jsonResponse({ success: true });
            }
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/app-prompt.js"),
        init_js="() => window.appTutorialPrompt.init()",
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "app" / "app-react-chat-window.js"))

    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.reactChatWindowHost.isGalgameModeEnabled() === false",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.reactChatWindowHost.setGalgameModeEnabled(true, {
                persist: false,
                force: true,
            });
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === true",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = true;
            window.dispatchEvent(new CustomEvent('neko:tutorial-started', {
                detail: { page: 'home' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isGalgameModeEnabled() === false",
        timeout=5000,
    )
    mock_page.wait_for_function(
        "() => window.__agentCommandBodies.length === 1 && window.__agentFlagBodies.length === 1",
        timeout=5000,
    )

    mock_page.evaluate(
        """
        () => {
            window.universalTutorialManager.isTutorialRunning = false;
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: { page: 'home' },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        """
        () => window.reactChatWindowHost.isGalgameModeEnabled() === true
            && window.localStorage.getItem('neko.reactChatWindow.galgameMode') === 'false'
        """,
        timeout=5000,
    )
    mock_page.wait_for_function(
        "() => window.__agentCommandBodies.length === 2 && window.__agentFlagBodies.length === 2",
        timeout=5000,
    )
    result = mock_page.evaluate(
        """
        () => ({
            suppressed: window.NekoHomeTutorialFeatureController.isActive(),
            agentFlagBodies: window.__agentFlagBodies.slice(),
            agentCommandBodies: window.__agentCommandBodies.slice(),
        })
        """
    )
    assert result["suppressed"] is False
    assert result["agentCommandBodies"][0]["command"] == "set_agent_enabled"
    assert result["agentCommandBodies"][0]["enabled"] is False
    assert result["agentCommandBodies"][1]["command"] == "set_agent_enabled"
    assert result["agentCommandBodies"][1]["enabled"] is True
    assert "agent_enabled" not in result["agentFlagBodies"][0]["flags"]
    assert result["agentFlagBodies"][0]["flags"]["computer_use_enabled"] is False
    assert "agent_enabled" not in result["agentFlagBodies"][1]["flags"]
    assert result["agentFlagBodies"][1]["flags"]["computer_use_enabled"] is True


@pytest.mark.frontend
def test_home_tutorial_feature_controller_enforce_reapplies_suppression_after_chat_host_ready(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.localStorage.setItem('neko.reactChatWindow.galgameMode', 'false');
            window.appState = {
                proactiveChatEnabled: true,
                proactiveVisionEnabled: true,
                proactiveVisionChatEnabled: true,
                proactiveNewsChatEnabled: true,
                proactiveVideoChatEnabled: true,
                proactivePersonalChatEnabled: true,
                proactiveMusicEnabled: true,
                proactiveMemeEnabled: true,
                proactiveMiniGameInviteEnabled: true,
            };
            window.stopProactiveChatScheduleCalls = 0;
            window.stopProactiveVisionDuringSpeechCalls = 0;
            window.releaseProactiveVisionStreamCalls = 0;
            window.stopProactiveChatSchedule = () => { window.stopProactiveChatScheduleCalls += 1; };
            window.stopProactiveVisionDuringSpeech = () => { window.stopProactiveVisionDuringSpeechCalls += 1; };
            window.releaseProactiveVisionStream = () => { window.releaseProactiveVisionStreamCalls += 1; };
        """,
        script_names=("app-prompt-shared.js", "tutorial/core/app-prompt.js"),
    )

    mock_page.evaluate(
        """
        () => {
            window.NekoHomeTutorialFeatureController.begin('test-before-chat-host');
        }
        """
    )
    mock_page.add_script_tag(path=str(PROJECT_ROOT / "static" / "app" / "app-react-chat-window.js"))
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.reactChatWindowHost.isGalgameModeEnabled() === false",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            window.reactChatWindowHost.setGalgameModeEnabled(true, {
                persist: false,
                force: true,
            });
            window.proactiveChatEnabled = true;
            window.proactiveVisionEnabled = true;
            window.appState.proactiveChatEnabled = true;
            window.appState.proactiveVisionEnabled = true;
            window.NekoHomeTutorialFeatureController.enforce('test-surface-ready');
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            active: window.NekoHomeTutorialFeatureController.isActive(),
            galgame: window.reactChatWindowHost.isGalgameModeEnabled(),
            proactiveChatEnabled: window.proactiveChatEnabled,
            proactiveVisionEnabled: window.proactiveVisionEnabled,
            appStateProactiveChatEnabled: window.appState.proactiveChatEnabled,
            appStateProactiveVisionEnabled: window.appState.proactiveVisionEnabled,
            stoppedChat: window.stopProactiveChatScheduleCalls,
            stoppedVision: window.stopProactiveVisionDuringSpeechCalls,
            releasedVision: window.releaseProactiveVisionStreamCalls,
        })
        """
    )

    assert result["active"] is True
    assert result["galgame"] is False
    assert result["proactiveChatEnabled"] is False
    assert result["proactiveVisionEnabled"] is False
    assert result["appStateProactiveChatEnabled"] is False
    assert result["appStateProactiveVisionEnabled"] is False
    assert result["stoppedChat"] >= 2
    assert result["stoppedVision"] >= 2
    assert result["releasedVision"] >= 2


@pytest.mark.frontend
def test_avatar_floating_round_ensures_chat_visible_before_first_highlight(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" hidden>
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:360px; height:280px;">
                        <div id="react-chat-window-root">
                            <section class="chat-window" style="width:360px; height:280px;">
                                <div class="composer-panel" style="width:320px; height:72px;">
                                    <textarea class="composer-input" style="width:300px; height:44px;"></textarea>
                                </div>
                            </section>
                        </div>
                    </div>
                </div>
            `;
            window.__guideSurfaceCalls = [];
            window.reactChatWindowHost = {
                ensureBundleLoaded: async () => {
                    window.__guideSurfaceCalls.push('bundle');
                },
                openWindow: () => {
                    document.getElementById('react-chat-window-overlay').hidden = false;
                    window.__guideSurfaceCalls.push('open');
                },
                setGalgameModeEnabled: (enabled) => {
                    window.__guideSurfaceCalls.push('galgame:' + String(enabled));
                },
            };
            window.NekoHomeTutorialFeatureController = {
                begin: (reason) => { window.__guideSurfaceCalls.push('begin:' + reason); },
                enforce: (reason) => { window.__guideSurfaceCalls.push('enforce:' + reason); },
                end: (reason) => { window.__guideSurfaceCalls.push('end:' + reason); },
                isActive: () => true,
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = window.__guideSurfaceCalls;
            director.getAvatarFloatingRoundConfig = () => ({
                scenes: [{ id: 'day4_intro_companion', text: '', voiceKey: '' }],
            });
            const realEnsure = director.ensureChatVisible.bind(director);
            director.ensureChatVisible = async () => {
                calls.push('ensure:start');
                const target = await realEnsure();
                calls.push('ensure:end:' + String(!document.getElementById('react-chat-window-overlay').hidden));
                return target;
            };
            const realHighlight = director.highlightChatWindow.bind(director);
            director.highlightChatWindow = () => {
                calls.push('highlight:' + String(!document.getElementById('react-chat-window-overlay').hidden));
                realHighlight();
            };
            director.ensureGuideIdleSwayPerformance = async () => null;
            director.ensurePersistentGhostCursorLookAtPerformance = async () => null;
            director.stopPersistentGhostCursorLookAtPerformance = async () => null;
            director.stopIntroVoiceCursorLookAtPerformance = async () => null;
            director.closeAvatarFloatingGuidePanels = async () => {};
            director.playAvatarFloatingScene = async () => {
                calls.push('scene');
                return true;
            };
            await director.playAvatarFloatingRound(4, { source: 'test' });
            return {
                calls,
                overlayHidden: document.getElementById('react-chat-window-overlay').hidden,
            };
        }
        """
    )

    calls = result["calls"]
    assert result["overlayHidden"] is False
    assert calls.index("ensure:start") < calls.index("highlight:true")
    assert calls.index("ensure:end:true") < calls.index("highlight:true")
    assert calls.index("highlight:true") < calls.index("scene")
    assert any(call == "enforce:avatar-floating-day4-surface-ready" for call in calls)


@pytest.mark.frontend
def test_avatar_floating_round_starts_cursor_look_at_before_first_scene(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-overlay">
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:360px; height:280px;"></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            let releaseLookAt;
            director.getAvatarFloatingRoundConfig = () => ({
                scenes: [{ id: 'day2_intro_context', text: '', voiceKey: '' }],
            });
            director.ensureAvatarFloatingGuideSurfaceReady = async () => {
                events.push('surface');
            };
            director.highlightChatWindow = () => {
                events.push('highlight');
            };
            director.ensureGuideIdleSwayPerformance = async () => null;
            director.ensurePersistentGhostCursorLookAtPerformance = async () => {
                events.push('lookAt:start');
                await new Promise((resolve) => {
                    releaseLookAt = () => {
                        events.push('lookAt:ready');
                        resolve();
                    };
                });
                return {
                    stop: async (reason) => events.push('lookAt:stop:' + reason),
                };
            };
            director.stopPersistentGhostCursorLookAtPerformance = async (reason) => {
                events.push('lookAt:stopPersistent:' + reason);
            };
            director.closeAvatarFloatingGuidePanels = async () => {};
            director.playAvatarFloatingScene = async () => {
                events.push('scene');
                return true;
            };

            const roundPromise = director.playAvatarFloatingRound(2, { source: 'test' });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeRelease = events.slice();
            releaseLookAt();
            await roundPromise;

            return {
                beforeRelease,
                events,
            };
        }
        """
    )

    assert "lookAt:start" in result["beforeRelease"]
    assert "scene" not in result["beforeRelease"]
    assert result["events"].index("lookAt:ready") < result["events"].index("scene")


@pytest.mark.frontend
def test_avatar_floating_round_locks_compact_input_until_round_cleanup(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__tutorialInputLocks = [];
            window.reactChatWindowHost = {
                setHomeTutorialInputLocked: (locked, reason) => {
                    window.__tutorialInputLocks.push({ locked, reason });
                },
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.getAvatarFloatingRoundConfig = () => ({
                scenes: [{ id: 'day1_intro', text: '', voiceKey: '' }],
            });
            director.ensureAvatarFloatingGuideSurfaceReady = async () => {};
            director.ensureGuideIdleSwayPerformance = async () => null;
            director.ensurePersistentGhostCursorLookAtPerformance = async () => null;
            director.stopPersistentGhostCursorLookAtPerformance = async () => {};
            director.stopIntroVoiceCursorLookAtPerformance = async () => {};
            director.closeAvatarFloatingGuidePanels = async () => {};
            director.playAvatarFloatingScene = async () => true;

            await director.playAvatarFloatingRound(1, { source: 'test' });
            return window.__tutorialInputLocks;
        }
        """
    )

    assert result[0] == {
        "locked": True,
        "reason": "avatar-floating-guide-day1",
    }
    assert result[-1] == {
        "locked": False,
        "reason": "avatar-floating-guide-day1-complete",
    }


@pytest.mark.frontend
def test_day3_round_resets_compact_tool_wheel_import_to_slot_zero(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__guideSurfaceCalls = [];
            window.reactChatWindowHost = {
                setCompactToolWheelIndex: (index, reason) => {
                    window.__guideSurfaceCalls.push({
                        type: 'wheelIndex',
                        index,
                        reason,
                    });
                },
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = window.__guideSurfaceCalls;
            director.getAvatarFloatingRoundConfig = () => ({
                scenes: [{ id: 'day3_tool_toggle_intro', text: '', voiceKey: '', target: 'chat-input' }],
            });
            director.ensureAvatarFloatingGuideSurfaceReady = async () => {
                calls.push({ type: 'surface' });
            };
            director.ensureGuideIdleSwayPerformance = async () => null;
            director.ensurePersistentGhostCursorLookAtPerformance = async () => null;
            director.stopPersistentGhostCursorLookAtPerformance = async () => null;
            director.stopIntroVoiceCursorLookAtPerformance = async () => null;
            director.closeAvatarFloatingGuidePanels = async () => {};
            director.playAvatarFloatingScene = async () => {
                calls.push({ type: 'scene' });
                return true;
            };

            await director.playAvatarFloatingRound(3, { source: 'test' });
            return calls;
        }
        """
    )

    reset = {
        "type": "wheelIndex",
        "index": 0,
        "reason": "avatar-floating-guide-day3-entry-reset",
    }
    assert reset in result
    assert result.index({"type": "surface"}) < result.index(reset)
    assert result.index(reset) < result.index({"type": "scene"})


@pytest.mark.frontend
def test_avatar_floating_daily_scenes_keep_persistent_cursor_look_at_enabled(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = '<button id="live2d-btn-agent" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>';
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return [
                'day1_home_greeting',
                'day2_wrap_intro',
                'day3_avatar_tools',
                'day4_intro_companion',
                'day5_personalization',
                'day6_agent_intro',
                'day7_graduation',
            ].map((sceneId) => ({
                sceneId,
                enabled: director.shouldUsePersistentGhostCursorLookAt(sceneId),
            }));
        }
        """
    )

    assert all(item["enabled"] for item in result)


@pytest.mark.frontend
def test_intro_voice_cursor_look_at_ramps_from_forward_to_cursor_position(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = '<div id="live2d-container" style="position:absolute; left:0; top:0; width:800px; height:600px;"></div>';
            const paramIds = [
                'ParamAngleX',
                'ParamAngleY',
                'ParamAngleZ',
                'ParamEyeBallX',
                'ParamEyeBallY',
                'ParamBodyAngleX',
                'ParamBodyAngleY',
                'ParamBodyAngleZ',
            ];
            const values = Object.fromEntries(paramIds.map((id) => [id, 0]));
            const coreModel = {
                getParameterIndex: (id) => paramIds.indexOf(id),
                getParameterValueByIndex: (index) => values[paramIds[index]] || 0,
                setParameterValueByIndex: (index, value) => {
                    values[paramIds[index]] = value;
                },
                getParameterMinimumValueByIndex: (index) => paramIds[index].includes('EyeBall') ? -1 : -30,
                getParameterMaximumValueByIndex: (index) => paramIds[index].includes('EyeBall') ? 1 : 30,
                getParameterDefaultValueByIndex: () => 0,
                __values: values,
            };
            const model = {
                destroyed: false,
                internalModel: { coreModel },
                getBounds: () => ({ left: 0, right: 100, top: 0, bottom: 100 }),
                focus: () => {},
            };
            window.live2dManager = {
                currentModel: model,
                getCurrentModel: () => model,
                getBubbleAnchorGeometryInfo: () => ({ headAnchor: { x: 0, y: 0 } }),
                __coreModel: coreModel,
            };
        """,
        script_names=("tutorial/avatar/yui-stage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const handle = await window.YuiGuideAvatarStage.startIntroVoiceCursorLookAt({
                getPoint: () => ({ x: 360, y: 0 }),
                isCancelled: () => false,
            });
            const valueAfterStart = window.live2dManager.__coreModel.__values.ParamAngleX;
            await new Promise((resolve) => setTimeout(resolve, 1300));
            const valueAfterRamp = window.live2dManager.__coreModel.__values.ParamAngleX;
            if (handle && typeof handle.stop === 'function') {
                await handle.stop('test');
            }
            return { valueAfterStart, valueAfterRamp };
        }
        """
    )

    assert abs(result["valueAfterStart"]) < 1
    assert result["valueAfterRamp"] >= 7


@pytest.mark.frontend
def test_avatar_floating_open_agent_clears_button_highlight_for_panel(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const highlightConfigs = [];
            const panel = document.createElement('div');
            panel.id = 'live2d-popup-agent';
            director.openAgentPanel = async () => true;
            director.resolveAvatarFloatingPersistent = async () => panel;
            director.applyGuideHighlights = (config) => {
                highlightConfigs.push({
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    hasPrimary: Object.prototype.hasOwnProperty.call(config, 'primary'),
                    primary: config.primary,
                    hasSecondary: Object.prototype.hasOwnProperty.call(config, 'secondary'),
                    secondary: config.secondary,
                });
                return { persistent: config.persistent || null, primary: config.primary || null, secondary: config.secondary || null };
            };
            const opened = await director.runAvatarFloatingSceneOperation({
                id: 'day6_intro_agent',
                operation: 'open-agent',
            }, document.createElement('button'), Date.now());
            return { opened, highlightConfigs };
        }
        """
    )

    assert result["opened"] is True
    assert result["highlightConfigs"] == [
        {
            "key": "day6_intro_agent-panel-open",
            "persistentId": "live2d-popup-agent",
            "hasPrimary": True,
            "primary": None,
            "hasSecondary": True,
            "secondary": None,
        }
    ]


@pytest.mark.frontend
def test_day6_status_and_plugin_lines_run_split_plugin_dashboard_flow(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-agent" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <section id="live2d-popup-agent" style="display:flex; opacity:1; position:absolute; left:90px; top:28px; width:320px; height:440px;"></section>
                <button id="live2d-toggle-agent-user-plugin" style="position:absolute; left:120px; top:150px; width:180px; height:48px;"></button>
                <section data-neko-sidepanel data-neko-sidepanel-type="agent-user-plugin-actions" style="display:flex; opacity:1; position:absolute; left:430px; top:80px; width:260px; height:300px;">
                    <button id="neko-sidepanel-action-agent-user-plugin-management-panel" style="position:absolute; left:30px; top:44px; width:180px; height:44px;"></button>
                </section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.waitForSceneDelay = async (durationMs) => {
                if (durationMs === 500) {
                    calls.push({ type: 'wait', durationMs });
                }
                return true;
            };
            const dashboardWindow = { closed: false };
            director.setSpotlightGeometryHint = (element, options) => {
                calls.push({
                    type: 'geometry',
                    id: element && element.id,
                    geometry: options && options.geometry ? options.geometry : null,
                    padding: options && options.padding,
                });
            };
            const realCreatePluginManagementEntrySpotlight = director.createPluginManagementEntrySpotlight.bind(director);
            director.createPluginManagementEntrySpotlight = (button) => {
                const spotlight = realCreatePluginManagementEntrySpotlight(button);
                calls.push({
                    type: 'virtualSpotlight',
                    key: spotlight && spotlight.getAttribute('data-yui-guide-virtual-spotlight'),
                    padding: spotlight && spotlight.getAttribute('data-yui-guide-spotlight-padding'),
                    width: Math.round(spotlight && spotlight.getBoundingClientRect().width || 0),
                    height: Math.round(spotlight && spotlight.getBoundingClientRect().height || 0),
                });
                return spotlight;
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary ? config.primary.id : null,
                    persistentId: config.persistent ? config.persistent.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.moveCursorToElement = async (element, durationMs, options) => {
                calls.push({
                    type: 'move',
                    id: element && element.id,
                    durationMs,
                    exactDuration: !!(options && options.exactDuration),
                    offsetY: options && options.targetPointOffset && options.targetPointOffset.y,
                    clamp: !!(options && options.clampTargetPointToRect),
                    inset: options && options.targetPointClampInsetPx,
                });
                return true;
            };
            director.moveCursorToTrackedElement = async (element, durationMs, options) => {
                calls.push({
                    type: 'trackedMove',
                    id: element && element.id,
                    durationMs,
                    exactDuration: !!(options && options.exactDuration),
                    recheckDelayMs: options && options.recheckDelayMs,
                    settleDelayMs: options && options.settleDelayMs,
                });
                return true;
            };
            director.waitForStableElementRect = async (element, timeoutMs) => {
                calls.push({
                    type: 'stableRect',
                    id: element && element.id,
                    timeoutMs,
                });
                return element;
            };
            director.isCursorAlignedWithElement = () => true;
            director.cursor = {
                hasPosition: () => true,
                hasVisiblePosition: () => true,
                showAt: () => {},
                moveToRect: async () => true,
                moveToPoint: async (x, y, options) => {
                    calls.push({
                        type: 'pointMove',
                        x: Math.round(x),
                        y: Math.round(y),
                        durationMs: options && options.durationMs,
                        exactDuration: !!(options && options.exactDuration),
                    });
                    return true;
                },
                click: (visibleMs) => calls.push({ type: 'click', visibleMs }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => calls.push({ type: 'cursor:hide' }),
            };
            director.overlay.getCursorPosition = () => ({ x: 321, y: 234 });
            director.openAgentPanel = async () => {
                calls.push({ type: 'api:openAgentPanel' });
                return true;
            };
            director.ensureAvatarFloatingAgentSidePanel = async (toggleId) => {
                calls.push({ type: 'api:ensureAgentSidePanel', toggleId });
                return document.querySelector('[data-neko-sidepanel-type="agent-user-plugin-actions"]');
            };
            director.ensureAgentSidePanelActionVisible = async (toggleId, actionId) => {
                calls.push({ type: 'api:ensureActionVisible', toggleId, actionId });
                return document.getElementById('neko-sidepanel-action-agent-user-plugin-management-panel');
            };
            director.clickAgentSidePanelAction = async (toggleId, actionId, options) => {
                calls.push({
                    type: 'api:clickAgentSidePanelAction',
                    toggleId,
                    actionId,
                    keepMainUIVisible: options && options.keepMainUIVisible === true,
                    source: options && options.source,
                    sceneId: options && options.sceneId,
                });
                return true;
            };
            director.waitForOpenedWindow = async (windowName, timeoutMs) => {
                calls.push({ type: 'api:waitForOpenedWindow', windowName, timeoutMs });
                return timeoutMs === 120 ? null : dashboardWindow;
            };
            director.waitForPluginDashboardPerformance = async (windowRef, payload) => {
                calls.push({
                    type: 'api:waitForPluginDashboardPerformance',
                    sameWindow: windowRef === dashboardWindow,
                    line: payload.line,
                    voiceKey: payload.voiceKey,
                    closeOnDone: payload.closeOnDone,
                    narrationStartedAtMs: payload.narrationStartedAtMs,
                });
                return new Promise((resolve) => {
                    director.__resolveDashboardPerformance = () => resolve(true);
                });
            };
            director.notifyPluginDashboardNarrationFinished = () => {
                calls.push({ type: 'api:notifyPluginDashboardNarrationFinished' });
                if (director.__resolveDashboardPerformance) {
                    director.__resolveDashboardPerformance();
                }
            };
            director.closePluginDashboardWindowIfCreatedByGuide = async (context) => {
                calls.push({ type: 'api:closePluginDashboardWindowIfCreatedByGuide', context });
                dashboardWindow.closed = true;
            };
            director.closeAgentPanel = async () => {
                calls.push({ type: 'api:closeAgentPanel' });
                return true;
            };
            director.collapseAgentSidePanel = (toggleId) => {
                calls.push({ type: 'ui:collapseAgentSidePanel', toggleId });
                return true;
            };
            director.clearVirtualSpotlight = (key) => calls.push({ type: 'ui:clearVirtualSpotlight', key });
            director.stopHoverElement = (element) => calls.push({ type: 'ui:stopHoverElement', id: element && element.id });
            director.waitForHomeMainUIReady = async (timeoutMs) => {
                calls.push({ type: 'api:waitForHomeMainUIReady', timeoutMs });
                return true;
            };
            director.cursor.showAt = (x, y) => calls.push({ type: 'cursor:showAt', x, y });

            const openedAgent = await director.runAvatarFloatingSceneOperation({
                id: 'day6_agent_status_master',
                text: '快跟我老实交代，这两天你有没有点开它试用一下呀？',
                voiceKey: 'avatar_floating_day6_status_master',
                operation: 'day6-plugin-open-agent-panel-flow',
            }, null, 1700000000000);
            const openedManagement = await director.runAvatarFloatingSceneOperation({
                id: 'day6_plugin_side_panel',
                text: '除了之前介绍的功能，这里还有超多好玩的插件呢。',
                voiceKey: 'avatar_floating_day6_plugin_side_panel',
                operation: 'day6-plugin-open-management-panel-flow',
            }, null, 1700000001000);
            const dashboardHandoff = await director.runAvatarFloatingSceneOperation({
                id: 'day6_plugin_dashboard',
                text: '有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！',
                voiceKey: 'avatar_floating_day6_plugin_dashboard',
                operation: 'day6-plugin-dashboard-handoff-flow',
            }, null, 1700000002000);
            return { openedAgent, openedManagement, dashboardHandoff, calls };
        }
        """
    )

    assert result["openedAgent"] is True
    assert result["openedManagement"] is True
    assert result["dashboardHandoff"] is True
    assert result["calls"] == [
        {"type": "geometry", "id": "live2d-btn-agent", "geometry": "circle", "padding": 4},
        {"type": "highlight", "key": "day6_agent_status_master-cat-paw", "primaryId": "live2d-btn-agent", "persistentId": None},
        {"type": "wait", "durationMs": 500},
        {
            "type": "move",
            "id": "live2d-btn-agent",
            "durationMs": 2800,
            "exactDuration": False,
            "offsetY": 8,
            "clamp": True,
            "inset": 4,
        },
        {"type": "click", "visibleMs": 620},
        {"type": "api:openAgentPanel"},
        {"type": "api:openAgentPanel"},
        {"type": "stableRect", "id": "live2d-toggle-agent-user-plugin", "timeoutMs": 760},
        {
            "type": "highlight",
            "key": "day6_plugin_side_panel-user-plugin",
            "primaryId": "live2d-toggle-agent-user-plugin",
            "persistentId": None,
        },
        {"type": "wait", "durationMs": 500},
        {
            "type": "trackedMove",
            "id": "live2d-toggle-agent-user-plugin",
            "durationMs": 1120,
            "exactDuration": True,
            "recheckDelayMs": 120,
            "settleDelayMs": 40,
        },
        {"type": "api:ensureAgentSidePanel", "toggleId": "user-plugin"},
        {
            "type": "api:ensureActionVisible",
            "toggleId": "agent-user-plugin",
            "actionId": "management-panel",
        },
        {
            "type": "stableRect",
            "id": "neko-sidepanel-action-agent-user-plugin-management-panel",
            "timeoutMs": 760,
        },
        {
            "type": "highlight",
            "key": "day6_plugin_side_panel-clear-user-plugin",
            "primaryId": None,
            "persistentId": None,
        },
        {"type": "ui:clearVirtualSpotlight", "key": "plugin-management-entry"},
        {"type": "virtualSpotlight", "key": "plugin-management-entry", "padding": "0", "width": 216, "height": 64},
        {
            "type": "highlight",
            "key": "day6_plugin_side_panel-management-panel",
            "primaryId": "",
            "persistentId": None,
        },
        {
            "type": "trackedMove",
            "id": "neko-sidepanel-action-agent-user-plugin-management-panel",
            "durationMs": 1120,
            "exactDuration": True,
            "recheckDelayMs": 120,
            "settleDelayMs": 40,
        },
        {"type": "ui:clearVirtualSpotlight", "key": "plugin-management-entry"},
        {"type": "virtualSpotlight", "key": "plugin-management-entry", "padding": "0", "width": 216, "height": 64},
        {
            "type": "highlight",
            "key": "day6_plugin_side_panel-management-panel",
            "primaryId": "",
            "persistentId": None,
        },
        {"type": "click", "visibleMs": 480},
        {"type": "api:waitForOpenedWindow", "windowName": "plugin_dashboard", "timeoutMs": 120},
        {
            "type": "api:clickAgentSidePanelAction",
            "toggleId": "agent-user-plugin",
            "actionId": "management-panel",
            "keepMainUIVisible": True,
            "source": "avatar-floating-guide",
            "sceneId": "day6_plugin_side_panel",
        },
        {"type": "api:waitForOpenedWindow", "windowName": "plugin_dashboard", "timeoutMs": 900},
        {"type": "cursor:hide"},
        {
            "type": "api:waitForPluginDashboardPerformance",
            "sameWindow": True,
            "line": "有了它们，我不光能看 B 站弹幕，还能帮你关灯开空调…… 本喵就是无所不能的超级猫猫神！哼哼！",
            "voiceKey": "avatar_floating_day6_plugin_dashboard",
            "closeOnDone": False,
            "narrationStartedAtMs": 1700000002000,
        },
        {"type": "api:notifyPluginDashboardNarrationFinished"},
        {"type": "api:closePluginDashboardWindowIfCreatedByGuide", "context": "Day 6 插件管理预览完成"},
        {"type": "ui:collapseAgentSidePanel", "toggleId": "agent-user-plugin"},
        {"type": "ui:clearVirtualSpotlight", "key": "plugin-management-entry"},
        {"type": "ui:stopHoverElement", "id": "live2d-toggle-agent-user-plugin"},
        {"type": "api:closeAgentPanel"},
        {"type": "api:waitForHomeMainUIReady", "timeoutMs": 3600},
        {"type": "cursor:showAt", "x": 321, "y": 234},
    ]


@pytest.mark.frontend
def test_day6_plugin_side_panel_does_not_clear_externalized_chat_target_when_entering_from_cat_paw_scene(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const sidePanelScene = window.YuiGuideDailyGuides[6].round.scenes.find(
                (candidate) => candidate.id === 'day6_plugin_side_panel'
            );
            director.currentSceneId = 'day6_agent_status_master';
            director.isHomeChatExternalized = () => true;
            director.clearExternalizedChatGuideTarget = (options) => {
                events.push({
                    type: 'clear-external',
                    clearCursor: !!(options && options.clearCursor),
                    preservePcOverlayCursor: !!(options && options.preservePcOverlayCursor),
                });
            };
            director.sceneOrchestrator.canPlayTimelineScene = () => false;
            director.sceneOrchestrator.playGenericScene = async () => {
                events.push({ type: 'play-generic' });
                return true;
            };

            const played = await director.playAvatarFloatingScene(sidePanelScene, 6, 2, 8);
            return { played, events };
        }
        """
    )

    assert result == {
        "played": True,
        "events": [{"type": "play-generic"}],
    }


@pytest.mark.frontend
def test_day6_status_reveals_hidden_cat_paw_before_cursor_move(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="live2d-floating-buttons" style="display:none; position:fixed; left:20px; top:30px; width:60px; height:300px;">
                    <button id="live2d-btn-agent" style="position:absolute; left:0; top:0; width:44px; height:44px;"></button>
                </div>
                <section id="live2d-popup-agent" style="display:none; opacity:0; position:absolute; left:90px; top:28px; width:320px; height:440px;"></section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.waitForSceneDelay = async () => true;
            director.prepareDay6AgentPanelCursorFromCapsule = async () => {
                calls.push({ type: 'unexpected:fromCapsule' });
                return false;
            };
            director.moveCursorToElement = async (element, durationMs, options) => {
                calls.push({
                    type: 'move',
                    id: element && element.id,
                    durationMs,
                    offsetY: options && options.targetPointOffset && options.targetPointOffset.y,
                    clamp: !!(options && options.clampTargetPointToRect),
                    inset: options && options.targetPointClampInsetPx,
                });
                return true;
            };
            director.cursor.click = (visibleMs) => calls.push({ type: 'click', visibleMs });
            director.openAgentPanel = async () => {
                calls.push({ type: 'api:openAgentPanel' });
                return true;
            };

            const opened = await director.runDay6PluginOpenAgentPanelFlow({
                id: 'day6_agent_status_master',
                voiceKey: 'avatar_floating_day6_status_master',
            });
            const toolbar = document.getElementById('live2d-floating-buttons');
            return {
                opened,
                toolbarDisplay: toolbar ? window.getComputedStyle(toolbar).display : '',
                calls,
            };
        }
        """
    )

    assert result == {
        "opened": True,
        "toolbarDisplay": "flex",
        "calls": [
            {
                "type": "move",
                "id": "live2d-btn-agent",
                "durationMs": 2800,
                "offsetY": 8,
                "clamp": True,
                "inset": 4,
            },
            {"type": "click", "visibleMs": 620},
            {"type": "api:openAgentPanel"},
        ],
    }


@pytest.mark.frontend
def test_day6_status_opens_cat_paw_without_capsule_cursor_start(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-agent" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <section id="live2d-popup-agent" style="display:none; opacity:0; position:absolute; left:90px; top:28px; width:320px; height:440px;"></section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.waitForSceneDelay = async () => true;
            director.prepareDay6AgentPanelCursorFromCapsule = async () => {
                calls.push({ type: 'unexpected:fromCapsule' });
                return false;
            };
            director.moveCursorToElement = async (element, durationMs, options) => {
                calls.push({
                    type: 'move',
                    id: element && element.id,
                    durationMs,
                    offsetY: options && options.targetPointOffset && options.targetPointOffset.y,
                    clamp: !!(options && options.clampTargetPointToRect),
                    inset: options && options.targetPointClampInsetPx,
                });
                return true;
            };
            director.cursor.click = (visibleMs) => calls.push({ type: 'click', visibleMs });
            director.openAgentPanel = async () => {
                calls.push({ type: 'api:openAgentPanel' });
                return true;
            };

            const opened = await director.runDay6PluginOpenAgentPanelFlow({
                id: 'day6_agent_status_master',
                voiceKey: 'avatar_floating_day6_status_master',
            });
            return { opened, calls };
        }
        """
    )

    assert result == {
        "opened": True,
        "calls": [
            {
                "type": "move",
                "id": "live2d-btn-agent",
                "durationMs": 2800,
                "offsetY": 8,
                "clamp": True,
                "inset": 4,
            },
            {"type": "click", "visibleMs": 620},
            {"type": "api:openAgentPanel"},
        ],
    }


@pytest.mark.frontend
def test_day6_move_cursor_to_element_supports_target_point_offset(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-agent" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            const button = document.getElementById('live2d-btn-agent');
            director.cursor = {
                moveToPoint: async (x, y, options) => {
                    calls.push({
                        type: 'pointMove',
                        x: Math.round(x),
                        y: Math.round(y),
                        durationMs: options && options.durationMs,
                    });
                    return true;
                },
                cancel: () => {},
            };

            const moved = await director.moveCursorToElement(button, 2800, {
                targetPointOffset: { y: 8 },
                clampTargetPointToRect: true,
                targetPointClampInsetPx: 4,
            });
            return { moved, calls };
        }
        """
    )

    assert result == {
        "moved": True,
        "calls": [
            {"type": "pointMove", "x": 42, "y": 60, "durationMs": 2800},
        ],
    }

@pytest.mark.frontend
def test_day6_wrap_cleanup_holds_cursor_to_avoid_resistance_move_overlap(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
        """,
        script_names=(
            "tutorial/yui-guide/overlay.js",
            "tutorial/yui-guide/director.js",
            "tutorial/yui-guide/days/day6-agent-guide.js",
        ),
    )

    result = mock_page.evaluate(
        """
        () => {
            const scene = window.YuiGuideDailyGuides[6].round.scenes.find(
                (candidate) => candidate.id === 'day6_wrap_cleanup'
            );
            return {
                cursorAction: scene && scene.cursorAction,
                target: scene && scene.target,
                operation: scene && scene.operation,
            };
        }
        """
    )

    assert result == {
        "cursorAction": "hold",
        "target": "chat-input",
        "operation": "cleanup",
    }


@pytest.mark.frontend
def test_day6_management_panel_spotlight_extends_width_and_vertical_margin_without_padding(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button
                    id="neko-sidepanel-action-agent-user-plugin-management-panel"
                    style="position:absolute; left:130px; top:84px; width:180px; height:44px;"
                ></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const button = document.getElementById('neko-sidepanel-action-agent-user-plugin-management-panel');
            const spotlight = director.createPluginManagementEntrySpotlight(button);
            const rect = spotlight.getBoundingClientRect();
            const buttonRect = button.getBoundingClientRect();
                return {
                    isVirtual: spotlight.getAttribute('data-yui-guide-virtual-spotlight'),
                    padding: spotlight.getAttribute('data-yui-guide-spotlight-padding'),
                    leftDelta: Math.round(buttonRect.left - rect.left),
                    rightDelta: Math.round(rect.right - buttonRect.right),
                    topDelta: Math.round(buttonRect.top - rect.top),
                    bottomDelta: Math.round(rect.bottom - buttonRect.bottom),
                    height: Math.round(rect.height),
                    buttonHeight: Math.round(buttonRect.height),
                };
        }
        """
    )

    assert result == {
        "isVirtual": "plugin-management-entry",
        "padding": "0",
        "leftDelta": 18,
        "rightDelta": 18,
        "topDelta": 10,
        "bottomDelta": 10,
        "height": 64,
        "buttonHeight": 44,
    }


@pytest.mark.frontend
def test_day6_task_hud_only_moves_cursor_to_hud_without_post_line_tour(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <section
                    id="agent-task-hud"
                    style="position:absolute; left:120px; top:90px; width:260px; height:140px;"
                >
                    <button id="hud-collapse" style="position:absolute; left:20px; top:20px; width:44px; height:32px;"></button>
                    <button id="hud-cancel" style="position:absolute; left:82px; top:20px; width:44px; height:32px;"></button>
                </section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            const scene = window.YuiGuideDailyGuides[6].round.scenes.find(
                (candidate) => candidate.id === 'day6_agent_task_hud'
            );
            director.currentSceneId = 'day6_plugin_dashboard';
            director.waitForSceneDelay = async () => true;
            director.prepareAvatarFloatingScene = async () => {};
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.speakGuideLine = async () => {
                calls.push({ type: 'narration:start' });
                calls.push({ type: 'narration:done' });
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: (x, y) => calls.push({ type: 'showAt', x, y }),
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => calls.push({ type: 'hide' }),
            };

            await director.playAvatarFloatingScene(scene, 6, 4, 8);
            return calls;
        }
        """
    )

    assert {
        "type": "highlight",
        "key": "day6_agent_task_hud",
        "primaryId": "agent-task-hud",
    } in result
    assert [
        call for call in result
        if call["type"] == "move"
    ] == [{
        "type": "move",
        "id": "agent-task-hud",
        "durationMs": 760,
    }]
    assert {"type": "wobble"} not in result
    assert {"type": "click"} not in result


@pytest.mark.frontend
def test_day6_task_hud_control_moves_cursor_to_hud_with_reused_spotlight(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <section
                    id="agent-task-hud"
                    style="position:absolute; left:120px; top:90px; width:260px; height:140px;"
                ></section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            const scene = window.YuiGuideDailyGuides[6].round.scenes.find(
                (candidate) => candidate.id === 'day6_agent_task_hud_control'
            );
            director.currentSceneId = 'day6_agent_task_hud';
            director.waitForSceneDelay = async () => true;
            director.prepareAvatarFloatingScene = async () => {};
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.speakGuideLine = async () => {
                calls.push({ type: 'narration:start' });
                await new Promise((resolve) => setTimeout(resolve, 20));
                calls.push({ type: 'narration:done' });
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: (x, y) => calls.push({ type: 'showAt', x, y }),
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => calls.push({ type: 'hide' }),
                runPauseAwareEllipse: async (centerX, centerY, radiusX, radiusY, cycleMs) => {
                    calls.push({
                        type: 'ellipse',
                        centerX: Math.round(centerX),
                        centerY: Math.round(centerY),
                        radiusX: Math.round(radiusX),
                        radiusY: Math.round(radiusY),
                        cycleMs,
                    });
                    await new Promise((resolve) => setTimeout(resolve, 5));
                    return true;
                },
            };

            await director.playAvatarFloatingScene(scene, 6, 5, 8);
            return calls;
        }
        """
    )

    assert {
        "type": "highlight",
        "key": "day6_agent_task_hud",
        "primaryId": "agent-task-hud",
    } in result
    assert [
        call for call in result
        if call["type"] == "move"
    ] == [{
        "type": "move",
        "id": "agent-task-hud",
        "durationMs": 760,
    }]
    assert not any(call["type"] == "ellipse" for call in result)
    assert {"type": "wobble"} not in result
    assert {"type": "click"} not in result


@pytest.mark.frontend
def test_day6_task_hud_control_reuses_hud_spotlight_key_while_moving_cursor_to_hud(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <section
                    id="agent-task-hud"
                    style="position:absolute; left:120px; top:90px; width:260px; height:140px;"
                ></section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            const scenes = window.YuiGuideDailyGuides[6].round.scenes;
            const hudScene = scenes.find((candidate) => candidate.id === 'day6_agent_task_hud');
            const controlScene = scenes.find((candidate) => candidate.id === 'day6_agent_task_hud_control');
            director.waitForSceneDelay = async () => true;
            director.prepareAvatarFloatingScene = async () => {};
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                calls.push({ type: 'operation', id: playedScene.id, operation: playedScene.operation || '' });
                return true;
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({
                    type: 'move',
                    sceneId: director.currentSceneId,
                    id: element && element.id,
                    durationMs,
                });
                return true;
            };
            director.speakGuideLine = async () => {};
            director.cursor = {
                hasPosition: () => true,
                hasVisiblePosition: () => true,
                showAt: (x, y) => calls.push({ type: 'showAt', x, y }),
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => calls.push({ type: 'hide' }),
            };

            await director.playAvatarFloatingScene(hudScene, 6, 4, 8);
            await director.playAvatarFloatingScene(controlScene, 6, 5, 8);
            return calls;
        }
        """
    )

    assert [
        call for call in result
        if call["type"] == "highlight"
    ] == [
        {"type": "highlight", "key": "day6_agent_task_hud", "primaryId": "agent-task-hud"},
        {"type": "highlight", "key": "day6_agent_task_hud", "primaryId": "agent-task-hud"},
    ]
    assert [
        call for call in result
        if call["type"] == "move"
    ] == [
        {
            "type": "move",
            "sceneId": "day6_agent_task_hud",
            "id": "agent-task-hud",
            "durationMs": 760,
        },
        {
            "type": "move",
            "sceneId": "day6_agent_task_hud_control",
            "id": "agent-task-hud",
            "durationMs": 760,
        },
    ]


@pytest.mark.frontend
def test_day6_task_hud_control_preserves_externalized_chat_target_from_hud_scene(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const scenes = window.YuiGuideDailyGuides[6].round.scenes;
            const controlScene = scenes.find((candidate) => candidate.id === 'day6_agent_task_hud_control');
            return {
                id: controlScene && controlScene.id,
                target: controlScene && controlScene.target,
                cursorAction: controlScene && controlScene.cursorAction,
                spotlightKey: controlScene && controlScene.spotlightKey,
                preserveExternalizedChatGuideTarget: !!(
                    controlScene && controlScene.preserveExternalizedChatGuideTarget === true
                ),
            };
        }
        """
    )

    assert result == {
        "id": "day6_agent_task_hud_control",
        "target": "#agent-task-hud",
        "cursorAction": "move",
        "spotlightKey": "day6_agent_task_hud",
        "preserveExternalizedChatGuideTarget": True,
    }


@pytest.mark.frontend
def test_day6_task_hud_control_does_not_clear_externalized_chat_target_when_entering_from_hud(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const controlScene = window.YuiGuideDailyGuides[6].round.scenes.find(
                (candidate) => candidate.id === 'day6_agent_task_hud_control'
            );
            director.currentSceneId = 'day6_agent_task_hud';
            director.isHomeChatExternalized = () => true;
            director.clearExternalizedChatGuideTarget = (options) => {
                events.push({
                    type: 'clear-external',
                    clearCursor: !!(options && options.clearCursor),
                    preservePcOverlayCursor: !!(options && options.preservePcOverlayCursor),
                });
            };
            director.sceneOrchestrator.canPlayTimelineScene = () => false;
            director.sceneOrchestrator.playGenericScene = async () => {
                events.push({ type: 'play-generic' });
                return true;
            };

            const played = await director.playAvatarFloatingScene(controlScene, 6, 5, 8);
            return { played, events };
        }
        """
    )

    assert result == {
        "played": True,
        "events": [{"type": "play-generic"}],
    }


@pytest.mark.frontend
def test_day4_chat_settings_opens_settings_then_tours_sidebar(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-settings" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <button id="chat-settings-button" style="position:absolute; left:100px; top:80px; width:120px; height:44px;"></button>
                <section id="chat-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="chat-settings" style="position:absolute; left:260px; top:70px; width:240px; height:360px;"></section>
            `;
            document.getElementById('chat-settings-panel')._anchorElement = document.getElementById('chat-settings-button');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            let releaseNarration;
            director.appendGuideChatMessage = () => calls.push({ type: 'message' });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async () => true;
            director.openSettingsPanel = async () => {
                calls.push({ type: 'api:openSettingsPanel' });
                return true;
            };
            director.ensureAvatarFloatingSettingsSidePanel = async (type) => {
                calls.push({ type: 'api:ensureSidePanel', panelType: type });
                return document.getElementById('chat-settings-panel');
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                runPauseAwareEllipse: async (x, y, radiusX, radiusY) => {
                    calls.push({ type: 'ellipse', x, y, radiusX, radiusY });
                    if (releaseNarration) {
                        releaseNarration();
                    }
                    return true;
                },
            };
            director.speakGuideLine = () => new Promise((resolve) => {
                releaseNarration = () => {
                    calls.push({ type: 'narration:done' });
                    resolve();
                };
            });

            const scenePromise = director.playAvatarFloatingScene({
                id: 'day4_chat_settings',
                text: '在这里可以决定我回复你的长短，还能决定要不要让我带上可爱的表情，或者在人家唠叨的时候打断我哦！都可以调到让你最舒服的节奏。',
                voiceKey: 'avatar_floating_day4_chat_settings',
                target: 'settings-sidepanel:chat-settings',
                cursorAction: 'tour',
                operation: 'show-settings-sidepanel:chat-settings',
            }, 4, 1, 8);
            await scenePromise;
            return calls;
        }
        """
    )

    event_keys = [
        (call["type"], call.get("key"), call.get("primaryId"), call.get("persistentId"))
        for call in result
        if call["type"] == "highlight"
    ]
    assert event_keys[:3] == [
        ("highlight", "day4_chat_settings-settings-button", "live2d-btn-settings", "live2d-btn-settings"),
        ("highlight", "day4_chat_settings-chat-settings-button", "chat-settings-button", "live2d-btn-settings"),
        ("highlight", "day4_chat_settings-chat-settings-panel", "chat-settings-panel", "live2d-btn-settings"),
    ]
    assert result.index({"type": "api:openSettingsPanel"}) < result.index({
        "type": "highlight",
        "key": "day4_chat_settings-chat-settings-button",
        "persistentId": "live2d-btn-settings",
        "primaryId": "chat-settings-button",
    })
    assert {"type": "click"} in result
    assert any(call["type"] == "ellipse" and call["radiusX"] > 0 and call["radiusY"] > 0 for call in result)


@pytest.mark.frontend
def test_day4_model_behavior_moves_from_chat_sidebar_to_animation_sidebar(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-settings" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <button id="animation-settings-button" style="position:absolute; left:100px; top:140px; width:120px; height:44px;"></button>
                <section id="chat-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="chat-settings" style="display:flex; opacity:1; position:absolute; left:260px; top:70px; width:240px; height:360px;"></section>
                <section id="animation-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="animation-settings" style="position:absolute; left:260px; top:70px; width:260px; height:380px;"></section>
            `;
            const chatPanel = document.getElementById('chat-settings-panel');
            chatPanel._collapse = () => {
                window.__calls.push({ type: 'collapse', id: 'chat-settings-panel' });
                chatPanel.style.display = 'none';
                chatPanel.style.opacity = '0';
            };
            document.getElementById('animation-settings-panel')._anchorElement = document.getElementById('animation-settings-button');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.__calls = [];
            const director = window.createYuiGuideDirector({ page: 'home' });
            let releaseNarration;
            director.appendGuideChatMessage = () => window.__calls.push({ type: 'message' });
            director.applyGuideEmotion = (emotion) => window.__calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => window.__calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async () => true;
            director.openSettingsPanel = async () => {
                window.__calls.push({ type: 'api:openSettingsPanel' });
                return true;
            };
            director.ensureAvatarFloatingSettingsSidePanel = async (type) => {
                window.__calls.push({ type: 'api:ensureSidePanel', panelType: type });
                const panel = document.getElementById(type === 'animation-settings'
                    ? 'animation-settings-panel'
                    : 'chat-settings-panel');
                panel.style.display = 'flex';
                panel.style.opacity = '1';
                return panel;
            };
            director.applyGuideHighlights = (config) => {
                window.__calls.push({
                    type: 'highlight',
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.moveCursorToElement = async (element, durationMs) => {
                window.__calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => window.__calls.push({ type: 'click' }),
                wobble: () => window.__calls.push({ type: 'wobble' }),
                runPauseAwareEllipse: async (x, y, radiusX, radiusY) => {
                    window.__calls.push({ type: 'ellipse', x, y, radiusX, radiusY });
                    if (releaseNarration) {
                        releaseNarration();
                    }
                    return true;
                },
                cancel: () => {},
            };
            director.speakGuideLine = () => new Promise((resolve) => {
                releaseNarration = () => {
                    window.__calls.push({ type: 'narration:done' });
                    releaseNarration = null;
                    resolve();
                };
                window.setTimeout(() => {
                    if (releaseNarration) {
                        releaseNarration();
                    }
                }, 20);
            });

            await director.playAvatarFloatingScene({
                id: 'day4_model_behavior',
                text: '如果你想要看到更精致、细节更满满的我，或者想要更丝滑、更流畅的动作体验，都可以在这里进行调整哦！不管哪一种，我都会展现出最可爱的一面哒~',
                voiceKey: 'avatar_floating_day4_model_behavior',
                target: 'settings-sidepanel:animation-settings',
                cursorAction: 'tour',
                operation: 'show-settings-sidepanel:animation-settings',
            }, 4, 2, 8);
            return window.__calls;
        }
        """
    )

    event_keys = [
        (call["type"], call.get("key"), call.get("primaryId"), call.get("persistentId"))
        for call in result
        if call["type"] == "highlight"
    ]
    assert result.index({"type": "collapse", "id": "chat-settings-panel"}) < result.index({
        "type": "highlight",
        "key": "day4_model_behavior-animation-settings-button",
        "persistentId": "live2d-btn-settings",
        "primaryId": "animation-settings-button",
    })
    assert event_keys[:2] == [
        ("highlight", "day4_model_behavior-animation-settings-button", "animation-settings-button", "live2d-btn-settings"),
        ("highlight", "day4_model_behavior-animation-settings-panel", "animation-settings-panel", "live2d-btn-settings"),
    ]
    assert result.index({"type": "move", "id": "animation-settings-button", "durationMs": 620}) < result.index({
        "type": "api:ensureSidePanel",
        "panelType": "animation-settings",
    })
    assert any(call["type"] == "ellipse" and call["radiusX"] > 0 and call["radiusY"] > 0 for call in result)


@pytest.mark.frontend
def test_day5_character_settings_moves_from_chat_to_settings_and_sidebar(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <section id="react-chat-window-root" style="position:absolute; left:20px; top:320px; width:420px; height:160px;"></section>
                <button id="live2d-btn-settings" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <button id="character-settings-button" style="position:absolute; left:100px; top:80px; width:130px; height:44px;"></button>
                <section id="character-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="character-settings" style="position:absolute; left:260px; top:70px; width:260px; height:380px;"></section>
            `;
            document.getElementById('character-settings-panel')._anchorElement = document.getElementById('character-settings-button');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            let releaseNarration;
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async (durationMs) => {
                calls.push({ type: 'delay', durationMs });
                return true;
            };
            director.openSettingsPanel = async () => {
                calls.push({ type: 'api:openSettingsPanel' });
                return true;
            };
            director.ensureAvatarFloatingSettingsSidePanel = async (type) => {
                calls.push({ type: 'api:ensureSidePanel', panelType: type });
                const panel = document.getElementById('character-settings-panel');
                panel.style.display = 'flex';
                panel.style.opacity = '1';
                return panel;
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.overlay.clearActionSpotlight = () => calls.push({ type: 'clearActionSpotlight' });
            director.overlay.clearPersistentSpotlight = () => calls.push({ type: 'clearPersistentSpotlight' });
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => calls.push({ type: 'showAt' }),
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                runPauseAwareEllipse: async (x, y, radiusX, radiusY) => {
                    calls.push({ type: 'ellipse', x, y, radiusX, radiusY });
                    if (releaseNarration) {
                        releaseNarration();
                    }
                    return true;
                },
                cancel: () => calls.push({ type: 'cancel' }),
                hide: () => {},
            };
            director.speakGuideLine = () => new Promise((resolve) => {
                releaseNarration = () => {
                    calls.push({ type: 'narration:done' });
                    releaseNarration = null;
                    resolve();
                };
                window.setTimeout(() => {
                    if (releaseNarration) {
                        releaseNarration();
                    }
                }, 20);
            });

            await director.playAvatarFloatingScene({
                id: 'day5_character_settings',
                text: '从今天起，我就真正成为只属于你的专属猫娘啦。你看，在这里可以为我穿上漂亮的新衣服，也可以帮我换一个更好听的声音……',
                voiceKey: 'avatar_floating_day5_character_settings',
                target: 'settings-sidepanel:character-settings',
                cursorAction: 'tour',
                operation: 'show-settings-sidepanel:character-settings',
            }, 5, 0, 4);
            return calls;
        }
        """
    )

    assert [
        (call["key"], call["primaryId"], call["persistentId"])
        for call in result
        if call["type"] == "highlight"
    ][:4] == [
        ("day5_character_settings-intro-chat", "react-chat-window-root", None),
        ("day5_character_settings-settings-button", "live2d-btn-settings", "live2d-btn-settings"),
        ("day5_character_settings-character-settings-button", "character-settings-button", "live2d-btn-settings"),
        ("day5_character_settings-character-settings-panel", "character-settings-panel", "live2d-btn-settings"),
    ]
    assert {"type": "delay", "durationMs": 1000} in result
    assert result.index({"type": "clearActionSpotlight"}) < result.index({
        "type": "highlight",
        "key": "day5_character_settings-settings-button",
        "persistentId": "live2d-btn-settings",
        "primaryId": "live2d-btn-settings",
    })
    assert result.index({"type": "move", "id": "live2d-btn-settings", "durationMs": 760}) < result.index({
        "type": "api:openSettingsPanel",
    })
    assert result.index({"type": "move", "id": "character-settings-button", "durationMs": 620}) < result.index({
        "type": "api:ensureSidePanel",
        "panelType": "character-settings",
    })
    assert any(call["type"] == "ellipse" and call["radiusX"] > 0 and call["radiusY"] > 0 for call in result)
    assert {"type": "click"} not in result


@pytest.mark.frontend
def test_day5_character_panic_keeps_character_sidebar_highlight_then_clears(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <section id="character-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="character-settings" style="display:flex; opacity:1; position:absolute; left:260px; top:70px; width:260px; height:380px;"></section>
                <button id="live2d-sidepanel-live2d-manage" style="position:absolute; left:290px; top:110px; width:120px; height:44px;"></button>
                <button id="live2d-sidepanel-voice-clone" style="position:absolute; left:290px; top:170px; width:120px; height:44px;"></button>
            `;
            const panel = document.getElementById('character-settings-panel');
            panel._collapse = () => {
                window.__calls.push({ type: 'collapse', id: 'character-settings-panel' });
                panel.style.display = 'none';
                panel.style.opacity = '0';
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.__calls = [];
            const calls = window.__calls;
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async (durationMs) => {
                calls.push({ type: 'delay', durationMs });
                return true;
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    primaryId: config.primary ? config.primary.id : null,
                    secondaryId: config.secondary ? config.secondary.id : null,
                    persistentId: config.persistent ? config.persistent.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.overlay.clearActionSpotlight = () => calls.push({ type: 'clearActionSpotlight' });
            director.overlay.clearPersistentSpotlight = () => calls.push({ type: 'clearPersistentSpotlight' });
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => {},
            };
            director.runSettingsPeekPanicPerformance = async (options) => {
                calls.push({
                    type: 'panic',
                    hasTargetRect: !!options.targetRect,
                    totalDurationMs: options.totalDurationMs,
                });
                return true;
            };
            director.speakGuideLine = async () => calls.push({ type: 'narration:done' });

            await director.playAvatarFloatingScene({
                id: 'day5_character_panic',
                text: '咦，这里居然还能把我换掉吗？等一下呀！你现在的动作……该不会是想要把我换掉吧？啊啊啊不行！快关掉，快关掉！',
                voiceKey: 'avatar_floating_day5_character_panic',
                target: 'settings-sidepanel:character-settings',
                cursorAction: 'tour',
                operation: 'settings-peek-panic',
            }, 5, 1, 4);
            return calls;
        }
        """
    )

    assert {
        "type": "highlight",
        "key": "day5_character_panic-character-settings-panel",
        "primaryId": "character-settings-panel",
        "secondaryId": None,
        "persistentId": None,
    } in result
    assert not any(call.get("primaryId") == "live2d-sidepanel-live2d-manage" for call in result)
    assert not any(call.get("secondaryId") == "live2d-sidepanel-voice-clone" for call in result)
    narration_index = result.index({"type": "narration:done"})
    assert result.index({"type": "clearActionSpotlight"}) > narration_index
    assert result.index({"type": "clearPersistentSpotlight"}) > narration_index
    assert result.index({"type": "collapse", "id": "character-settings-panel"}) > narration_index


@pytest.mark.frontend
def test_day4_gaze_follow_highlights_mouse_tracking_toggle(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-settings" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <section id="animation-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="animation-settings" style="display:flex; opacity:1; position:absolute; left:260px; top:70px; width:260px; height:380px;">
                    <div id="mouse-tracking-row" role="switch" style="position:absolute; left:20px; top:120px; width:180px; height:42px;">
                        <input id="live2d-mouse-tracking-toggle" type="checkbox" style="display:none;">
                        <span>跟踪鼠标</span>
                    </div>
                </section>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async () => true;
            director.ensureAvatarFloatingSettingsSidePanel = async (type) => {
                calls.push({ type: 'api:ensureSidePanel', panelType: type });
                return document.getElementById('animation-settings-panel');
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => {},
            };
            director.speakGuideLine = async () => calls.push({ type: 'narration:done' });

            await director.playAvatarFloatingScene({
                id: 'day4_gaze_follow',
                text: '开启这个功能后，无论你的鼠标移动到哪里，人家的目光都会紧紧跟随着你哟！是不是有种被时刻关注的幸福感呢？',
                voiceKey: 'avatar_floating_day4_gaze_follow',
                target: 'settings-sidepanel:animation-settings',
                cursorAction: 'tour',
                operation: 'show-settings-sidepanel:animation-settings',
            }, 4, 3, 8);
            return calls;
        }
        """
    )

    assert {
        "type": "message",
        "text": "开启这个功能后，无论你的鼠标移动到哪里，人家的目光都会紧紧跟随着你哟！是不是有种被时刻关注的幸福感呢？",
    } in result
    assert {
        "type": "highlight",
        "key": "day4_gaze_follow-mouse-tracking-toggle",
        "persistentId": "live2d-btn-settings",
        "primaryId": "mouse-tracking-row",
    } in result
    assert {
        "type": "move",
        "id": "mouse-tracking-row",
        "durationMs": 620,
    } in result
    assert {"type": "click"} not in result


@pytest.mark.frontend
def test_day4_privacy_mode_highlights_privacy_without_privacy_sidepanel(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-settings" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
                <button id="live2d-lock-icon" style="position:absolute; left:80px; top:30px; width:44px; height:44px;"></button>
                <button id="privacy-mode-button" style="position:absolute; left:100px; top:200px; width:120px; height:44px;"></button>
                <section id="live2d-popup-settings" style="display:flex; opacity:1; pointer-events:auto; position:absolute; left:220px; top:40px; width:340px; height:440px;"></section>
                <section id="animation-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="animation-settings" style="display:flex; opacity:1; position:absolute; left:260px; top:70px; width:260px; height:380px;"></section>
                <section id="privacy-panel" data-neko-sidepanel data-neko-sidepanel-type="interval-proactive-vision" style="display:none; opacity:0; position:absolute; left:260px; top:70px; width:260px; height:240px;">
                    <input id="live2d-toggle-proactive-vision" type="checkbox">
                </section>
            `;
            const animationPanel = document.getElementById('animation-settings-panel');
            animationPanel._collapse = () => {
                window.__calls.push({ type: 'collapse', id: 'animation-settings-panel' });
                animationPanel.style.display = 'none';
                animationPanel.style.opacity = '0';
            };
            const privacyPanel = document.getElementById('privacy-panel');
            privacyPanel._anchorElement = document.getElementById('privacy-mode-button');
            privacyPanel._collapse = () => {
                window.__calls.push({ type: 'collapse', id: 'privacy-panel' });
                privacyPanel.style.display = 'none';
                privacyPanel.style.opacity = '0';
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.__calls = [];
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = window.__calls;
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async () => true;
            director.closeSettingsPanel = async () => calls.push({ type: 'api:closeSettingsPanel' });
            director.ensureAvatarFloatingSettingsSidePanel = async (type) => {
                calls.push({ type: 'api:ensureSidePanel', panelType: type });
                return document.getElementById('privacy-panel');
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => {},
            };
            director.speakGuideLine = async () => {
                calls.push({ type: 'narration:done' });
                const animationPanel = document.getElementById('animation-settings-panel');
                animationPanel.style.display = 'flex';
                animationPanel.style.opacity = '1';
                calls.push({ type: 'sidepanel:visible-during-narration' });
            };

            await director.playAvatarFloatingScene({
                id: 'day4_privacy_mode',
                text: '这个是控制人家能不能看屏幕的‘终极防护开关’喵！把它关闭人家就能看到你的屏幕啦，要是开启它，前两天介绍的【屏幕分享】就统统失效、人家就绝对不会偷看哟~',
                voiceKey: 'avatar_floating_day4_privacy_mode',
                target: '#${p}-toggle-proactive-vision',
                cursorAction: 'move',
                operation: 'show-settings-sidepanel:interval-proactive-vision',
            }, 4, 4, 8);
            return {
                calls,
                settingsPopupDisplay: getComputedStyle(document.getElementById('live2d-popup-settings')).display,
                settingsPopupOpacity: getComputedStyle(document.getElementById('live2d-popup-settings')).opacity,
                settingsPopupPointerEvents: getComputedStyle(document.getElementById('live2d-popup-settings')).pointerEvents,
                animationPanelDisplay: getComputedStyle(document.getElementById('animation-settings-panel')).display,
                animationPanelOpacity: getComputedStyle(document.getElementById('animation-settings-panel')).opacity,
            };
        }
        """
    )
    calls = result["calls"]

    assert {"type": "api:ensureSidePanel", "panelType": "interval-proactive-vision"} not in calls
    assert [
        (call["key"], call["primaryId"], call["persistentId"])
        for call in calls
        if call["type"] == "highlight"
    ] == [
        ("day4_privacy_mode-privacy-mode-button", "privacy-mode-button", "live2d-btn-settings"),
    ]
    assert [
        (call["id"], call["durationMs"])
        for call in calls
        if call["type"] == "move"
    ] == [
        ("privacy-mode-button", 620),
    ]
    assert not any(call.get("primaryId") == "privacy-panel" for call in calls)
    assert not any(call.get("primaryId") == "live2d-toggle-proactive-vision" for call in calls)
    assert not any(call.get("primaryId") == "live2d-lock-icon" for call in calls)
    narration_index = calls.index({"type": "narration:done"})
    close_settings_indices = [
        index for index, call in enumerate(calls)
        if call == {"type": "api:closeSettingsPanel"}
    ]
    assert any(index > narration_index for index in close_settings_indices)
    assert {"type": "click"} not in calls
    assert result["settingsPopupDisplay"] == "none"
    assert result["settingsPopupOpacity"] == "0"
    assert result["settingsPopupPointerEvents"] == "none"
    assert result["animationPanelDisplay"] == "none"
    assert result["animationPanelOpacity"] == "0"


@pytest.mark.frontend
def test_day4_model_lock_highlights_lock_during_model_lock_line(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-lock-icon" style="display:none; position:absolute; left:80px; top:30px; width:44px; height:44px;"></button>
                <section id="privacy-panel" data-neko-sidepanel data-neko-sidepanel-type="interval-proactive-vision" style="display:flex; opacity:1; position:absolute; left:260px; top:70px; width:260px; height:240px;"></section>
            `;
            const privacyPanel = document.getElementById('privacy-panel');
            privacyPanel._collapse = () => {
                window.__calls.push({ type: 'collapse', id: 'privacy-panel' });
                privacyPanel.style.display = 'none';
                privacyPanel.style.opacity = '0';
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.__calls = [];
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = window.__calls;
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.waitForSceneDelay = async () => true;
            director.closeSettingsPanel = async () => calls.push({ type: 'api:closeSettingsPanel' });
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    primaryId: config.primary ? config.primary.id : null,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryDisplay: config.primary ? getComputedStyle(config.primary).display : null,
                });
                return {
                    persistent: config.persistent || null,
                    primary: config.primary || null,
                    secondary: config.secondary || null,
                };
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({
                    type: 'move',
                    id: element && element.id,
                    durationMs,
                    display: element ? getComputedStyle(element).display : null,
                });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => {},
            };
            director.speakGuideLine = async () => calls.push({ type: 'narration:done' });

            await director.playAvatarFloatingScene({
                id: 'day4_model_lock',
                text: '总是不小心触碰到、把我点歪吗？那就快把我牢牢固定在当前的位置吧！开启锁定后，我就哪儿也不去，乖乖在原地陪着你~',
                voiceKey: 'avatar_floating_day4_model_lock',
                target: '#${p}-lock-icon',
                cursorAction: 'move',
                cleanupBefore: true,
            }, 4, 5, 8);
            return calls;
        }
        """
    )

    assert {"type": "collapse", "id": "privacy-panel"} in result
    assert {
        "type": "message",
        "text": "总是不小心触碰到、把我点歪吗？那就快把我牢牢固定在当前的位置吧！开启锁定后，我就哪儿也不去，乖乖在原地陪着你~",
    } in result
    assert {
        "type": "highlight",
        "key": "day4_model_lock",
        "primaryId": "live2d-lock-icon",
        "persistentId": None,
        "primaryDisplay": "block",
    } in result
    assert {
        "type": "move",
        "id": "live2d-lock-icon",
        "durationMs": 760,
        "display": "block",
    } in result
    assert {"type": "wobble"} not in result
    assert {"type": "click"} not in result


@pytest.mark.frontend
def test_day4_model_lock_uses_active_model_lock_icon_when_prefix_fallback_is_live2d(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.getActiveModelType = () => 'vrm';
            document.body.innerHTML = `
                <button id="vrm-lock-icon" style="display:none; position:absolute; left:120px; top:60px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const calls = [];
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.waitForSceneDelay = async () => true;
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    primaryId: config.primary ? config.primary.id : null,
                    primaryDisplay: config.primary ? getComputedStyle(config.primary).display : null,
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({
                    type: 'move',
                    id: element && element.id,
                    durationMs,
                    display: element ? getComputedStyle(element).display : null,
                });
                return true;
            };
            director.cursor = {
                hasPosition: () => true,
                showAt: () => {},
                click: () => calls.push({ type: 'click' }),
                wobble: () => calls.push({ type: 'wobble' }),
                cancel: () => {},
                hide: () => {},
            };
            director.speakGuideLine = async () => calls.push({ type: 'narration:done' });

            await director.playAvatarFloatingScene({
                id: 'day4_model_lock',
                text: '总是不小心触碰到、把我点歪吗？那就快把我牢牢固定在当前的位置吧！开启锁定后，我就哪儿也不去，乖乖在原地陪着你~',
                voiceKey: 'avatar_floating_day4_model_lock',
                target: '#${p}-lock-icon',
                cursorAction: 'move',
                cleanupBefore: true,
            }, 4, 5, 8);
            return calls;
        }
        """
    )

    assert {
        "type": "highlight",
        "key": "day4_model_lock",
        "primaryId": "vrm-lock-icon",
        "primaryDisplay": "block",
    } in result
    assert {
        "type": "move",
        "id": "vrm-lock-icon",
        "durationMs": 760,
        "display": "block",
    } in result
    assert {"type": "wobble"} not in result


@pytest.mark.frontend
def test_avatar_floating_tutorial_marks_global_tutorial_mode_while_active(mock_page: Page):
    _bootstrap_page(
        mock_page,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            window.isInTutorial = false;
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.setTutorialTakingOver(true);
            const activeValue = window.isInTutorial;
            director.setTutorialTakingOver(false);
            return {
                activeValue,
                restoredValue: window.isInTutorial,
            };
        }
        """
    )

    assert result == {
        "activeValue": True,
        "restoredValue": False,
    }


@pytest.mark.frontend
def test_avatar_floating_director_fallback_enforcement_disables_proactive_and_galgame(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.appState = {
                proactiveChatEnabled: true,
                proactiveVisionEnabled: true,
            };
            window.proactiveChatEnabled = true;
            window.proactiveVisionEnabled = true;
            window.__fallbackGalgameRequests = [];
            window.__fallbackProactiveStops = [];
            window.reactChatWindowHost = {
                setGalgameModeEnabled: (enabled, options) => {
                    window.__fallbackGalgameRequests.push({ enabled, options });
                },
            };
            window.stopProactiveChatSchedule = () => { window.__fallbackProactiveStops.push('chat'); };
            window.stopProactiveVisionDuringSpeech = () => { window.__fallbackProactiveStops.push('vision'); };
            window.releaseProactiveVisionStream = () => { window.__fallbackProactiveStops.push('stream'); };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            window.NekoHomeTutorialFeatureController = null;
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.enforceAvatarFloatingGuideFeatureSuppression('fallback-test');
            return {
                galgameRequests: window.__fallbackGalgameRequests,
                proactiveStops: window.__fallbackProactiveStops,
                proactiveChatEnabled: window.proactiveChatEnabled,
                proactiveVisionEnabled: window.proactiveVisionEnabled,
                appStateProactiveChatEnabled: window.appState.proactiveChatEnabled,
                appStateProactiveVisionEnabled: window.appState.proactiveVisionEnabled,
            };
        }
        """
    )

    assert result["galgameRequests"] == [
        {
            "enabled": False,
            "options": {
                "persist": False,
                "suppressRefetch": True,
            },
        }
    ]
    assert result["proactiveStops"] == ["chat", "vision", "stream"]
    assert result["proactiveChatEnabled"] is False
    assert result["proactiveVisionEnabled"] is False
    assert result["appStateProactiveChatEnabled"] is False
    assert result["appStateProactiveVisionEnabled"] is False


@pytest.mark.frontend
def test_day2_first_scene_does_not_hide_cursor_before_chat_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-overlay">
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:320px; height:240px;"></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.cursor = {
                cancel: () => calls.push({ type: 'cancel' }),
                hide: () => calls.push({ type: 'hide' }),
                clearPosition: () => calls.push({ type: 'clearPosition' }),
                showAt: (x, y) => calls.push({ type: 'showAt', x, y }),
                wobble: () => calls.push({ type: 'wobble' }),
                hasPosition: () => true,
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};

	            await director.playAvatarFloatingScene({
	                id: 'day2_intro_context',
	                text: 'intro',
	                voiceKey: 'avatar_floating_day2_intro',
	                target: 'chat-window',
	                cursorAction: 'move',
	            }, 2, 0, 6);

            return calls;
        }
        """
    )

    assert result[0]["type"] == "cancel"
    assert all(call["type"] != "hide" for call in result)
    assert all(call["type"] != "clearPosition" for call in result)
    assert any(call["type"] == "showAt" for call in result)


@pytest.mark.frontend
def test_day2_personalization_detail_clicks_character_settings_then_ellipses_sidebar(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="character-settings-button" style="position:absolute; left:100px; top:80px; width:130px; height:44px;"></button>
                <section id="character-settings-panel" data-neko-sidepanel data-neko-sidepanel-type="character-settings" style="position:absolute; left:260px; top:70px; width:260px; height:380px;"></section>
            `;
            document.getElementById('character-settings-panel')._anchorElement = document.getElementById('character-settings-button');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            let releaseNarration;
            director.appendGuideChatMessage = (text) => calls.push({ type: 'message', text });
            director.applyGuideEmotion = (emotion) => calls.push({ type: 'emotion', emotion });
            director.enableInterrupts = () => calls.push({ type: 'interrupts' });
            director.openSettingsPanel = async () => {
                calls.push({ type: 'api:openSettingsPanel' });
                return true;
            };
            director.ensureAvatarFloatingSettingsSidePanel = async (type) => {
                calls.push({ type: 'api:ensureSidePanel', panelType: type });
                return document.getElementById('character-settings-panel');
            };
            director.collapseCharacterSettingsSidePanel = () => {
                calls.push({ type: 'api:collapseCharacterSettingsSidePanel' });
            };
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return config;
            };
            director.overlay.clearActionSpotlight = () => calls.push({ type: 'clearActionSpotlight' });
            director.overlay.clearPersistentSpotlight = () => calls.push({ type: 'clearPersistentSpotlight' });
            director.moveCursorToElement = async (element, durationMs) => {
                calls.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.clickCursorAndWait = async (durationMs) => {
                calls.push({ type: 'click:start', durationMs });
                await new Promise((resolve) => window.setTimeout(resolve, 12));
                calls.push({ type: 'click:done', durationMs });
            };
            director.cursor = {
                hasPosition: () => true,
                runPauseAwareEllipse: async (x, y, radiusX, radiusY) => {
                    calls.push({ type: 'ellipse', x, y, radiusX, radiusY });
                    return true;
                },
                cancel: () => calls.push({ type: 'cancel' }),
                hide: () => {},
            };
            director.speakGuideLine = () => new Promise((resolve) => {
                releaseNarration = () => {
                    calls.push({ type: 'narration:done' });
                    releaseNarration = null;
                    resolve();
                };
                window.setTimeout(() => {
                    if (releaseNarration) {
                        releaseNarration();
                    }
                }, 20);
            });

            await director.playAvatarFloatingScene({
                id: 'day2_personalization_detail',
                text: '不管是说话的温度、相处的小脾气，还是我每天那些细腻的小心思，都可以一点一点调成你喜欢的样子。',
                voiceKey: 'takeover_settings_peek_detail',
                target: '#${p}-menu-character',
                cursorAction: 'click',
                operation: 'day2-settings-detail',
            }, 2, 2, 7);
            return calls;
        }
        """
    )

    highlights = [
        (call["key"], call["primaryId"])
        for call in result
        if call["type"] == "highlight"
    ]
    assert highlights[:2] == [
        ("day2_personalization_detail-character-settings-button", "character-settings-button"),
        ("day2_personalization_detail-character-settings-panel", "character-settings-panel"),
    ]
    assert result.index({"type": "move", "id": "character-settings-button", "durationMs": 620}) < result.index({
        "type": "click:start",
        "durationMs": 420,
    })
    assert result.index({"type": "click:done", "durationMs": 420}) < result.index({
        "type": "api:ensureSidePanel",
        "panelType": "character-settings",
    })
    assert result.index({
        "type": "move",
        "id": "character-settings-panel",
        "durationMs": 620,
    }) < next(index for index, call in enumerate(result) if call["type"] == "ellipse")
    assert any(call["type"] == "ellipse" and call["radiusX"] > 0 and call["radiusY"] > 0 for call in result)
    assert result.index({"type": "narration:done"}) < result.index({
        "type": "api:collapseCharacterSettingsSidePanel",
    })
    assert result.index({"type": "narration:done"}) < result.index({"type": "clearActionSpotlight"})
    assert result.index({"type": "narration:done"}) < result.index({"type": "clearPersistentSpotlight"})


@pytest.mark.frontend
def test_day2_proactive_chat_highlights_only_proactive_toggle(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="character-settings-button" style="position:absolute; left:100px; top:80px; width:130px; height:44px;"></button>
                <button id="proactive-toggle" style="position:absolute; left:280px; top:180px; width:150px; height:42px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.getDay5CharacterSettingsButtonTarget = () => document.getElementById('character-settings-button');
            director.prepareAvatarFloatingScene = async () => {};
            director.resolveAvatarFloatingPersistent = async () => null;
            director.resolveAvatarFloatingTarget = async () => document.getElementById('proactive-toggle');
            director.resolveAvatarFloatingSceneText = (scene) => scene.text || '';
            director.speakGuideLine = async () => calls.push({ type: 'narration:done' });
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.moveAvatarFloatingCursor = async () => {};
            director.runAvatarFloatingSceneOperation = async () => true;
            director.applyGuideHighlights = (config) => {
                calls.push({
                    type: 'highlight',
                    key: config.key,
                    persistentId: config.persistent ? config.persistent.id : null,
                    primaryId: config.primary ? config.primary.id : null,
                });
                return config;
            };

            await director.playAvatarFloatingScene({
                id: 'day2_proactive_chat',
                text: '这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。',
                voiceKey: 'takeover_settings_peek_detail_part_2',
                target: '#${p}-toggle-proactive-chat',
                cursorAction: 'move',
            }, 2, 3, 7);

            return calls;
        }
        """
    )

    proactive_highlight = next(
        call for call in result
        if call["type"] == "highlight" and call["key"] == "day2_proactive_chat"
    )
    assert proactive_highlight == {
        "type": "highlight",
        "key": "day2_proactive_chat",
        "persistentId": None,
        "primaryId": "proactive-toggle",
    }
    assert result.index(proactive_highlight) < result.index({"type": "narration:done"})


@pytest.mark.frontend
def test_day2_proactive_chat_closes_settings_panel_after_line(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="proactive-toggle" style="position:absolute; left:280px; top:180px; width:150px; height:42px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.prepareAvatarFloatingScene = async () => {};
            director.resolveAvatarFloatingPersistent = async () => null;
            director.resolveAvatarFloatingTarget = async () => document.getElementById('proactive-toggle');
            director.resolveAvatarFloatingSceneText = (scene) => scene.text || '';
            director.speakGuideLine = async () => calls.push({ type: 'narration:done' });
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.moveAvatarFloatingCursor = async () => {};
            director.runAvatarFloatingSceneOperation = async () => true;
            director.forceHideManagedPanel = (panelId) => calls.push({ type: 'forceHideManagedPanel', panelId });
            director.collapseAvatarFloatingSidePanelsExcept = (panel) => calls.push({
                type: 'collapseSidePanelsExcept',
                hasPanel: !!panel,
            });

            await director.playAvatarFloatingScene({
                id: 'day2_proactive_chat',
                text: '这个小按钮也很重要哦，只要你轻轻点一下，我就能在合适的时候跑过去找你啦。',
                voiceKey: 'takeover_settings_peek_detail_part_2',
                target: '#${p}-toggle-proactive-chat',
                cursorAction: 'move',
            }, 2, 3, 7);

            return calls;
        }
        """
    )

    assert result.index({"type": "narration:done"}) < result.index({
        "type": "forceHideManagedPanel",
        "panelId": "settings",
    })
    assert result.index({"type": "narration:done"}) < result.index({
        "type": "collapseSidePanelsExcept",
        "hasPanel": False,
    })


@pytest.mark.frontend
def test_day2_personalization_space_opens_settings_on_cursor_click_without_character_sidebar(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-settings" style="position:absolute; left:20px; top:30px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.openSettingsPanel = async () => {
                calls.push({ type: 'api:openSettingsPanel' });
                return true;
            };
            director.ensureCharacterSettingsSidePanelVisible = async () => {
                calls.push({ type: 'api:ensureCharacterSettingsSidePanelVisible' });
                return true;
            };
            director.speakGuideLine = async () => {};
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => calls.push({ type: 'message' });
            director.applyGuideEmotion = () => {};
            director.applyGuideHighlights = () => {};
            director.moveAvatarFloatingCursor = async (scene, target, secondary, previousSceneId, options) => {
                calls.push({ type: 'cursor:move', id: target && target.id });
                calls.push({ type: 'cursor:click:start' });
                const operationPromise = options && typeof options.onClickStart === 'function'
                    ? options.onClickStart()
                    : Promise.resolve();
                calls.push({ type: 'cursor:click:done' });
                await operationPromise;
            };

            await director.playAvatarFloatingScene({
                id: 'day2_personalization_space',
                text: '在这个只属于我们的小空间里，你可以由着自己的心意，慢慢描绘出最希望能一直陪着你的那个我。',
                voiceKey: 'takeover_settings_peek_intro',
                target: '#${p}-btn-settings',
                cursorAction: 'click',
                operation: 'day2-open-settings-personalization',
            }, 2, 1, 7);
            return calls;
        }
        """
    )

    assert {"type": "api:openSettingsPanel"} in result
    assert {"type": "api:ensureCharacterSettingsSidePanelVisible"} not in result
    assert result.index({"type": "message"}) < result.index({"type": "cursor:click:start"})
    assert result.index({"type": "cursor:click:start"}) < result.index({"type": "api:openSettingsPanel"})


@pytest.mark.frontend
def test_day3_to_day7_first_scene_does_not_hide_cursor_before_visible_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-overlay">
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:320px; height:240px;">
                        <div class="composer-panel" style="position:absolute; left:20px; top:160px; width:260px; height:56px;"></div>
                        <button class="send-button-circle compact-input-tool-toggle" style="position:absolute; left:260px; top:164px; width:42px; height:42px;"></button>
                    </div>
                </div>
            `;
        """,
        script_names=(
            "tutorial/yui-guide/days/day3-interaction-guide.js",
            "tutorial/yui-guide/days/day4-companion-guide.js",
            "tutorial/yui-guide/days/day5-personalization-guide.js",
            "tutorial/yui-guide/days/day6-agent-guide.js",
            "tutorial/yui-guide/days/day7-graduation-guide.js",
            "tutorial/yui-guide/overlay.js",
            "tutorial/yui-guide/director.js",
        ),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const outcomes = [];
            for (const day of [3, 4, 5, 6, 7]) {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const scene = window.YuiGuideDailyGuides[day].round.scenes[0];
                const calls = [];
                let cursorHasPosition = true;
                director.cursor = {
                    cancel: () => {
                        cursorHasPosition = false;
                        calls.push({ type: 'cancel' });
                    },
                    hide: () => calls.push({ type: 'hide' }),
                    clearPosition: () => {
                        cursorHasPosition = false;
                        calls.push({ type: 'clearPosition' });
                    },
                    showAt: (x, y) => {
                        cursorHasPosition = true;
                        calls.push({ type: 'showAt', x, y });
                    },
                    moveToRect: async (rect) => {
                        cursorHasPosition = true;
                        calls.push({ type: 'moveToRect', x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 });
                        return true;
                    },
                    click: () => calls.push({ type: 'click' }),
                    wobble: () => calls.push({ type: 'wobble' }),
                    hasPosition: () => cursorHasPosition,
                };
                director.speakGuideLine = async () => null;
                director.waitForSceneDelay = async () => true;
                director.appendGuideChatMessage = () => {};
                director.applyGuideEmotion = () => {};
                director.prepareAvatarFloatingScene = async () => {};
                director.runAvatarFloatingSceneOperation = async () => {};

                await director.playAvatarFloatingScene(scene, day, 0, window.YuiGuideDailyGuides[day].round.scenes.length);

                outcomes.push({
                    day,
                    calls,
                });
            }
            return outcomes;
        }
        """
    )

    for outcome in result:
        calls = outcome["calls"]
        assert calls[0]["type"] == "cancel"
        assert all(call["type"] != "hide" for call in calls), outcome
        assert all(call["type"] != "clearPosition" for call in calls), outcome
        assert any(call["type"] == "showAt" for call in calls), outcome


@pytest.mark.frontend
def test_day2_wrap_intro_cursor_start_prefers_previous_screen_button_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-overlay">
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:320px; height:240px;"></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.avatarFloatingSceneCursorAnchorPoints = {
                day2_screen_entry_invite: { x: 720, y: 520 },
            };
            const chatTarget = document.getElementById('react-chat-window-shell');
            return director.resolveAvatarFloatingCursorStartPoint(
                { id: 'day2_wrap_intro' },
                [chatTarget]
            );
        }
        """
    )

    assert result == {"x": 720, "y": 520}


@pytest.mark.frontend
def test_day2_wrap_intro_externalized_cursor_target_is_not_reissued_after_cleanup(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.currentSceneId = 'day2_screen_entry_invite';
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => calls.push({ type: 'spotlight', kind }),
                setExternalizedChatCursor: (kind, options) => calls.push({
                    type: 'cursor',
                    kind,
                    effect: options && options.effect,
                }),
            };
            director.cursor.showAt(720, 520);
            director.prepareAvatarFloatingScene = async () => true;
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};

	            await director.playAvatarFloatingScene({
	                id: 'day2_wrap_intro',
	                text: '今天的教程到这里就结束了呢。',
	                voiceKey: 'avatar_floating_day2_wrap_intro',
	                target: 'chat-window',
	                cursorAction: 'move',
	                cursorMoveDurationMs: 900,
	                operation: 'cleanup',
	            }, 2, 4, 6);

            return calls;
        }
        """
    )

    window_cursor_calls = [
        call for call in result
        if call["type"] == "cursor" and call["kind"] == "window"
    ]
    assert window_cursor_calls == [
        {"type": "cursor", "kind": "window", "effect": "move"}
    ]


@pytest.mark.frontend
def test_day2_screen_entry_uses_externalized_intro_cursor_anchor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
	            window.localStorage.setItem('neko_yui_guide_external_chat_cursor_screen_point_v1', JSON.stringify({
	                x: 640,
	                y: 430,
	                kind: 'window',
	                effect: 'move',
	                source: 'external-chat',
	                at: Date.now(),
	            }));
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" style="display:none;">
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:320px; height:240px;"></div>
                </div>
                <button id="live2d-btn-screen" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return director.resolveAvatarFloatingCursorStartPoint(
                { id: 'day2_screen_entry' },
                [document.getElementById('live2d-btn-screen')],
                'day2_intro_context'
            );
        }
        """
    )

    assert result == {"x": 540, "y": 380}


@pytest.mark.frontend
def test_day2_externalized_intro_records_visible_cursor_anchor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" style="display:none;"></div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const cursorKinds = [];
            const spotlightKinds = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => spotlightKinds.push(kind),
                setExternalizedChatCursor: (kind) => {
                    cursorKinds.push(kind);
                    if (kind) {
	                        window.localStorage.setItem('neko_yui_guide_external_chat_cursor_screen_point_v1', JSON.stringify({
	                            x: 640,
	                            y: 430,
	                            kind,
	                            effect: 'move',
	                            source: 'external-chat',
	                            at: Date.now(),
	                        }));
                    }
                },
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.resolveAvatarFloatingPersistent = async () => null;
            director.resolveAvatarFloatingTarget = async () => null;
            director.runAvatarFloatingSceneOperation = async () => {};

            await director.playAvatarFloatingScene({
	                id: 'day2_intro_context',
	                text: 'intro',
	                voiceKey: 'avatar_floating_day2_intro',
	                target: 'chat-window',
	                cursorAction: 'move',
	            }, 2, 0, 6);

            return {
                cursorKinds,
                spotlightKinds,
                anchor: director.avatarFloatingSceneCursorAnchorPoints.day2_intro_context,
            };
        }
        """
    )

    assert result["cursorKinds"] == ["input"]
    assert result["spotlightKinds"] == ["input", ""]
    assert result["anchor"] == {"x": 540, "y": 380}


@pytest.mark.frontend
def test_day2_externalized_intro_to_screen_entry_preserves_cursor_visibility(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" style="display:none;"></div>
                <button id="live2d-btn-screen" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const cursorKinds = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: () => {},
                setExternalizedChatCursor: (kind) => {
                    cursorKinds.push(kind);
                    if (kind) {
                        window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                            detail: {
	                                x: 640,
	                                y: 430,
	                                kind,
	                                effect: 'move',
	                                source: 'external-chat',
	                                timestamp: Date.now(),
	                            },
                        }));
                    }
                },
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.resolveAvatarFloatingPersistent = async () => null;
            director.runAvatarFloatingSceneOperation = async () => {};

            await director.playAvatarFloatingScene({
	                id: 'day2_intro_context',
	                text: 'intro',
	                voiceKey: 'avatar_floating_day2_intro',
	                target: 'chat-window',
	                cursorAction: 'move',
	            }, 2, 0, 6);

            director.resolveAvatarFloatingTarget = async () => document.getElementById('live2d-btn-screen');
            await director.playAvatarFloatingScene({
	                id: 'day2_screen_entry',
	                text: 'screen',
	                voiceKey: 'avatar_floating_day2_screen_entry_intro',
	                target: '#${p}-btn-screen',
	                cursorAction: 'move',
	            }, 2, 1, 6);

            const firstInputIndex = cursorKinds.indexOf('input');
            return {
                cursorKinds,
                afterInput: firstInputIndex >= 0 ? cursorKinds.slice(firstInputIndex + 1) : [],
            };
        }
        """
    )

    assert "input" in result["cursorKinds"]
    assert "" not in result["afterInput"]


@pytest.mark.frontend
def test_externalized_chat_cursor_reports_anchor_back_to_home(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            const relays = [];
            const updates = [];
            window.__externalChatAnchorRelays = relays;
            window.__externalChatOverlayUpdates = updates;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    updates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
                relayToPet: (payload) => relays.push(payload),
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            document.body.innerHTML = `
                <div id="react-chat-window-shell" style="position:fixed; left:600px; top:400px; width:240px; height:160px;"></div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_cursor',
                    kind: 'window',
                    effect: 'wobble',
                    effectDurationMs: 2000,
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 80));
            const raw = window.localStorage.getItem('neko_yui_guide_external_chat_cursor_screen_point_v1');
            return {
                relays: window.__externalChatAnchorRelays,
                stored: raw ? JSON.parse(raw) : null,
                updates: window.__externalChatOverlayUpdates,
            };
        }
        """
    )

    anchorRelays = [
        relay for relay in result["relays"]
        if relay.get("action") == "yui_guide_chat_cursor_anchor"
    ]
    assert anchorRelays
    assert anchorRelays[-1]["x"] == 820
    assert anchorRelays[-1]["y"] == 530
    assert anchorRelays[-1]["kind"] == "window"
    assert anchorRelays[-1]["effect"] == ""
    assert anchorRelays[-1]["effectDurationMs"] == 0
    assert anchorRelays[-1]["source"] == "external-chat"
    assert result["stored"]["x"] == 820
    assert result["stored"]["y"] == 530
    assert result["stored"]["effect"] == ""
    assert result["stored"]["effectDurationMs"] == 0
    assert any(
        update.get("payload", {}).get("cursor", {}).get("visible") is True
        and update["payload"]["cursor"]["x"] == 820
        and update["payload"]["cursor"]["y"] == 530
        and update["payload"]["cursor"].get("effect") == "wobble"
        and update["payload"]["cursor"].get("effectDurationMs") == 2000
        for update in result["updates"]
    )


@pytest.mark.frontend
def test_externalized_chat_spotlight_refresh_does_not_override_active_cursor_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push({
                        at: Date.now(),
                        payload,
                    });
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
                relayToPet: () => {},
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:absolute; left:260px; top:164px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const sendRelay = (payload) => window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    tutorialRunId: 'test-run',
                    timestamp: Date.now(),
                    ...payload,
                },
            }, '*');

            sendRelay({
                action: 'yui_guide_set_chat_spotlight',
                kind: 'tool-toggle',
            });
            await new Promise((resolve) => setTimeout(resolve, 30));

            sendRelay({
                action: 'yui_guide_set_chat_cursor',
                kind: 'tool-toggle',
                effect: 'click',
                effectDurationMs: 420,
            });
            await new Promise((resolve) => setTimeout(resolve, 180));

            const updates = window.__externalChatOverlayUpdates;
            const clickIndex = updates.findIndex((entry) => (
                entry.payload
                && entry.payload.payload
                && entry.payload.payload.cursor
                && entry.payload.payload.cursor.effect === 'click'
            ));
            const clickAt = clickIndex >= 0 ? updates[clickIndex].at : 0;
            const duringClick = clickIndex >= 0
                ? updates.slice(clickIndex + 1).filter((entry) => entry.at - clickAt < 420)
                : [];
            return {
                clickIndex,
                duringClick,
                updates,
            };
        }
        """
    )

    assert result["clickIndex"] >= 0
    assert all(
        (
            not entry.get("payload", {}).get("payload", {}).get("cursor")
            or entry["payload"]["payload"]["cursor"].get("effect") == "click"
        )
        for entry in result["duringClick"]
    )


@pytest.mark.frontend
def test_externalized_chat_input_cursor_without_effect_shows_without_pc_move(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:fixed; left:600px; top:400px; width:320px; height:56px;"
                    ></div>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_cursor',
                    kind: 'input',
                    effect: '',
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 80));
            const cursorUpdates = window.__externalChatOverlayUpdates
                .map((update) => update && update.payload && update.payload.cursor)
                .filter(Boolean);
            return cursorUpdates[cursorUpdates.length - 1] || null;
        }
        """
    )

    assert result["visible"] is True
    assert result["x"] == 860
    assert result["y"] == 478
    assert result["durationMs"] == 0
    assert result.get("effect", "") == ""


@pytest.mark.frontend
def test_externalized_chat_cursor_explicit_duration_overrides_handoff_speed(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            window.localStorage.setItem('neko_yui_guide_external_chat_cursor_screen_point_v1', JSON.stringify({
                x: 700,
                y: 460,
                kind: 'input',
                effect: '',
                source: 'home-director-handoff',
                at: Date.now(),
            }));
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:fixed; left:600px; top:400px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_cursor',
                    kind: 'tool-toggle',
                    effect: 'move',
                    durationMs: 1480,
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 80));
            const cursorUpdates = window.__externalChatOverlayUpdates
                .map((update) => update && update.payload && update.payload.cursor)
                .filter(Boolean);
            return cursorUpdates[cursorUpdates.length - 1] || null;
        }
        """
    )

    assert result["visible"] is True
    assert result["x"] == 721
    assert result["y"] == 471
    assert result["effect"] == "move"
    assert result["durationMs"] == 1480


@pytest.mark.frontend
def test_externalized_chat_cursor_anchor_reports_after_pc_move_duration(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__externalChatOverlayUpdates = [];
            window.__externalChatAnchorRelays = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
                relayToPet: (payload) => window.__externalChatAnchorRelays.push(payload),
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:fixed; left:600px; top:400px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_cursor',
                    kind: 'tool-toggle',
                    effect: 'move',
                    durationMs: 120,
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 60));
            const beforeMoveSettled = window.__externalChatAnchorRelays.filter(
                (relay) => relay && relay.action === 'yui_guide_chat_cursor_anchor'
            );
            await new Promise((resolve) => setTimeout(resolve, 120));
            const afterMoveSettled = window.__externalChatAnchorRelays.filter(
                (relay) => relay && relay.action === 'yui_guide_chat_cursor_anchor'
            );
            const raw = window.localStorage.getItem('neko_yui_guide_external_chat_cursor_screen_point_v1');
            return {
                beforeMoveSettled,
                afterMoveSettled,
                stored: raw ? JSON.parse(raw) : null,
            };
        }
        """
    )

    assert result["beforeMoveSettled"] == []
    assert result["afterMoveSettled"]
    assert result["afterMoveSettled"][-1]["kind"] == "tool-toggle"
    assert result["afterMoveSettled"][-1]["effect"] == ""
    assert result["afterMoveSettled"][-1]["settled"] is True
    assert result["stored"]["kind"] == "tool-toggle"
    assert result["stored"]["settled"] is True


@pytest.mark.frontend
def test_home_director_receives_externalized_chat_cursor_anchor_event(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" style="display:none;"></div>
                <button id="live2d-btn-screen" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.currentSceneId = 'day2_intro_context';
            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
	                    x: 640,
	                    y: 430,
	                    kind: 'window',
	                    effect: 'move',
	                    source: 'external-chat',
	                    timestamp: Date.now(),
	                },
            }));
            return {
                anchor: director.avatarFloatingSceneCursorAnchorPoints.day2_intro_context,
                start: director.resolveAvatarFloatingCursorStartPoint(
                    { id: 'day2_screen_entry' },
                    [document.getElementById('live2d-btn-screen')],
                    'day2_intro_context'
                ),
            };
        }
        """
    )

    assert result["anchor"] == {"x": 540, "y": 380}
    assert result["start"] == {"x": 540, "y": 380}


@pytest.mark.frontend
def test_home_director_owns_pc_cursor_for_externalized_chat_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.currentSceneId = 'intro_basic';
            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
                    x: 640,
                    y: 430,
                    kind: 'input',
                    effect: 'wobble',
                    effectDurationMs: 2000,
                    source: 'external-chat',
                    timestamp: Date.now(),
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            const cursorShell = document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            return {
                currentPosition: director.overlay.getCursorPosition(),
                visible: director.overlay.isCursorVisible(),
                domExists: !!cursorShell,
                updates: window.__pcOverlayUpdates,
            };
        }
        """
    )

    assert result["currentPosition"] == {"x": 540, "y": 380}
    assert result["visible"] is True
    assert result["domExists"] is False
    assert any(
        update["payload"]["cursor"]["visible"] is True
        and update["payload"]["cursor"]["x"] == 640
        and update["payload"]["cursor"]["y"] == 430
        and update["payload"]["cursor"].get("effect") == "wobble"
        and update["payload"]["cursor"].get("effectDurationMs") == 2000
        for update in result["updates"]
    )


@pytest.mark.frontend
def test_settled_externalized_cursor_anchor_refreshes_home_pc_cursor_cache(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.currentSceneId = 'day6_wrap_cleanup';
            director.cursor.showAt(680, 100);
            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
                    x: 640,
                    y: 430,
                    kind: 'input',
                    effect: '',
                    source: 'external-chat',
                    settled: true,
                    timestamp: Date.now(),
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            director.overlay.clearActionSpotlight();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return window.__pcOverlayUpdates.map((update) => update.payload);
        }
        """
    )

    assert result[-1]["cursor"]["visible"] is True
    assert result[-1]["cursor"]["x"] == 640
    assert result[-1]["cursor"]["y"] == 430


@pytest.mark.frontend
def test_home_spotlight_refresh_does_not_replay_stale_cursor_while_externalized_chat_owns_cursor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.currentSceneId = 'day6_wrap_cleanup';
            director.cursor.showAt(680, 100);
            await new Promise((resolve) => setTimeout(resolve, 0));
            director.overlay.setPcCursorOutputSuppressed(true);
            director.overlay.clearActionSpotlight();
            await new Promise((resolve) => setTimeout(resolve, 0));
            return window.__pcOverlayUpdates.map((update) => update.payload);
        }
        """
    )

    assert result[0]["cursor"]["visible"] is True
    assert result[0]["cursor"]["x"] == 780
    assert result[0]["cursor"]["y"] == 150
    assert "cursor" not in result[-1]


@pytest.mark.frontend
def test_home_petal_update_does_not_replay_stale_cursor_while_externalized_chat_owns_cursor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.currentSceneId = 'day6_wrap_cleanup';
            director.cursor.showAt(680, 100);
            await new Promise((resolve) => setTimeout(resolve, 0));
            director.overlay.setPcCursorOutputSuppressed(true);
            director.overlay.playPetalTransition({ x: 320, y: 240 }, { durationMs: 500 });
            await new Promise((resolve) => setTimeout(resolve, 0));
            return window.__pcOverlayUpdates.map((update) => update.payload);
        }
        """
    )

    assert result[0]["cursor"]["visible"] is True
    assert result[0]["cursor"]["x"] == 780
    assert result[0]["cursor"]["y"] == 150
    assert result[-1]["petal"]["durationMs"] == 500
    assert "cursor" not in result[-1]


@pytest.mark.frontend
def test_home_director_ignores_click_effect_from_externalized_chat_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const clicks = [];
            director.currentSceneId = 'day3_avatar_tools';
            director.clickCursorAndWait = async (durationMs) => {
                clicks.push(durationMs);
                return true;
            };
            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
                    x: 640,
                    y: 430,
                    kind: 'tool-toggle',
                    effect: 'click',
                    effectDurationMs: 420,
                    source: 'external-chat',
                    timestamp: Date.now(),
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                clicks,
                currentPosition: director.overlay.getCursorPosition(),
            };
        }
        """
    )

    assert result["clicks"] == []
    assert result["currentPosition"] == {"x": 540, "y": 380}


@pytest.mark.frontend
def test_home_director_smoothly_moves_hidden_cursor_to_externalized_chat_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.currentSceneId = 'day2_wrap_intro';
            director.overlay.getCursorPosition = () => ({ x: 242, y: 202 });
            director.cursor = {
                hasPosition: () => true,
                hasVisiblePosition: () => false,
                showAt: (x, y) => calls.push({ type: 'showAt', x, y }),
                moveToPoint: (x, y, options) => {
                    calls.push({
                        type: 'moveToPoint',
                        x,
                        y,
                        durationMs: options && options.durationMs,
                    });
                    return Promise.resolve(true);
                },
                wobble: () => calls.push({ type: 'wobble' }),
            };

            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
	                    x: 640,
	                    y: 430,
	                    kind: 'window',
	                    effect: 'move',
	                    source: 'external-chat',
	                    timestamp: Date.now(),
	                },
            }));
            await Promise.resolve();

            return {
                calls,
                anchor: director.avatarFloatingSceneCursorAnchorPoints.day2_wrap_intro,
            };
        }
        """
    )

    assert result["anchor"] == {"x": 540, "y": 380}
    assert result["calls"][0] == {"type": "showAt", "x": 242, "y": 202}
    assert result["calls"][1]["type"] == "moveToPoint"
    assert result["calls"][1]["x"] == 540
    assert result["calls"][1]["y"] == 380
    assert result["calls"][1]["durationMs"] > 0
    assert len(result["calls"]) == 2


@pytest.mark.frontend
def test_pc_overlay_suppresses_dom_cursor_on_first_show(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.cursor.showAt(320, 240);
            const cursorShell = document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            return {
                domExists: !!cursorShell,
                bodyActive: document.body.classList.contains('yui-guide-ghost-cursor-active'),
                updates: window.__pcOverlayUpdates,
            };
        }
        """
    )

    assert result["domExists"] is False
    assert result["bodyActive"] is False
    assert result["updates"][0]["payload"]["cursor"]["visible"] is True


@pytest.mark.frontend
def test_tutorial_skip_and_angry_exit_do_not_start_new_user_icebreaker(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.__icebreakerFetchCount = 0;",
        fetch_js="""
            window.__icebreakerFetchCount += 1;
            return jsonResponse({}, 200);
        """,
        script_names=("tutorial/icebreaker/new-user-icebreaker.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.dispatchEvent(new CustomEvent('neko:avatar-floating-guide-skip', {
                detail: {
                    day: 1,
                    endState: {
                        day: 1,
                        ended: true,
                        outcome: 'skip',
                        rawReason: 'angry_exit',
                        isAngryExit: true,
                    },
                },
            }));
            window.dispatchEvent(new CustomEvent('neko:tutorial-skipped', {
                detail: {
                    page: 'home',
                    day: 1,
                    reason: 'skip',
                },
            }));
            window.dispatchEvent(new CustomEvent('neko:tutorial-completed', {
                detail: {
                    page: 'home',
                    day: 1,
                    endState: {
                        day: 1,
                        ended: true,
                        outcome: 'skip',
                        rawReason: 'angry_exit',
                        isAngryExit: true,
                    },
                },
            }));
            await new Promise((resolve) => setTimeout(resolve, 700));
            return {
                fetchCount: window.__icebreakerFetchCount,
                activeSession: window.newUserIcebreaker.getActiveSession(),
            };
        }
        """
    )

    assert result == {"fetchCount": 0, "activeSession": None}


@pytest.mark.frontend
def test_yui_overlay_lifecycle_epoch_blocks_late_dom_recreation(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.isInTutorial = true;",
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        () => {
            const staleOverlay = new window.YuiGuideOverlay(document);
            staleOverlay.showBubble('active tutorial');
            const initiallyCreated = !!document.getElementById('yui-guide-overlay');

            staleOverlay.destroy();
            staleOverlay.showBubble('late callback');
            const recreatedByStaleInstance = !!document.getElementById('yui-guide-overlay');

            const nextOverlay = new window.YuiGuideOverlay(document);
            nextOverlay.showBubble('next tutorial');
            const recreatedByNextInstance = !!document.getElementById('yui-guide-overlay');
            const nextRoot = document.getElementById('yui-guide-overlay');
            const nextEpoch = window.__NEKO_YUI_GUIDE_OVERLAY_LIFECYCLE_EPOCH__;
            staleOverlay.destroy();
            staleOverlay.showBubble('older callback after next tutorial');
            const nextRootPreserved = document.getElementById('yui-guide-overlay') === nextRoot;
            const nextContentPreserved = document.querySelector('.yui-guide-bubble-body')?.textContent === 'next tutorial';
            const nextEpochPreserved = window.__NEKO_YUI_GUIDE_OVERLAY_LIFECYCLE_EPOCH__ === nextEpoch;
            return {
                initiallyCreated,
                recreatedByStaleInstance,
                recreatedByNextInstance,
                nextRootPreserved,
                nextContentPreserved,
                nextEpochPreserved,
            };
        }
        """
    )

    assert result == {
        "initiallyCreated": True,
        "recreatedByStaleInstance": False,
        "recreatedByNextInstance": True,
        "nextRootPreserved": True,
        "nextContentPreserved": True,
        "nextEpochPreserved": True,
    }


@pytest.mark.frontend
def test_pc_overlay_move_with_existing_position_never_reveals_dom_cursor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            overlay.cursorPosition = { x: 242, y: 202 };
            overlay.cursorVisible = true;
            const movePromise = overlay.moveCursorTo(320, 220, { durationMs: 900 });
            await new Promise((resolve) => setTimeout(resolve, 120));
            const cursorShell = document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            await movePromise;
            return {
                domExists: !!cursorShell,
                domVisibleClass: cursorShell ? cursorShell.classList.contains('is-visible') : false,
                bodyActive: document.body.classList.contains('yui-guide-ghost-cursor-active'),
                cursorVisible: overlay.isCursorVisible(),
                updates: window.__pcOverlayUpdates,
            };
        }
        """
    )

    assert result["domExists"] is False
    assert result["domVisibleClass"] is False
    assert result["bodyActive"] is False
    assert result["cursorVisible"] is True
    assert any(
        update.get("payload", {}).get("cursor", {}).get("x") == 420
        and update["payload"]["cursor"].get("y") == 270
        for update in result["updates"]
    )


@pytest.mark.frontend
def test_pc_overlay_cursor_is_hidden_before_plugin_dashboard_handoff(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.overlay.isPcOverlayActive = () => true;
            director.cursor = {
                cancel: () => calls.push('cancel'),
                hide: () => calls.push('hide'),
                clearPosition: () => calls.push('clearPosition'),
            };
            director.hideHomeCursorForExternalizedChat();
            return calls;
        }
        """
    )

    assert result == ["cancel", "hide", "clearPosition"]


@pytest.mark.frontend
def test_pc_overlay_updates_keep_cursor_and_spotlights_in_same_payload(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <button id="settings-button" style="position:absolute; left:80px; top:90px; width:48px; height:48px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            const settingsButton = document.getElementById('settings-button');
            overlay.activateSpotlight(settingsButton);
            overlay.showCursorAt(104, 114);
            overlay.refreshSpotlight();
            await Promise.resolve();
            return window.__pcOverlayUpdates.map((update) => update.payload);
        }
        """
    )

    cursor_payload = next(
        payload for payload in result
        if payload.get("cursor", {}).get("visible") is True
    )
    assert cursor_payload["spotlights"][0]["kind"] == "primary"
    assert cursor_payload["cursor"]["x"] == 204
    assert cursor_payload["cursor"]["y"] == 164

    latest_payload = result[-1]
    assert latest_payload["spotlights"][0]["kind"] == "primary"
    assert latest_payload["cursor"]["visible"] is True


@pytest.mark.frontend
def test_pc_overlay_cursor_click_uses_effect_without_position_glide(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            overlay.showCursorAt(260, 164);
            overlay.clickCursor(420);
            await Promise.resolve();
            return window.__pcOverlayUpdates.map((update) => update.payload.cursor).filter(Boolean);
        }
        """
    )

    click_payload = result[-1]
    assert click_payload["x"] == 360
    assert click_payload["y"] == 214
    assert click_payload["effect"] == "click"
    assert click_payload["durationMs"] == 0
    assert click_payload["effectDurationMs"] == 420


@pytest.mark.frontend
def test_externalized_chat_spotlight_renders_compact_capsule_in_pc_overlay_only(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            const begins = [];
            const updates = [];
            window.__externalChatOverlayBegins = begins;
            window.__externalChatOverlayUpdates = updates;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    updates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: (payload) => {
                    begins.push(payload);
                    return Promise.resolve({ ok: true });
                },
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-shell" style="position:fixed; left:560px; top:360px; width:480px; height:90px;">
                    <div id="react-chat-window-root">
                        <div
                            class="compact-chat-surface-frame"
                            data-compact-geometry-owner="surface"
                            data-compact-geometry-item="capsule"
                            data-compact-drag-surface="true"
                            style="position:fixed; left:600px; top:400px; width:430px; height:54px; border-radius:999px;"
                        ></div>
                    </div>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_spotlight',
                    kind: 'input',
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 180));
            return {
                runId: window.localStorage.getItem('yuiGuidePcOverlayRunId'),
                begins: window.__externalChatOverlayBegins,
                updates: window.__externalChatOverlayUpdates,
            };
        }
        """
    )

    assert result["runId"] == "test-run"
    assert result["begins"] == [{"tutorialRunId": "test-run"}]
    assert len(result["updates"]) >= 1
    spotlight_payload = result["updates"][-1]["payload"]["spotlights"][0]
    assert spotlight_payload["id"] == "external-chat-input"
    assert spotlight_payload["kind"] == "input"
    assert spotlight_payload["shape"] == "rounded-rect"
    assert spotlight_payload["x"] == 692
    assert spotlight_payload["y"] == 442
    assert spotlight_payload["width"] == 446
    assert spotlight_payload["height"] == 70


@pytest.mark.frontend
def test_externalized_chat_input_spotlight_retries_after_capsule_layout_appears(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-shell" style="position:fixed; left:560px; top:360px; width:480px; height:90px;">
                    <div id="react-chat-window-root"></div>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const timestamp = Date.now();
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_spotlight',
                    kind: 'input',
                    timestamp,
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 120));
            document.getElementById('react-chat-window-root').innerHTML = `
                <div
                    class="compact-chat-surface-frame"
                    data-compact-geometry-owner="surface"
                    data-compact-geometry-item="capsule"
                    data-compact-drag-surface="true"
                    style="position:fixed; left:600px; top:400px; width:430px; height:54px; border-radius:999px;"
                ></div>
            `;
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_update_chat_message',
                    messageId: 'guide-message',
                    patch: { status: 'streaming' },
                    timestamp: timestamp + 1,
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 420));
            return {
                updates: window.__externalChatOverlayUpdates || [],
            };
        }
        """
    )

    assert result["updates"][-1]["payload"]["spotlights"][0]["id"] == "external-chat-input"
    assert result["updates"][-1]["payload"]["spotlights"][0]["kind"] == "input"
    assert result["updates"][-1]["payload"]["spotlights"][0]["width"] == 446
    assert result["updates"][-1]["payload"]["spotlights"][0]["height"] == 70


@pytest.mark.frontend
def test_externalized_chat_capsule_spotlight_keeps_last_rect_when_target_temporarily_missing(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-shell" style="position:fixed; left:560px; top:360px; width:480px; height:90px;">
                    <div id="react-chat-window-root">
                        <div
                            id="capsule-target"
                            class="compact-chat-surface-frame"
                            data-compact-geometry-owner="surface"
                            data-compact-geometry-item="capsule"
                            data-compact-geometry-part="capsuleBody"
                            data-compact-drag-surface="true"
                            style="position:fixed; left:600px; top:400px; width:430px; height:54px; border-radius:999px;"
                        ></div>
                    </div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/common.js", "app-interpage.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_spotlight',
                    kind: 'capsule-input',
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 160));
            const target = document.getElementById('capsule-target');
            target.style.display = 'none';
            const hiddenAt = window.__externalChatOverlayUpdates.length;
            await new Promise((resolve) => setTimeout(resolve, 260));
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_spotlight',
                    kind: '',
                    timestamp: Date.now() + 1,
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 40));
            const updates = window.__externalChatOverlayUpdates || [];
            return {
                updates,
                hiddenUpdates: updates.slice(hiddenAt),
            };
        }
        """
    )

    first_spotlight_payloads = [
        entry for entry in result["updates"]
        if entry.get("payload", {}).get("spotlights")
    ]
    assert first_spotlight_payloads
    assert first_spotlight_payloads[0]["payload"]["spotlights"][0]["id"] == "external-chat-capsule-input"
    hidden_spotlight_lengths = [
        len(entry.get("payload", {}).get("spotlights", []))
        for entry in result["hiddenUpdates"][:-1]
        if "spotlights" in entry.get("payload", {})
    ]
    assert hidden_spotlight_lengths
    assert all(length > 0 for length in hidden_spotlight_lengths)
    assert result["hiddenUpdates"][-1]["payload"]["spotlights"] == []


@pytest.mark.frontend
def test_externalized_chat_capsule_input_spotlight_uses_capsule_body_rect_without_variant(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-shell" style="position:fixed; left:560px; top:360px; width:480px; height:90px;">
                    <div id="react-chat-window-root">
                        <div
                            class="compact-chat-surface-frame"
                            data-compact-geometry-owner="surface"
                            data-compact-geometry-item="capsule"
                            data-compact-geometry-part="capsuleBody"
                            data-compact-drag-surface="true"
                            style="position:fixed; left:600px; top:400px; width:430px; height:54px; border-radius:999px;"
                        ></div>
                        <button
                            class="compact-chat-capsule-button"
                            data-compact-hit-region-id="capsule:text"
                            style="position:fixed; left:780px; top:408px; width:180px; height:38px;"
                        ></button>
                    </div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/common.js", "app-interpage.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_spotlight',
                    kind: 'capsule-input',
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 160));
            const updates = window.__externalChatOverlayUpdates || [];
            return updates.filter((entry) => entry.payload && entry.payload.spotlights);
        }
        """
    )

    assert result
    spotlight = result[-1]["payload"]["spotlights"][0]
    assert spotlight["id"] == "external-chat-capsule-input"
    assert spotlight["x"] == 692
    assert spotlight["width"] == 446


@pytest.mark.frontend
def test_pc_overlay_cursor_position_updates_during_suppressed_move_for_look_at(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            overlay.isPcOverlayActive = () => true;
            overlay.shouldSuppressDomForPcOverlay = () => true;
            overlay.pcOverlayBridge = {
                showCursorAt: () => {},
                moveCursorTo: () => {},
            };
            overlay.showCursorAt(100, 100);
            const movePromise = overlay.moveCursorTo(500, 100, { durationMs: 420 });
            await new Promise((resolve) => setTimeout(resolve, 180));
            const mid = overlay.getCursorPosition();
            const visibleDuringMove = overlay.isCursorVisible();
            await movePromise;
            const end = overlay.getCursorPosition();
            return { mid, end, visibleDuringMove };
        }
        """
    )

    assert result["visibleDuringMove"] is True
    assert result["mid"]["x"] > 360
    assert result["mid"]["x"] < 500
    assert result["mid"]["y"] == 100
    assert result["end"] == {"x": 500, "y": 100}


@pytest.mark.frontend
def test_pc_overlay_suppressed_ellipse_keeps_dom_cursor_hidden(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            const pcMoves = [];
            overlay.isPcOverlayActive = () => true;
            overlay.shouldSuppressDomForPcOverlay = () => true;
            overlay.pcOverlayBridge = {
                showCursorAt: (x, y) => pcMoves.push({ type: 'show', x, y }),
                moveCursorTo: (x, y, durationMs, effect) => {
                    pcMoves.push({ type: 'move', x, y, durationMs, effect });
                },
            };
            overlay.showCursorAt(100, 100);
            const cursorShellBefore = document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            let cancel = false;
            const animation = overlay.runEllipseAnimation(
                200,
                120,
                48,
                28,
                1200,
                () => false,
                null,
                () => cancel
            );
            const waitUntil = Date.now() + 900;
            while (
                pcMoves.filter((entry) => entry.type === 'move').length < 6
                && Date.now() < waitUntil
            ) {
                await new Promise((resolve) => setTimeout(resolve, 40));
            }
            const cursorShell = document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            const duringAnimation = {
                domExistsBefore: !!cursorShellBefore,
                domExists: !!cursorShell,
                bodyActive: document.body.classList.contains('yui-guide-ghost-cursor-active'),
                pcMoveCount: pcMoves.filter((entry) => entry.type === 'move').length,
                cursorVisible: overlay.isCursorVisible(),
            };
            cancel = true;
            await animation;
            return {
                duringAnimation,
                finalDomExists: !!cursorShell,
            };
        }
        """
    )

    assert result["duringAnimation"]["domExistsBefore"] is False
    assert result["duringAnimation"]["domExists"] is False
    assert result["duringAnimation"]["bodyActive"] is False
    assert result["duringAnimation"]["pcMoveCount"] >= 6
    assert result["duringAnimation"]["cursorVisible"] is True
    assert result["finalDomExists"] is False


@pytest.mark.frontend
def test_pc_overlay_ellipse_uses_global_overlay_on_first_cursor_animation(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const overlay = new window.YuiGuideOverlay(document);
            const animation = overlay.runEllipseAnimation(
                200,
                120,
                48,
                28,
                900,
                () => false,
                null,
                () => false
            );
            await new Promise((resolve) => setTimeout(resolve, 260));
            const cursorShell = document.querySelector('#yui-guide-overlay .yui-guide-cursor-shell');
            return {
                domExists: !!cursorShell,
                bodyActive: document.body.classList.contains('yui-guide-ghost-cursor-active'),
                cursorVisible: overlay.isCursorVisible(),
                pcCursorUpdates: window.__pcOverlayUpdates
                    .map((update) => update && update.payload && update.payload.cursor)
                    .filter(Boolean).length,
            };
        }
        """
    )

    assert result["domExists"] is False
    assert result["bodyActive"] is False
    assert result["cursorVisible"] is True
    assert result["pcCursorUpdates"] >= 1


@pytest.mark.frontend
def test_return_petal_transition_keeps_dom_fallback_without_pc_petal_capability(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const transition = director.createReturnPetalTransition(
                { x: 320, y: 240 },
                {
                    durationMs: 900,
                    finalOpacity: 0.6,
                    sequence: {
                        url: '/static/assets/tutorial/petals/yui-guide-petal-transition.webp',
                    },
                }
            );
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const layer = document.querySelector('.yui-guide-petal-transition');
            if (transition && typeof transition.finish === 'function') {
                await transition.finish();
            }
            return {
                hasDomLayer: !!layer,
                pcPetalUpdates: window.__pcOverlayUpdates.filter(
                    (update) => update.payload && update.payload.petal
                ).length,
            };
        }
        """
    )

    assert result == {
        "hasDomLayer": True,
        "pcPetalUpdates": 1,
    }


@pytest.mark.frontend
def test_return_petal_transition_keeps_dom_fallback_with_pc_petal_capability(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                capabilities: { petalTransition: true },
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const transition = director.createReturnPetalTransition(
                { x: 320, y: 240 },
                {
                    durationMs: 900,
                    finalOpacity: 0.6,
                    sequence: {
                        url: '/static/assets/tutorial/petals/yui-guide-petal-transition.webp',
                    },
                }
            );
            await new Promise((resolve) => requestAnimationFrame(resolve));
            const layer = document.querySelector('.yui-guide-petal-transition');
            if (transition && typeof transition.finish === 'function') {
                await transition.finish();
            }
            return {
                hasDomLayer: !!layer,
                pcPetalUpdates: window.__pcOverlayUpdates.filter(
                    (update) => update.payload && update.payload.petal
                ).length,
            };
        }
        """
    )

    assert result == {
        "hasDomLayer": True,
        "pcPetalUpdates": 1,
    }


@pytest.mark.frontend
def test_avatar_floating_petal_cue_does_not_wait_for_petal_sequence_preload(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            let releaseSequence;
            const sequencePromise = new Promise((resolve) => {
                releaseSequence = resolve;
            });
            director.sceneRunId = 9;
            director.loadReturnPetalSequence = () => {
                events.push({ type: 'load-sequence' });
                return sequencePromise;
            };
            director.getAvatarFloatingNarrationDurationMs = () => 10000;
            director.waitForSceneDelay = async (durationMs) => {
                events.push({ type: 'delay', durationMs });
                return true;
            };
            director.playReturnPetalTransition = async (options) => {
                events.push({ type: 'transition', durationMs: options && options.durationMs });
                if (options && typeof options.onTransitionStart === 'function') {
                    options.onTransitionStart();
                }
            };
            director.cursor.hide = () => events.push({ type: 'cursor-hide' });
            director.clearExternalizedChatGuideTarget = () => events.push({ type: 'clear-external' });
            director.overlay.clearPersistentSpotlight = () => events.push({ type: 'clear-persistent' });
            director.overlay.clearActionSpotlight = () => events.push({ type: 'clear-action' });
            director.clearSceneExtraSpotlights = () => {};
            director.clearRetainedExtraSpotlights = () => {};
            director.clearAllVirtualSpotlights = () => {};
            director.clearSpotlightGeometryHints = () => {};
            director.clearSpotlightVariantHints = () => {};
            director.disableInterrupts = () => {};

            const playPromise = director.playAvatarFloatingPetalTransitionAtCue(
                { id: 'day6_wrap' },
                9,
                'avatar_floating_day6_wrap',
                '你可以放心地继续做你自己的事情',
                Date.now()
            );
            await Promise.resolve();
            await Promise.resolve();
            const beforeSequenceReady = events.slice();
            releaseSequence(null);
            await playPromise;
            return {
                beforeSequenceReady,
                afterSequenceReady: events,
            };
        }
        """
    )

    cue_delays = [
        event["durationMs"]
        for event in result["beforeSequenceReady"]
        if event.get("type") == "delay"
    ]
    assert any(6990 <= duration_ms <= 7000 for duration_ms in cue_delays)
    assert {"type": "transition", "durationMs": 3000} in result["beforeSequenceReady"]
    assert {"type": "cursor-hide"} in result["beforeSequenceReady"]


@pytest.mark.frontend
def test_return_petal_transition_pc_overlay_starts_before_dom_sequence_load(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            let releaseSequence;
            const sequencePromise = new Promise((resolve) => {
                releaseSequence = resolve;
            });
            director.petalTransitionController.preloadReturnPetalSequence = () => sequencePromise;
            director.getReturnPetalTransitionOrigin = () => ({ x: 320, y: 240 });
            director.shouldReduceTutorialMotion = () => false;
            director.overlay.playPetalTransition = (origin, options) => {
                events.push({
                    type: 'pc-petal',
                    x: origin && origin.x,
                    y: origin && origin.y,
                    durationMs: options && options.durationMs,
                });
                return null;
            };
            director.overlay.isPcOverlayActive = () => true;
            director.createReturnPetalTransition = (origin, options) => {
                events.push({
                    type: 'dom-petal',
                    skipPcOverlay: !!(options && options.skipPcOverlay),
                    hasSequence: !!(options && options.sequence),
                });
                return {
                    done: async () => events.push({ type: 'dom-done' }),
                    finish: async () => events.push({ type: 'dom-finish' }),
                };
            };
            director.fadeReturnPetalTransitionModelOut = async (durationMs) => {
                events.push({ type: 'fade', durationMs });
                return true;
            };
            director.restoreTutorialAvatarForReturnPetalTransition = async () => {
                events.push({ type: 'restore-avatar' });
                return true;
            };
            director.restoreReturnPetalTransitionOpacityTargets = () => {
                events.push({ type: 'restore-opacity' });
            };

            const playPromise = director.playReturnPetalTransition({
                durationMs: 3000,
                onTransitionStart: () => events.push({ type: 'transition-start' }),
            });
            await Promise.resolve();
            await Promise.resolve();
            const beforeSequenceReady = events.slice();
            releaseSequence({
                url: '/static/assets/tutorial/petals/yui-guide-petal-transition.webp',
            });
            await playPromise;
            return {
                beforeSequenceReady,
                afterSequenceReady: events,
            };
        }
        """
    )

    assert result["beforeSequenceReady"] == [
        {"type": "pc-petal", "x": 320, "y": 240, "durationMs": 6200},
        {"type": "transition-start"},
        {"type": "fade", "durationMs": 3000},
    ]
    assert {
        "type": "dom-petal",
        "skipPcOverlay": True,
        "hasSequence": True,
    } in result["afterSequenceReady"]


@pytest.mark.frontend
def test_day1_skip_clears_externalized_chat_cursor_immediately(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.interactionTakeover = {
                clearExternalizedChatFx: () => calls.push('clearExternalizedChatFx'),
                setExternalizedChatCursor: (kind) => calls.push('cursor:' + kind),
                setExternalizedChatSpotlight: (kind) => calls.push('spotlight:' + kind),
            };
            director.beginTerminationVisualCleanup();
            return calls;
        }
        """
    )

    assert "clearExternalizedChatFx" in result


@pytest.mark.frontend
def test_day2_screen_entry_does_not_use_bottom_right_chat_proxy_fallback(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" style="display:none;">
                    <div id="react-chat-window-shell" style="position:absolute; left:40px; top:40px; width:320px; height:240px;"></div>
                </div>
                <button id="live2d-btn-screen" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return {
                chatProxy: director.getAvatarFloatingChatProxyAnchorPoint(),
                start: director.resolveAvatarFloatingCursorStartPoint(
                    { id: 'day2_screen_entry' },
                    [document.getElementById('live2d-btn-screen')],
                    'day2_intro_context'
                ),
                bottomRightProxy: {
                    x: window.innerWidth * 0.72,
                    y: window.innerHeight * 0.78,
                },
            };
        }
        """
    )

    assert result["chatProxy"] is None
    assert result["start"] == {"x": 242, "y": 202}
    assert result["start"] != result["bottomRightProxy"]


@pytest.mark.frontend
def test_avatar_floating_cursor_start_uses_visible_target_without_previous_anchor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="target" style="position:absolute; left:40px; top:40px; width:120px; height:80px;"></div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const target = document.getElementById('target');
            return director.resolveAvatarFloatingCursorStartPoint(
                { id: 'next_scene' },
                [target]
            );
        }
        """
    )

    assert result == {"x": 100, "y": 80}


@pytest.mark.frontend
def test_managed_scene_cursor_start_uses_previous_scene_anchor_when_position_lost(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="target" style="position:absolute; left:40px; top:40px; width:120px; height:80px;"></div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const registry = {
                getStep: (stepId) => ({
                    page: 'home',
                    anchor: '#target',
                    performance: {
                        bubbleText: '',
                        cursorAction: 'wobble',
                        cursorTarget: '#target',
                        delayMs: 0,
                    },
                    interrupts: {},
                }),
            };
            const director = window.createYuiGuideDirector({ page: 'home', registry });
            const calls = [];
            let hasPosition = false;
            director.cursor = {
                hasPosition: () => hasPosition,
                showAt: (x, y) => {
                    calls.push({ type: 'showAt', x, y });
                    hasPosition = true;
                },
                moveToRect: async () => {
                    calls.push({ type: 'moveToRect' });
                    return true;
                },
                wobble: () => calls.push({ type: 'wobble' }),
            };
            director.stopPersistentGhostCursorLookAtPerformance = async () => null;
            director.stopIntroVoiceCursorLookAtPerformance = async () => null;
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.currentSceneId = 'previous_scene';
            director.avatarFloatingSceneCursorAnchorPoints = {
                previous_scene: { x: 680, y: 460 },
            };
            await director.playAvatarFloatingScene({
                id: 'next_scene',
                target: '#target',
                cursorTarget: '#target',
                cursorAction: 'move',
            }, 1, 1, 2);
            return calls;
        }
        """
    )

    assert result[0] == {"type": "showAt", "x": 680, "y": 460}
    assert result[1]["type"] == "moveToRect"


@pytest.mark.frontend
def test_avatar_floating_resistance_cursor_moves_away_from_pointer_without_motion_vector(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const moves = [];
            director.cursor.lastTarget = { x: 420, y: 260 };
            director.cursor.overlay = {
                getCursorPosition: () => ({ x: 100, y: 100 }),
                moveCursorTo: async (x, y, options) => {
                    moves.push({ x, y, durationMs: options && options.durationMs });
                    return true;
                },
                wobbleCursor: () => moves.push({ type: 'wobble' }),
            };

            await director.cursor.resistTo(160, 100, {});
            return moves;
        }
        """
    )

    assert result[0]["x"] < 100
    assert result[0]["y"] == 100
    assert result[0]["x"] <= 82


@pytest.mark.frontend
def test_avatar_floating_resistance_cursor_returns_to_current_position_not_last_target(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const moves = [];
            director.cursor.lastTarget = { x: 420, y: 260 };
            director.cursor.overlay = {
                getCursorPosition: () => ({ x: 100, y: 100 }),
                moveCursorTo: async (x, y, options) => {
                    moves.push({ x, y, durationMs: options && options.durationMs });
                    return true;
                },
                wobbleCursor: () => moves.push({ type: 'wobble' }),
            };

            await director.cursor.resistTo(160, 100, {
                motionDx: 24,
                motionDy: 0,
            });
            return moves;
        }
        """
    )

    assert result[0]["x"] < 100
    assert len(result) == 2
    assert result[1] == {"x": 100, "y": 100, "durationMs": 260}


@pytest.mark.frontend
def test_avatar_floating_repeated_cursor_reaction_returns_to_original_rest_point(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            let current = { x: 100, y: 100 };
            const moves = [];
            let releaseFirstMove;
            let pendingFirstMove = true;
            director.cursor.overlay = {
                hasCursorPosition: () => true,
                isCursorVisible: () => true,
                getCursorPosition: () => ({ x: current.x, y: current.y }),
                moveCursorTo: (x, y, options) => {
                    current = { x, y };
                    moves.push({ x, y, durationMs: options && options.durationMs });
                    if (pendingFirstMove) {
                        pendingFirstMove = false;
                        return new Promise((resolve) => {
                            releaseFirstMove = () => resolve(true);
                        });
                    }
                    return Promise.resolve(true);
                },
            };

            const firstReaction = director.cursor.reactToUserMotion(160, 100, {
                motionDx: 24,
                motionDy: 0,
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const secondReaction = director.cursor.reactToUserMotion(170, 100, {
                motionDx: 24,
                motionDy: 0,
            });
            releaseFirstMove();
            await Promise.all([firstReaction, secondReaction]);
            return moves;
        }
        """
    )

    assert result[-1] == {"x": 100, "y": 100, "durationMs": 240}


@pytest.mark.frontend
def test_plugin_dashboard_light_resistance_keeps_cursor_reaction(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.dispatchDesktopPluginDashboardInterruptAck = (payload) => {
                calls.push({ type: 'ack', payload });
            };
            director.playLightResistance = async (x, y, options) => {
                calls.push({ type: 'resist', x, y, options });
            };

            await director.handlePluginDashboardInterruptRequest(null, {
                windowRef: null,
                targetOrigin: window.location.origin,
            }, {
                requestId: 'interrupt-request-1',
                sessionId: 'session-1',
                detail: {
                    kind: 'interrupt_resist_light',
                    x: 160,
                    y: 100,
                },
            });
            return calls;
        }
        """
    )

    resist_call = next(call for call in result if call["type"] == "resist")
    assert resist_call["options"] == {
        "suppressCursorReveal": True,
        "forceSystemCursorReveal": True,
    }


@pytest.mark.frontend
def test_plugin_dashboard_light_resistance_temporarily_reveals_system_cursor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const previousCommon = window.YuiGuideCommon;
            const temporaryReveals = [];
            window.YuiGuideCommon = {
                syncPcSystemCursorTemporaryReveal: (durationMs, reason) => {
                    temporaryReveals.push({ reason, durationMs });
                },
            };
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const calls = [];
                director.dispatchDesktopPluginDashboardInterruptAck = (payload) => {
                    calls.push({ type: 'ack', payload });
                };
                director.getStep = (stepId) => {
                    if (stepId === 'interrupt_resist_light') {
                        return {
                            performance: {
                                bubbleText: 'Stop pulling me',
                                voiceKey: 'interrupt_resist_light_1',
                            },
                        };
                    }
                    return null;
                };
                director.resolvePerformanceBubbleText = (performance) => performance && performance.bubbleText || '';
                director.resolvePerformanceResistanceVoices = () => [];
                director.captureCurrentGuidePresentationSnapshot = () => null;
                director.pauseCurrentSceneForResistance = () => {};
                director.resumeCurrentSceneAfterResistance = () => {};
                director.interruptNarrationForResistance = () => {};
                director.appendGuideChatMessage = () => {};
                director.applyGuideEmotion = () => {};
                director.voiceQueue.speak = async () => null;
                director.runInterruptResistPerformance = async () => null;
                director.cursor.resistTo = async () => null;

                await director.handlePluginDashboardInterruptRequest(null, {
                    windowRef: null,
                    targetOrigin: window.location.origin,
                }, {
                    requestId: 'interrupt-request-1',
                    sessionId: 'session-1',
                    detail: {
                        kind: 'interrupt_resist_light',
                        x: 160,
                        y: 100,
                    },
                });
                return { calls, temporaryReveals };
            } finally {
                window.YuiGuideCommon = previousCommon;
            }
        }
        """
    )

    assert result["temporaryReveals"] == [{
        "reason": "interrupt_resist_light",
        "durationMs": 2000,
    }]
    assert any(call["type"] == "ack" for call in result["calls"])


@pytest.mark.frontend
def test_avatar_floating_cursor_reaction_waits_for_meaningful_real_mouse_move(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const reactions = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.hasVisiblePosition = () => true;
                director.cursor.reactToUserMotion = (x, y, options) => {
                    reactions.push({ x, y, options });
                };
                director.playLightResistance = () => {
                    throw new Error('small mouse movement should not trigger a light interrupt');
                };

                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0 };
                window.__now = 1016;
                director.handleInterrupt({
                    isTrusted: true,
                    type: 'mousemove',
                    clientX: 124,
                    clientY: 100,
                    movementX: 24,
                    movementY: 0,
                });
                window.__now = 1032;
                director.handleInterrupt({
                    isTrusted: true,
                    type: 'mousemove',
                    clientX: 155,
                    clientY: 100,
                    movementX: 31,
                    movementY: 0,
                });
                return reactions;
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert len(result) == 1
    assert result[0]["options"]["motionDx"] == 31
    assert result[0]["options"]["motionDy"] == 0
    assert result[0]["options"]["scale"] >= 0.4
    assert result[0]["options"]["outDurationMs"] >= 140
    assert result[0]["options"]["backDurationMs"] >= 240


@pytest.mark.frontend
def test_avatar_floating_cursor_reaction_ignores_hidden_position(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const moves = [];
            director.cursor.overlay = {
                hasCursorPosition: () => true,
                isCursorVisible: () => false,
                getCursorPosition: () => ({ x: 1180, y: 660 }),
                moveCursorTo: async (x, y, options) => {
                    moves.push({ x, y, durationMs: options && options.durationMs });
                    return true;
                },
            };

            director.playCursorResistanceToUserMotion(200, 160, 24, 24, 0);
            return moves;
        }
        """
    )

    assert result == []


@pytest.mark.frontend
def test_avatar_floating_cursor_reaction_fallback_moves_away_from_pointer(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const moves = [];
            director.cursor.overlay = {
                getCursorPosition: () => ({ x: 100, y: 100 }),
                moveCursorTo: async (x, y, options) => {
                    moves.push({ x, y, durationMs: options && options.durationMs });
                    return true;
                },
            };

            await director.cursor.reactToUserMotion(160, 100, {});
            return moves;
        }
        """
    )

    assert result[0]["x"] < 100
    assert result[0]["y"] == 100
    assert result[0]["x"] <= 82
    assert result[0]["durationMs"] >= 140
    assert result[1] == {"x": 100, "y": 100, "durationMs": 240}


@pytest.mark.frontend
def test_avatar_floating_cursor_move_retries_after_resistance_reaction(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="target" style="position:absolute; left:180px; top:130px; width:40px; height:40px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const target = document.getElementById('target');
            const moves = [];
            let current = { x: 100, y: 100 };
            let firstTargetMove = true;
            let activeTargetResolve = null;
            director.currentSceneId = 'test_scene';
            director.cursor.overlay = {
                hasCursorPosition: () => true,
                isCursorVisible: () => true,
                getCursorPosition: () => ({ x: current.x, y: current.y }),
                moveCursorTo: (x, y, options) => {
                    moves.push({ x: Math.round(x), y: Math.round(y), durationMs: options && options.durationMs });
                    const isTarget = Math.round(x) === 200 && Math.round(y) === 150;
                    if (isTarget && firstTargetMove) {
                        firstTargetMove = false;
                        return new Promise((resolve) => {
                            activeTargetResolve = resolve;
                        });
                    }
                    if (!isTarget && activeTargetResolve) {
                        const resolveTarget = activeTargetResolve;
                        activeTargetResolve = null;
                        resolveTarget(false);
                    }
                    current = { x, y };
                    return new Promise((resolve) => setTimeout(() => resolve(true), isTarget ? 0 : 40));
                },
            };

            const movePromise = director.moveCursorToElement(target, 420);
            await new Promise((resolve) => setTimeout(resolve, 0));
            director.playCursorResistanceToUserMotion(160, 100, 24, 24, 0);
            const moved = await movePromise;
            return { moved, moves };
        }
        """
    )

    target_moves = [move for move in result["moves"] if move["x"] == 200 and move["y"] == 150]
    assert result["moved"] is True
    assert len(target_moves) >= 2


@pytest.mark.frontend
def test_avatar_floating_distance_below_new_threshold_does_not_trigger_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                document.body.classList.add('yui-taking-over');
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                    if (options && options.forceSystemCursorReveal && !options.cursorRevealAlreadyRequested) {
                        director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');
                    }
                };

                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0.04 };
                [
                    { t: 2000, x: 280 },
                    { t: 3000, x: 460 },
                    { t: 4000, x: 640 },
                ].forEach((sample) => {
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        movementX: 180,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_large_straight_moves_do_not_trigger_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };
                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0.04 };
                [
                    { t: 2000, x: 320 },
                    { t: 3000, x: 540 },
                    { t: 4000, x: 760 },
                ].forEach((sample) => {
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        movementX: 220,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_sustained_shake_triggers_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                const playShake = (samples, startAt) => {
                    samples.forEach((x, index) => {
                        const previousX = index > 0 ? samples[index - 1] : 0;
                        window.__now = startAt + (index * 100);
                        director.handleInterrupt({
                            isTrusted: true,
                            type: 'mousemove',
                            clientX: x,
                            clientY: 100,
                            screenX: x,
                            screenY: 100,
                            movementX: x - previousX,
                            movementY: 0,
                        });
                    });
                };

                playShake([100, 200, 100, 200, 100, 200, 100, 200], 1000);
                const belowRaisedThreshold = {
                    lightInterruptCount: lightInterrupts.length,
                    interruptCount: director.interruptCount,
                };
                playShake([100, 220, 100, 220, 100, 220, 100, 220, 100, 220], 2000);
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                    belowRaisedThreshold,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["belowRaisedThreshold"] == {
        "lightInterruptCount": 0,
        "interruptCount": 0,
    }
    assert len(result["lightInterrupts"]) == 1
    assert result["interruptCount"] == 1
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_near_threshold_shake_uses_matching_distance_and_time_interval(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                [100, 200, 100, 200, 100, 200, 100, 200, 100, 200].forEach((x, index, samples) => {
                    const previousX = index > 0 ? samples[index - 1] : 0;
                    window.__now = 1000 + (index * 100);
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: x,
                        clientY: 100,
                        screenX: x,
                        screenY: 100,
                        movementX: x - previousX,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0


@pytest.mark.frontend
def test_avatar_floating_slow_shake_does_not_trigger_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                const samples = [
                    { t: 1000, x: 100 },
                    { t: 1200, x: 200 },
                    { t: 1400, x: 100 },
                    { t: 1600, x: 200 },
                    { t: 1800, x: 100 },
                    { t: 2000, x: 200 },
                    { t: 2200, x: 100 },
                    { t: 2400, x: 200 },
                ];
                samples.forEach((sample, index) => {
                    const previousX = index > 0 ? samples[index - 1].x : 0;
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        screenX: sample.x,
                        screenY: 100,
                        movementX: sample.x - previousX,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0


@pytest.mark.frontend
def test_avatar_floating_quick_mousemove_under_single_event_threshold_does_not_trigger_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0 };
                [
                    { t: 1040, x: 260 },
                    { t: 1080, x: 420 },
                    { t: 1120, x: 580 },
                    { t: 1160, x: 740 },
                    { t: 1200, x: 900 },
                ].forEach((sample) => {
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        movementX: 160,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_slow_continuous_mousemove_does_not_accumulate_forever(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                director.lastPointerPoint = {
                    x: 100,
                    y: 100,
                    t: 1000,
                    speed: 0,
                };
                let x = 100;
                for (let index = 1; index <= 30; index += 1) {
                    x += 20;
                    window.__now = 1000 + index * 100;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: x,
                        clientY: 100,
                        movementX: 20,
                        movementY: 0,
                    });
                }
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_light_resistance_reveals_real_cursor_for_two_seconds(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            const originalSetTimeout = window.setTimeout;
            const originalClearTimeout = window.clearTimeout;
            window.__now = 1000;
            Date.now = () => window.__now;
            const timers = [];
            const clearedTimers = [];
            window.setTimeout = (callback, delay) => {
                const timer = { callback, delay };
                timers.push(timer);
                return timer;
            };
            window.clearTimeout = (timer) => {
                clearedTimers.push(timer);
            };
            try {
                const cursorVisibility = [];
                const temporaryReveals = [];
                window.YuiGuideCommon = {
                    syncPcSystemCursorHidden: (hidden, reason) => {
                        cursorVisibility.push({ hidden, reason });
                    },
                    syncPcSystemCursorTemporaryReveal: (durationMs, reason) => {
                        temporaryReveals.push({ reason, durationMs });
                    },
                };
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                document.body.classList.add('yui-taking-over');
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.hasVisiblePosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                    if (options && options.forceSystemCursorReveal && !options.cursorRevealAlreadyRequested) {
                        director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');
                    }
                };

                director.lastPointerPoint = { x: 0, y: 100, t: 900, speed: 0 };
                const samples = [
                    { t: 1000, x: 100 },
                    { t: 1100, x: 220 },
                    { t: 1200, x: 100 },
                    { t: 1300, x: 220 },
                    { t: 1400, x: 100 },
                    { t: 1500, x: 220 },
                    { t: 1600, x: 100 },
                    { t: 1700, x: 220 },
                    { t: 1800, x: 100 },
                    { t: 1900, x: 220 },
                ];
                samples.forEach((sample, index) => {
                    const previousX = index > 0 ? samples[index - 1].x : 0;
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        screenX: sample.x,
                        screenY: 100,
                        movementX: sample.x - previousX,
                        movementY: 0,
                    });
                });
                const beforeTimer = cursorVisibility.slice();
                const htmlInterruptCountClassBeforeTimer = document.documentElement.classList.contains('yui-interrupt-count-cursor-revealed');
                const bodyInterruptCountClassBeforeTimer = document.body.classList.contains('yui-interrupt-count-cursor-revealed');
                const temporaryCursorClassBeforeTimer = document.body.classList.contains('yui-resistance-cursor-reveal');
                const activeTimer = timers[timers.length - 1];
                activeTimer.callback();
                return {
                    lightInterrupts,
                    cursorVisibility,
                    temporaryReveals,
                    beforeTimer,
                    htmlInterruptCountClassBeforeTimer,
                    bodyInterruptCountClassBeforeTimer,
                    temporaryCursorClassBeforeTimer,
                    timerDelays: timers.map((timer) => timer.delay),
                    clearedTimers: clearedTimers.length,
                };
            } finally {
                Date.now = originalNow;
                window.setTimeout = originalSetTimeout;
                window.clearTimeout = originalClearTimeout;
                delete window.YuiGuideCommon;
            }
        }
        """
    )

    assert len(result["lightInterrupts"]) == 1
    assert result["lightInterrupts"][0]["options"]["forceSystemCursorReveal"] is True
    assert result["lightInterrupts"][0]["options"]["suppressCursorReveal"] is True
    assert result["lightInterrupts"][0]["options"]["cursorRevealAlreadyRequested"] is True
    expected_temporary_reveals = [{
        "reason": "interrupt_resist_light",
        "durationMs": 2000,
    }]
    assert result["temporaryReveals"] == expected_temporary_reveals
    assert result["beforeTimer"] == [{
        "hidden": True,
        "reason": "user_cursor_reveal_suppressed",
    }]
    assert result["htmlInterruptCountClassBeforeTimer"] is False
    assert result["bodyInterruptCountClassBeforeTimer"] is False
    assert result["temporaryCursorClassBeforeTimer"] is True
    assert result["timerDelays"] == [2000]
    assert result["cursorVisibility"]
    assert all(
        entry == {
            "hidden": True,
            "reason": "user_cursor_reveal_suppressed",
        }
        for entry in result["cursorVisibility"]
    )
    assert result["clearedTimers"] == 0


@pytest.mark.frontend
def test_avatar_floating_active_light_resistance_does_not_count_continuous_shake(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            const originalSetTimeout = window.setTimeout;
            const originalClearTimeout = window.clearTimeout;
            window.__now = 1000;
            Date.now = () => window.__now;
            const timers = [];
            const clearedTimers = [];
            window.setTimeout = (callback, delay) => {
                const timer = { callback, delay };
                timers.push(timer);
                return timer;
            };
            window.clearTimeout = (timer) => {
                clearedTimers.push(timer);
            };
            try {
                const temporaryReveals = [];
                window.YuiGuideCommon = {
                    syncPcSystemCursorHidden: () => {},
                    syncPcSystemCursorTemporaryReveal: (durationMs, reason) => {
                        temporaryReveals.push({ reason, durationMs });
                    },
                };
                const director = window.createYuiGuideDirector({ page: 'home' });
                document.body.classList.add('yui-taking-over');
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.hasVisiblePosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');
                director.interruptCount = 1;
                director.resistanceController.lightResistanceActive = true;
                director.scenePausedForResistance = true;

                const playQualifyingGroup = () => {
                    const startAt = window.__now;
                    director.lastPointerPoint = { x: 0, y: 100, t: startAt, speed: 0 };
                    const samples = [100, 220, 100, 220, 100, 220, 100, 220, 100, 220];
                    samples.forEach((x, index) => {
                        window.__now = startAt + ((index + 1) * 100);
                        director.handleInterrupt({
                            isTrusted: true,
                            type: 'mousemove',
                            clientX: x,
                            clientY: 100,
                            screenX: x,
                            screenY: 100,
                            movementX: x - (index > 0 ? samples[index - 1] : 0),
                            movementY: 0,
                        });
                    });
                };

                playQualifyingGroup();

                return {
                    activeDuringSecond: director.resistanceController.lightResistanceActive,
                    pausedDuringSecond: director.scenePausedForResistance,
                    interruptCount: director.interruptCount,
                    temporaryReveals,
                    timerDelays: timers.map((timer) => timer.delay),
                    clearedTimers: clearedTimers.length,
                };
            } finally {
                Date.now = originalNow;
                window.setTimeout = originalSetTimeout;
                window.clearTimeout = originalClearTimeout;
                delete window.YuiGuideCommon;
            }
        }
        """
    )

    assert result["activeDuringSecond"] is True
    assert result["pausedDuringSecond"] is True
    assert result["interruptCount"] == 1
    assert result["temporaryReveals"] == [
        {"reason": "interrupt_resist_light", "durationMs": 2000},
    ]
    assert result["timerDelays"] == [2000]
    assert result["clearedTimers"] == 0


@pytest.mark.frontend
def test_avatar_floating_interrupt_cursor_reveal_survives_angry_exit_timeout(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalSetTimeout = window.setTimeout;
            const originalClearTimeout = window.clearTimeout;
            const timers = [];
            window.setTimeout = (callback, delay) => {
                const timer = { callback, delay };
                timers.push(timer);
                return timer;
            };
            window.clearTimeout = () => {};
            try {
                const cursorVisibility = [];
                window.YuiGuideCommon = {
                    syncPcSystemCursorHidden: (hidden, reason) => {
                        cursorVisibility.push({ hidden, reason });
                    },
                };
                const director = window.createYuiGuideDirector({ page: 'home' });
                document.body.classList.add('yui-taking-over');

                director.revealRealCursorForInterruptCount();
                director.angryExitTriggered = true;
                timers[0].callback();

                return {
                    htmlClassRetained: document.documentElement.classList.contains('yui-interrupt-count-cursor-revealed'),
                    bodyClassRetained: document.body.classList.contains('yui-interrupt-count-cursor-revealed'),
                    cursorVisibility,
                    timerDelay: timers[0] && timers[0].delay,
                };
            } finally {
                window.setTimeout = originalSetTimeout;
                window.clearTimeout = originalClearTimeout;
                delete window.YuiGuideCommon;
            }
        }
        """
    )

    assert result["timerDelay"] == 3000
    assert result["htmlClassRetained"] is True
    assert result["bodyClassRetained"] is True
    assert result["cursorVisibility"] == [
        {"hidden": False, "reason": "interrupt_count_reveal"},
    ]


@pytest.mark.frontend
def test_avatar_floating_angry_exit_clears_temporary_system_cursor_reveal_timer(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const originalSetTimeout = window.setTimeout;
            const originalClearTimeout = window.clearTimeout;
            const timers = [];
            const clearedTimers = [];
            window.setTimeout = (callback, delay) => {
                const timer = { callback, delay };
                timers.push(timer);
                return timer;
            };
            window.clearTimeout = (timer) => {
                clearedTimers.push(timer);
            };
            try {
                const cursorVisibility = [];
                window.YuiGuideCommon = {
                    syncPcSystemCursorHidden: (hidden, reason) => {
                        cursorVisibility.push({ hidden, reason });
                    },
                    syncPcSystemCursorTemporaryReveal: (durationMs, reason) => {
                        cursorVisibility.push({ temporaryReveal: durationMs, reason });
                    },
                };
                const director = window.createYuiGuideDirector({ page: 'home' });
                document.body.classList.add('yui-taking-over');
                director.currentSceneId = 'test_scene';
                director.interruptCount = 3;
                director.recordExperienceMetric = () => {};
                director.clearSceneTimers = () => {};
                director.disableInterrupts = () => {};
                director.cancelActiveNarration = () => {};
                director.beginGuideInterruptPresentation = () => {};
                director.getStep = () => ({ performance: {} });
                director.resolvePerformanceBubbleText = () => '';
                director.getGuideVoiceDurationMs = () => 0;
                director.setTutorialTakingOver = () => {};
                director.overlay = {
                    setAngry: () => {},
                    hidePluginPreview: () => {},
                    hideBubble: () => {},
                };
                director.appendGuideChatMessage = () => {};
                director.applyGuideEmotion = () => {};
                director.runAngryExitPerformance = async () => null;
                director.speakGuideLine = async () => null;
                director.notifyPluginDashboardNarrationFinished = () => {};
                director.requestTermination = () => {};

                director.revealSystemCursorTemporarily(2000, 'interrupt_resist_light');
                await director.abortAsAngryExit('pointer_interrupt');

                return {
                    timerCount: timers.length,
                    clearedTimerCount: clearedTimers.length,
                    cursorVisibility,
                    hasResistanceClass: document.body.classList.contains('yui-resistance-cursor-reveal'),
                };
            } finally {
                window.setTimeout = originalSetTimeout;
                window.clearTimeout = originalClearTimeout;
                delete window.YuiGuideCommon;
            }
        }
        """
    )

    assert result["timerCount"] == 1
    assert result["clearedTimerCount"] == 1
    assert result["cursorVisibility"] == [
        {"temporaryReveal": 2000, "reason": "interrupt_resist_light"},
        {"hidden": False, "reason": "interrupt_angry_exit"},
    ]
    assert result["hasResistanceClass"] is True


@pytest.mark.frontend
def test_voice_queue_speak_stays_cancelled_when_stopped_during_start_delay(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const originalSetTimeout = window.setTimeout;
            const originalClearTimeout = window.clearTimeout;
            const timers = [];
            const clearedTimers = [];
            window.setTimeout = (callback, delay) => {
                const timer = { callback, delay };
                timers.push(timer);
                return timer;
            };
            window.clearTimeout = (timer) => {
                clearedTimers.push(timer);
            };
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                director.voiceQueue.resolveGuideAudioSrc = () => '';

                const speakPromise = director.voiceQueue.speak('hello', {
                    minDurationMs: 1200,
                });
                director.voiceQueue.stop();
                timers[0].callback();
                await speakPromise;

                return {
                    timerDelays: timers.map((timer) => timer.delay),
                    clearedTimerCount: clearedTimers.length,
                    currentFinishActive: typeof director.voiceQueue.currentFinish === 'function',
                };
            } finally {
                window.setTimeout = originalSetTimeout;
                window.clearTimeout = originalClearTimeout;
            }
        }
        """
    )

    assert result["timerDelays"] == [48]
    assert result["clearedTimerCount"] == 0
    assert result["currentFinishActive"] is False


@pytest.mark.frontend
def test_avatar_floating_acceleration_below_new_threshold_does_not_trigger_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0 };
                [
                    { t: 1001, x: 101, dx: 1 },
                    { t: 1002, x: 103, dx: 2 },
                    { t: 1003, x: 106, dx: 3 },
                ].forEach((sample) => {
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        movementX: sample.dx,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_small_acceleration_spikes_do_not_trigger_light_resistance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0 };
                [
                    { t: 1001, x: 105, dx: 5 },
                    { t: 1002, x: 115, dx: 10 },
                    { t: 1003, x: 135, dx: 20 },
                    { t: 1004, x: 160, dx: 25 },
                ].forEach((sample) => {
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        movementX: sample.dx,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                    streak: director.interruptQualifyingMoveStreak,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0
    assert result["streak"] == 0


@pytest.mark.frontend
def test_avatar_floating_acceleration_threshold_requires_single_event_distance(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 3, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };

                director.lastPointerPoint = { x: 100, y: 100, t: 1000, speed: 0 };
                [
                    { t: 1001, x: 140, dx: 40 },
                    { t: 1002, x: 200, dx: 60 },
                    { t: 1003, x: 290, dx: 90 },
                ].forEach((sample) => {
                    window.__now = sample.t;
                    director.handleInterrupt({
                        isTrusted: true,
                        type: 'mousemove',
                        clientX: sample.x,
                        clientY: 100,
                        movementX: sample.dx,
                        movementY: 0,
                    });
                });
                return {
                    lightInterrupts,
                    interruptCount: director.interruptCount,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterrupts"] == []
    assert result["interruptCount"] == 0


@pytest.mark.frontend
def test_avatar_floating_fourth_interrupt_enters_angry_exit_after_three_resistance_lines(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const originalNow = Date.now;
            window.__now = 1000;
            Date.now = () => window.__now;
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const lightInterrupts = [];
                const angryExits = [];
                director.platformCapabilities = { windowBoundsSource: 'electron-window-bounds' };
                director.currentSceneId = 'test_scene';
                director.currentStep = {
                    performance: {},
                    interrupts: { threshold: 4, throttleMs: 0 },
                };
                director.interruptsEnabled = true;
                director.cursor.hasPosition = () => true;
                director.cursor.reactToUserMotion = () => {};
                director.playLightResistance = (x, y, options) => {
                    lightInterrupts.push({ x, y, options });
                };
                director.abortAsAngryExit = (source) => {
                    angryExits.push(source);
                };

                let t = 1000;
                const playQualifyingGroup = () => {
                    const samples = [100, 220, 100, 220, 100, 220, 100, 220, 100, 220];
                    director.lastPointerPoint = { x: 0, y: 100, t, speed: 0 };
                    samples.forEach((sampleX, index) => {
                        t += 100;
                        window.__now = t;
                        director.handleInterrupt({
                            isTrusted: true,
                            type: 'mousemove',
                            clientX: sampleX,
                            clientY: 100,
                            screenX: sampleX,
                            screenY: 100,
                            movementX: sampleX - (index > 0 ? samples[index - 1] : 0),
                            movementY: 0,
                        });
                    });
                };

                playQualifyingGroup();
                playQualifyingGroup();
                playQualifyingGroup();
                playQualifyingGroup();
                return {
                    lightInterruptCount: lightInterrupts.length,
                    angryExits,
                    interruptCount: director.interruptCount,
                };
            } finally {
                Date.now = originalNow;
            }
        }
        """
    )

    assert result["lightInterruptCount"] == 3
    assert result["angryExits"] == ["pointer_interrupt"]
    assert result["interruptCount"] == 4


@pytest.mark.frontend
def test_avatar_floating_light_resistance_forces_angry_then_restores_emotion(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const emotions = [];
            const originalApplyGuideEmotion = director.applyGuideEmotion.bind(director);
            director.applyGuideEmotion = (emotion, options) => {
                emotions.push({
                    emotion,
                    allowDuringInterrupt: !!(options && options.allowDuringInterrupt),
                });
                originalApplyGuideEmotion(emotion, options);
            };
            director.activeGuideEmotion = 'happy';
            director.getStep = (stepId) => {
                if (stepId === 'interrupt_resist_light') {
                    return {
                        performance: {
                            bubbleText: 'Stop pulling me',
                            emotion: 'surprised',
                            voiceKey: 'interrupt_resist_light_1',
                        },
                    };
                }
                return director.currentStep;
            };
            director.resolvePerformanceBubbleText = (performance) => performance && performance.bubbleText || '';
            director.resolvePerformanceResistanceVoices = () => [];
            director.voiceQueue.speak = async () => null;
            director.runInterruptResistPerformance = async () => null;
            director.cursor.resistTo = async () => null;
            director.currentSceneId = 'test_scene';
            director.currentStep = {
                anchor: '',
                performance: {
                    bubbleText: 'Current scene',
                    emotion: 'happy',
                },
            };

            await director.playLightResistance(320, 180, {
                motionDx: 16,
                motionDy: 0,
            });

            return {
                emotions,
                activeGuideEmotion: director.activeGuideEmotion,
            };
        }
        """
    )

    assert {"emotion": "angry", "allowDuringInterrupt": True} in result["emotions"]
    assert result["emotions"][-1]["emotion"] == "happy"
    assert result["activeGuideEmotion"] == "happy"


@pytest.mark.frontend
def test_avatar_floating_angry_exit_forces_angry_emotion(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const cursorVisibility = [];
            const previousYuiGuideCommon = window.YuiGuideCommon;
            window.YuiGuideCommon = {
                syncPcSystemCursorHidden: (hidden, reason) => {
                    cursorVisibility.push({ hidden, reason });
                },
            };
            try {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const emotions = [];
                const terminationRequests = [];
                const originalApplyGuideEmotion = director.applyGuideEmotion.bind(director);
                director.applyGuideEmotion = (emotion, options) => {
                    emotions.push({
                        emotion,
                        allowDuringInterrupt: !!(options && options.allowDuringInterrupt),
                    });
                    originalApplyGuideEmotion(emotion, options);
                };
                director.getStep = (stepId) => {
                    if (stepId === 'interrupt_angry_exit') {
                        return {
                            performance: {
                                bubbleText: 'Stop now',
                                emotion: 'happy',
                                voiceKey: 'interrupt_angry_exit',
                            },
                        };
                    }
                    return null;
                };
                director.resolvePerformanceBubbleText = (performance) => performance && performance.bubbleText || '';
                director.voiceQueue.speak = async () => null;
                director.runAngryExitPerformance = async () => null;
                director.requestTermination = (reason, tutorialReason) => {
                    terminationRequests.push({ reason, tutorialReason });
                };

                await director.abortAsAngryExit('pointer_interrupt');

                return {
                    cursorVisibility,
                    emotions,
                    terminationRequests,
                };
            } finally {
                window.YuiGuideCommon = previousYuiGuideCommon;
            }
        }
        """
    )

    assert {"hidden": False, "reason": "interrupt_angry_exit"} in result["cursorVisibility"]
    assert {"emotion": "angry", "allowDuringInterrupt": True} in result["emotions"]
    assert result["terminationRequests"] == [
        {"reason": "pointer_interrupt", "tutorialReason": "angry_exit"}
    ]


@pytest.mark.frontend
def test_externalized_chat_handoff_remembers_home_cursor_screen_point(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const calls = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => calls.push({ type: 'spotlight', kind }),
                setExternalizedChatCursor: (kind, options) => calls.push({ type: 'cursor', kind, options }),
            };
            director.cursor.showAt(720, 520);
            director.setExternalizedChatGuideTarget('window', { effect: 'wobble' });
            const raw = window.localStorage.getItem('neko_yui_guide_external_chat_cursor_screen_point_v1');
            return {
                calls,
                stored: raw ? JSON.parse(raw) : null,
            };
        }
        """
    )

    assert result["calls"][0] == {"type": "spotlight", "kind": "window"}
    assert result["calls"][1]["type"] == "cursor"
    assert result["stored"]["x"] == 820
    assert result["stored"]["y"] == 570
    assert result["stored"]["kind"] == "window"
    assert result["stored"]["effect"] == "wobble"
    assert result["stored"]["source"] == "home-director-handoff"


@pytest.mark.frontend
def test_externalized_chat_handoff_does_not_clear_home_cursor_position(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const cursorCalls = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: () => {},
                setExternalizedChatCursor: () => {},
            };
            director.cursor.showAt(720, 520);
            director.cursor.hide = () => cursorCalls.push('hide');
            director.cursor.clearPosition = () => cursorCalls.push('clearPosition');

            const handled = director.setExternalizedChatGuideTarget('window', { effect: 'wobble' });

            return {
                handled,
                cursorCalls,
                currentPosition: director.overlay.getCursorPosition(),
            };
        }
        """
    )

    assert result["handled"] is True
    assert result["cursorCalls"] == []
    assert result["currentPosition"] == {"x": 720, "y": 520}


@pytest.mark.frontend
def test_externalized_chat_cursor_uses_recent_handoff_anchor_for_first_smooth_move(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__updates = [];
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 0, y: 0, width: 1280, height: 720 },
                    contentBounds: { x: 0, y: 0, width: 1280, height: 720 },
                    zoomFactor: 1,
                }),
                begin: () => Promise.resolve({ ok: true }),
                update: (payload) => {
                    window.__updates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <div id="react-chat-window-shell" style="position:fixed; left:600px; top:400px; width:240px; height:160px;"></div>
            `;
            window.localStorage.setItem('neko_yui_guide_external_chat_cursor_screen_point_v1', JSON.stringify({
                x: 120,
                y: 90,
                kind: 'screen-button',
                effect: 'wobble',
                source: 'home-director-handoff',
                at: Date.now(),
            }));
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_chat_cursor',
                    kind: 'window',
                    effect: 'wobble',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => window.requestAnimationFrame(() => {
                window.requestAnimationFrame(resolve);
            }));
            const cursorUpdates = window.__updates
                .map((update) => update && update.payload && update.payload.cursor)
                .filter(Boolean);
            const latestCursor = cursorUpdates[cursorUpdates.length - 1] || null;
            return {
                hasLocalCursor: !!document.getElementById('yui-guide-chat-cursor'),
                latestCursor,
            };
        }
        """
    )

    assert result["hasLocalCursor"] is False
    assert result["latestCursor"]["visible"] is True
    assert result["latestCursor"]["durationMs"] >= 900
    assert result["latestCursor"]["effect"] == "wobble"


@pytest.mark.frontend
def test_day3_avatar_tools_props_sentence_opens_menu_with_cursor_click(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
        """,
        script_names=("tutorial/yui-guide/days/day3-interaction-guide.js",),
    )

    result = mock_page.evaluate(
        """
        () => {
            const scenes = window.YuiGuideDailyGuides[3].round.scenes;
            const toggle = scenes.find((scene) => scene.id === 'day3_tool_toggle_intro');
            const intro = scenes.find((scene) => scene.id === 'day3_avatar_tools');
            const props = scenes.find((scene) => scene.id === 'day3_avatar_tools_props');
            const wrap = scenes.find((scene) => scene.id === 'day3_wrap');
            const wrapReady = scenes.find((scene) => scene.id === 'day3_wrap_ready');
            return {
                toggleTarget: toggle && toggle.target || '',
                toggleOperation: toggle && toggle.operation || '',
                toggleCursorAction: toggle && toggle.cursorAction || '',
                introOperation: intro && intro.operation || '',
                introTarget: intro && intro.target || '',
                introCursorAction: intro && intro.cursorAction || '',
                introHasCursorMoveDurationMs: !!(intro && Object.prototype.hasOwnProperty.call(intro, 'cursorMoveDurationMs')),
                propsOperation: props && props.operation || '',
                propsCursorAction: props && props.cursorAction || '',
                propsTarget: props && props.target || '',
                galgameOperation: scenes.find((scene) => scene.id === 'day3_galgame_entry')?.operation || '',
                wrapTarget: wrap && wrap.target || '',
                wrapCursorAction: wrap && wrap.cursorAction || '',
                wrapOperation: wrap && wrap.operation || '',
                wrapReadyTarget: wrapReady && wrapReady.target || '',
                wrapReadyCursorAction: wrapReady && wrapReady.cursorAction || '',
                wrapReadyPetalTransition: !!(wrapReady && wrapReady.petalTransition === true),
            };
        }
        """
    )

    assert result == {
        "toggleTarget": "chat-input",
        "toggleOperation": "",
        "toggleCursorAction": "move",
        "introOperation": "open-compact-tool-fan",
        "introTarget": "chat-tool-toggle",
        "introCursorAction": "click",
        "introHasCursorMoveDurationMs": False,
        "propsOperation": "show-avatar-tools-then-hide-after-narration",
        "propsCursorAction": "click",
        "propsTarget": "chat-avatar-tools",
        "galgameOperation": "rotate-galgame-tool-into-center",
        "wrapTarget": "chat-input",
        "wrapCursorAction": "move",
        "wrapOperation": "cleanup",
        "wrapReadyTarget": "chat-input",
        "wrapReadyCursorAction": "move",
        "wrapReadyPetalTransition": True,
    }


@pytest.mark.frontend
def test_day3_externalized_cursor_effect_never_defaults_to_wobble(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return {
                day3Fallback: director.getExternalizedChatCursorEffect({
                    id: 'day3_future_scene',
                    cursorAction: 'tour',
                }),
                day4Fallback: director.getExternalizedChatCursorEffect({
                    id: 'day4_future_scene',
                    cursorAction: 'tour',
                }),
            };
        }
        """
    )

    assert result == {
        "day3Fallback": "move",
        "day4Fallback": "move",
    }


@pytest.mark.frontend
def test_day3_first_line_highlights_capsule_input_and_centers_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:120px; top:90px; width:320px; height:56px;"
                    ></div>
                    <button
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:absolute; left:470px; top:98px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_tool_toggle_intro'
            );
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push({ type: 'operation', operation: playedScene.operation || '' });
                return true;
            };
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    primaryId: config.primary && config.primary.id,
                    primaryItem: config.primary && config.primary.getAttribute('data-compact-geometry-item'),
                });
            };
            director.cursor.showAt = (x, y) => events.push({ type: 'showAt', x, y });
            director.moveCursorToElement = async (element, durationMs) => {
                const rect = element.getBoundingClientRect();
                events.push({
                    type: 'move',
                    id: element.id || '',
                    item: element.getAttribute('data-compact-geometry-item'),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    durationMs,
                });
                return true;
            };

            await director.playAvatarFloatingScene(scene, 3, 0, 8);
            return events;
        }
        """
    )

    assert {
        "type": "highlight",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    } in result
    assert {"type": "showAt", "x": 280, "y": 118} in result
    assert not any(event.get("type") == "move" for event in result)
    assert {"type": "operation", "operation": "open-compact-tool-fan"} not in result


@pytest.mark.frontend
def test_day3_wrap_highlights_capsule_input_and_keeps_cursor_there(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:140px; top:110px; width:360px; height:60px;"
                    ></div>
                    <button
                        class="compact-input-tool-item-galgame"
                        style="position:absolute; left:560px; top:120px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_wrap'
            );
            director.currentSceneId = 'day3_galgame_choices';
            director.cursor.showAt(581, 141);
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                    primaryItem: config.primary && config.primary.getAttribute('data-compact-geometry-item'),
                });
            };
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push({ type: 'operation', operation: playedScene.operation || '' });
                return true;
            };
            director.moveCursorToElement = async (element, durationMs) => {
                const rect = element.getBoundingClientRect();
                events.push({
                    type: 'move',
                    id: element.id || '',
                    item: element.getAttribute('data-compact-geometry-item'),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    durationMs,
                });
                return true;
            };

            await director.playAvatarFloatingScene(scene, 3, 5, 7);
            return events;
        }
        """
    )

    first_highlight = {
        "type": "highlight",
        "key": "day3_wrap",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    settled_highlight = {
        "type": "highlight",
        "key": "day3_wrap-settled",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    move = {
        "type": "move",
        "id": "compact-chat-input",
        "item": "input",
        "x": 320,
        "y": 140,
        "durationMs": 760,
    }
    operation = {"type": "operation", "operation": "cleanup"}
    assert first_highlight in result
    assert move in result
    assert operation in result
    assert settled_highlight in result
    assert result.index(first_highlight) < result.index(move)
    assert result.index(move) < result.index(operation)
    assert result.index(operation) < result.index(settled_highlight)


@pytest.mark.frontend
def test_day4_wrap_highlights_capsule_input_and_keeps_cursor_there(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="chat-window"
                        style="position:absolute; left:40px; top:40px; width:560px; height:280px;"
                    ></div>
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:140px; top:250px; width:360px; height:60px;"
                    ></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day4-companion-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[4].round.scenes.find(
                (candidate) => candidate.id === 'day4_wrap'
            );
            director.currentSceneId = 'day4_return_home';
            director.cursor.showAt(540, 110);
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.playAvatarFloatingPetalTransitionAtCue = async (playedScene) => {
                events.push({ type: 'petal', sceneId: playedScene.id });
            };
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                    primaryItem: config.primary && config.primary.getAttribute('data-compact-geometry-item'),
                });
            };
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push({ type: 'operation', operation: playedScene.operation || '' });
                return true;
            };
            director.moveCursorToElement = async (element, durationMs) => {
                const rect = element.getBoundingClientRect();
                events.push({
                    type: 'move',
                    id: element.id || '',
                    item: element.getAttribute('data-compact-geometry-item'),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    durationMs,
                });
                return true;
            };

            await director.playAvatarFloatingScene(scene, 4, 7, 8);
            return events;
        }
        """
    )

    first_highlight = {
        "type": "highlight",
        "key": "day4_wrap",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    settled_highlight = {
        "type": "highlight",
        "key": "day4_wrap-settled",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    move = {
        "type": "move",
        "id": "compact-chat-input",
        "item": "input",
        "x": 320,
        "y": 280,
        "durationMs": 760,
    }
    operation = {"type": "operation", "operation": "cleanup"}
    assert first_highlight in result
    assert move in result
    assert operation in result
    assert settled_highlight in result
    assert result.index(first_highlight) < result.index(move)
    assert result.index(move) < result.index(operation)
    assert result.index(operation) < result.index(settled_highlight)


@pytest.mark.frontend
def test_day5_wrap_highlights_capsule_input_and_keeps_cursor_there(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="chat-window"
                        style="position:absolute; left:40px; top:40px; width:560px; height:280px;"
                    ></div>
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:140px; top:250px; width:360px; height:60px;"
                    ></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day5-personalization-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[5].round.scenes.find(
                (candidate) => candidate.id === 'day5_wrap'
            );
            director.currentSceneId = 'day5_memory_entry';
            director.cursor.showAt(540, 110);
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.playAvatarFloatingPetalTransitionAtCue = async (playedScene) => {
                events.push({ type: 'petal', sceneId: playedScene.id });
            };
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                    primaryItem: config.primary && config.primary.getAttribute('data-compact-geometry-item'),
                });
            };
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push({ type: 'operation', operation: playedScene.operation || '' });
                return true;
            };
            director.moveCursorToElement = async (element, durationMs) => {
                const rect = element.getBoundingClientRect();
                events.push({
                    type: 'move',
                    id: element.id || '',
                    item: element.getAttribute('data-compact-geometry-item'),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    durationMs,
                });
                return true;
            };

            await director.playAvatarFloatingScene(scene, 5, 3, 4);
            return events;
        }
        """
    )

    first_highlight = {
        "type": "highlight",
        "key": "day5_wrap",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    settled_highlight = {
        "type": "highlight",
        "key": "day5_wrap-settled",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    move = {
        "type": "move",
        "id": "compact-chat-input",
        "item": "input",
        "x": 320,
        "y": 280,
        "durationMs": 760,
    }
    operation = {"type": "operation", "operation": "cleanup"}
    assert first_highlight in result
    assert move in result
    assert operation in result
    assert settled_highlight in result
    assert result.index(first_highlight) < result.index(move)
    assert result.index(move) < result.index(operation)
    assert result.index(operation) < result.index(settled_highlight)


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("day", "script_name", "scene_id", "current_scene_id", "scene_index", "total_scenes"),
    [
        (6, "tutorial/yui-guide/days/day6-agent-guide.js", "day6_wrap", "day6_wrap_cleanup", 7, 8),
        (7, "tutorial/yui-guide/days/day7-graduation-guide.js", "day7_graduation_wrap", "day7_memory_control", 2, 3),
    ],
)
def test_day6_day7_wrap_highlights_capsule_input_and_keeps_cursor_there(
    mock_page: Page,
    day: int,
    script_name: str,
    scene_id: str,
    current_scene_id: str,
    scene_index: int,
    total_scenes: int,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="chat-window"
                        style="position:absolute; left:40px; top:40px; width:560px; height:280px;"
                    ></div>
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:140px; top:250px; width:360px; height:60px;"
                    ></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", script_name),
    )

    result = mock_page.evaluate(
        """
        async ({ day, sceneId, currentSceneId, sceneIndex, totalScenes }) => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[day].round.scenes.find(
                (candidate) => candidate.id === sceneId
            );
            director.currentSceneId = currentSceneId;
            director.cursor.showAt(540, 110);
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.playAvatarFloatingPetalTransitionAtCue = async (playedScene) => {
                events.push({ type: 'petal', sceneId: playedScene.id });
            };
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                    primaryItem: config.primary && config.primary.getAttribute('data-compact-geometry-item'),
                });
            };
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push({ type: 'operation', operation: playedScene.operation || '' });
                return true;
            };
            director.moveCursorToElement = async (element, durationMs) => {
                const rect = element.getBoundingClientRect();
                events.push({
                    type: 'move',
                    id: element.id || '',
                    item: element.getAttribute('data-compact-geometry-item'),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    durationMs,
                });
                return true;
            };

            await director.playAvatarFloatingScene(scene, day, sceneIndex, totalScenes);
            return events;
        }
        """,
        {
            "day": day,
            "sceneId": scene_id,
            "currentSceneId": current_scene_id,
            "sceneIndex": scene_index,
            "totalScenes": total_scenes,
        },
    )

    first_highlight = {
        "type": "highlight",
        "key": scene_id,
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    settled_highlight = {
        "type": "highlight",
        "key": f"{scene_id}-settled",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    }
    move = {
        "type": "move",
        "id": "compact-chat-input",
        "item": "input",
        "x": 320,
        "y": 280,
        "durationMs": 760,
    }
    operation = {"type": "operation", "operation": "cleanup"}
    assert first_highlight in result
    if scene_id == "day7_graduation_wrap":
        assert move in result
        assert operation in result
        assert result.index(move) < result.index(operation)
        assert result.index(operation) < result.index(settled_highlight)
        assert settled_highlight in result
        assert result.index(first_highlight) < result.index(move)
    else:
        assert not any(event.get("type") == "move" for event in result)
    assert {"type": "petal", "sceneId": scene_id} in result


@pytest.mark.frontend
def test_day6_wrap_cleanup_and_final_wrap_hold_cursor_after_hud(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <section id="agent-task-hud" style="position:absolute; left:650px; top:80px; width:260px; height:140px;"></section>
                <div id="react-chat-window-root">
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:140px; top:250px; width:360px; height:60px;"
                    ></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scenes = window.YuiGuideDailyGuides[6].round.scenes;
            const cleanupScene = scenes.find((candidate) => candidate.id === 'day6_wrap_cleanup');
            const wrapScene = scenes.find((candidate) => candidate.id === 'day6_wrap');
            director.currentSceneId = 'day6_agent_task_hud_control';
            director.cursor.showAt(780, 150);
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.playAvatarFloatingPetalTransitionAtCue = async () => {};
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push({ type: 'operation', sceneId: playedScene.id, operation: playedScene.operation || '' });
                return true;
            };
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    key: config.key || '',
                    primaryId: config.primary && config.primary.id,
                    primaryItem: config.primary && config.primary.getAttribute('data-compact-geometry-item'),
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                const rect = element.getBoundingClientRect();
                events.push({
                    type: 'move',
                    id: element.id || '',
                    item: element.getAttribute('data-compact-geometry-item'),
                    x: rect.left + rect.width / 2,
                    y: rect.top + rect.height / 2,
                    durationMs,
                    sceneId: director.currentSceneId,
                });
                return true;
            };

            await director.playAvatarFloatingScene(cleanupScene, 6, 6, 8);
            await director.playAvatarFloatingScene(wrapScene, 6, 7, 8);
            return events;
        }
        """
    )

    assert not [
        event for event in result
        if event["type"] == "move"
    ]
    assert {
        "type": "highlight",
        "key": "day6_wrap",
        "primaryId": "compact-chat-input",
        "primaryItem": "input",
    } in result


@pytest.mark.frontend
def test_day6_wrap_cleanup_externalized_keeps_input_cursor_target_during_cleanup(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day6-agent-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[6].round.scenes.find(
                (candidate) => candidate.id === 'day6_wrap_cleanup'
            );
            director.currentSceneId = 'day6_agent_task_hud_control';
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push({
                    type: 'spotlight',
                    kind: String(kind || ''),
                }),
                setExternalizedChatCursor: (kind, options) => events.push({
                    type: 'cursor',
                    kind: String(kind || ''),
                    effect: options && options.effect || '',
                }),
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.waitForExternalizedChatCursorMove = async (sceneId) => {
                events.push({ type: 'wait-move', sceneId });
                return true;
            };
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.closeChatToolPopover = () => events.push({ type: 'close-tool-popover' });
            director.collapseAvatarFloatingSidePanelsExcept = () => {};
            director.clearSceneExtraSpotlights = () => {};
            director.clearRetainedExtraSpotlights = () => {};
            director.clearSpotlightGeometryHints = () => {};
            director.clearSpotlightVariantHints = () => {};
            director.overlay.clearActionSpotlight = () => {};
            director.closeManagedPanels = async () => {};
            director.collapseAgentSidePanel = () => {};
            director.collapseCharacterSettingsSidePanel = () => {};

            await director.playAvatarFloatingScene(scene, 6, 6, 8);
            return events;
        }
        """
    )

    assert [event for event in result if event["type"] == "cursor"] == [{
        "type": "cursor",
        "kind": "input",
        "effect": "",
    }]
    assert not [
        event for event in result
        if event["type"] == "spotlight" and event["kind"] == ""
    ]


@pytest.mark.frontend
def test_day3_first_line_externalized_chat_uses_input_spotlight_and_cursor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_tool_toggle_intro'
            );
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => {
                    events.push(
                        'cursor:'
                        + String(kind || '')
                        + ':'
                        + String(options && options.effect || '')
                        + ':'
                        + String(options && options.durationMs || 0)
                    );
                    window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                        detail: {
                            x: 640,
                            y: 430,
                            kind,
                            effect: options && options.effect || '',
                            source: 'external-chat',
                            timestamp: Date.now(),
                        },
                    }));
                },
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.runAvatarFloatingSceneOperation = async (playedScene) => {
                events.push('operation:' + String(playedScene.operation || ''));
                return true;
            };

            await director.playAvatarFloatingScene(scene, 3, 0, 8);
            return events;
        }
        """
    )

    assert "spotlight:input" in result
    assert "cursor:input::0" in result
    assert "spotlight:tool-toggle" not in result
    assert "operation:open-compact-tool-fan" not in result


@pytest.mark.frontend
def test_day2_to_day7_first_line_externalized_chat_uses_input_spotlight_and_cursor(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=(
            "tutorial/yui-guide/overlay.js",
            "tutorial/yui-guide/director.js",
            "tutorial/yui-guide/days/day2-screen-voice-guide.js",
            "tutorial/yui-guide/days/day3-interaction-guide.js",
            "tutorial/yui-guide/days/day4-companion-guide.js",
            "tutorial/yui-guide/days/day5-personalization-guide.js",
            "tutorial/yui-guide/days/day6-agent-guide.js",
            "tutorial/yui-guide/days/day7-graduation-guide.js",
        ),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const expectedFirstSceneIds = {
                2: 'day2_intro_context',
                3: 'day3_tool_toggle_intro',
                4: 'day4_intro_companion',
                5: 'day5_character_settings',
                6: 'day6_intro_agent',
                7: 'day7_memory_review',
            };
            const results = {};
            for (const day of [2, 3, 4, 5, 6, 7]) {
                const director = window.createYuiGuideDirector({ page: 'home' });
                const events = [];
                director.interactionTakeover = {
                    setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                    setExternalizedChatCursor: (kind, options) => {
                        events.push(
                            'cursor:'
                            + String(kind || '')
                            + ':'
                            + String(options && options.effect || '')
                            + ':'
                            + (
                                options && Object.prototype.hasOwnProperty.call(options, 'durationMs')
                                    ? String(options.durationMs)
                                    : 'unset'
                            )
                        );
                        window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                            detail: {
                                x: 640,
                                y: 430,
                                kind,
                                effect: options && options.effect || '',
                                source: 'external-chat',
                                timestamp: Date.now(),
                            },
                        }));
                    },
                };
                director.speakGuideLine = async () => null;
                director.waitForSceneDelay = async () => true;
                director.appendGuideChatMessage = () => {};
                director.applyGuideEmotion = () => {};
                director.prepareAvatarFloatingScene = async () => {};
                director.resolveAvatarFloatingPersistent = async () => null;
                director.resolveAvatarFloatingTarget = async () => null;
                director.runAvatarFloatingSceneOperation = async (playedScene) => {
                    events.push('operation:' + String(playedScene.operation || ''));
                    return true;
                };
                director.enableInterrupts = () => {};
                director.openSettingsPanel = async () => true;
                director.waitForElement = async () => null;
                director.ensureAvatarFloatingSettingsSidePanel = async () => null;
                director.getDay4SettingsButtonSpotlightTarget = () => null;
                director.getDay5CharacterSettingsButtonTarget = () => null;

                const scene = window.YuiGuideDailyGuides[day].round.scenes[0];
                if (scene.id !== expectedFirstSceneIds[day]) {
                    throw new Error('Unexpected first scene for day ' + day + ': ' + scene.id);
                }
                await director.playAvatarFloatingScene(
                    scene,
                    day,
                    0,
                    window.YuiGuideDailyGuides[day].round.scenes.length
                );
                results[day] = events;
            }
            return results;
        }
        """
    )

    for day in ["2", "3", "4", "5", "6", "7"]:
        assert "spotlight:input" in result[day]
        assert "cursor:input::0" in result[day]


@pytest.mark.frontend
def test_day3_avatar_tools_line_moves_to_toggle_and_opens_tool_fan_on_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        id="tool-toggle"
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:absolute; left:260px; top:164px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const toolToggle = document.getElementById('tool-toggle');
            toolToggle.addEventListener('click', () => events.push({ type: 'button:click' }));
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools'
            );
            let releaseClick;
            const clickStarted = new Promise((resolve) => {
                director.clickCursorAndWait = (durationMs) => {
                    events.push({ type: 'click:start', durationMs });
                    resolve();
                    return new Promise((release) => { releaseClick = release; });
                };
            });
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.setCompactToolFanOpen = (open, reason) => {
                events.push({ type: 'toolFan', open, reason });
                return true;
            };
            director.setChatAvatarToolMenuOpen = (open, reason) => {
                events.push({ type: 'avatarMenu', open, reason });
                return true;
            };
            director.applyGuideHighlights = (config) => {
                events.push({
                    type: 'highlight',
                    persistentId: config.persistent && config.persistent.id,
                    primaryId: config.primary && config.primary.id,
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                events.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };

            const playPromise = director.playAvatarFloatingScene(scene, 3, 1, 8);
            await clickStarted;
            const beforeRelease = events.slice();
            releaseClick();
            await playPromise;
            return {
                beforeRelease,
                afterRelease: events,
            };
        }
        """
    )

    assert {"type": "toolFan", "open": True, "reason": "avatar-floating-guide-prepare-tool-fan"} not in result["beforeRelease"]
    assert {
        "type": "highlight",
        "persistentId": "tool-toggle",
        "primaryId": "tool-toggle",
    } in result["beforeRelease"]
    assert {"type": "move", "id": "tool-toggle", "durationMs": 760} in result["beforeRelease"]
    assert {"type": "click:start", "durationMs": 420} in result["beforeRelease"]
    assert {"type": "button:click"} in result["beforeRelease"]
    assert {"type": "toolFan", "open": True, "reason": "avatar-floating-guide-open-tool-fan"} in result["beforeRelease"]
    assert result["beforeRelease"].index({"type": "click:start", "durationMs": 420}) < result["beforeRelease"].index({
        "type": "toolFan",
        "open": True,
        "reason": "avatar-floating-guide-open-tool-fan",
    })
    assert {"type": "avatarMenu", "open": True, "reason": "avatar-floating-guide-open-avatar-tool-menu"} not in result["afterRelease"]


@pytest.mark.frontend
def test_day3_avatar_tools_externalized_moves_to_toggle_and_opens_tool_fan_on_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools'
            );
            const toolFanOpened = new Promise((resolve) => {
                window.__resolveToolFanOpened = resolve;
            });
            director.clickCursorAndWait = async (durationMs) => {
                events.push('click:start:' + String(durationMs || 0));
                return true;
            };
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => {
                    events.push(
                        'cursor:'
                        + String(kind || '')
                        + ':'
                        + String(options && options.effect || '')
                        + ':'
                        + String(options && options.durationMs || 0)
                        + ':'
                        + String(options && options.effectDurationMs || 0)
                    );
                    window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                        detail: {
                            x: 640,
                            y: 430,
                            kind,
                            effect: options && options.effect || '',
                            source: 'external-chat',
                            timestamp: Date.now(),
                        },
                    }));
                },
                setExternalizedChatCompactToolFanOpen: (open, reason) => {
                    events.push('toolFan:' + String(open) + ':' + String(reason || ''));
                    if (open && window.__resolveToolFanOpened) {
                        window.__resolveToolFanOpened();
                    }
                },
                setExternalizedChatAvatarToolMenuOpen: (open, reason) => events.push(
                    'avatarMenu:' + String(open) + ':' + String(reason || '')
                ),
                clickExternalizedChatAvatarToolButton: (reason) => events.push(
                    'avatarClick:' + String(reason || '')
                ),
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};

            const playPromise = director.playAvatarFloatingScene(scene, 3, 1, 8);
            await toolFanOpened;
            const beforeRelease = events.slice();
            await playPromise;
            return {
                beforeRelease,
                afterRelease: events,
            };
        }
        """
    )

    assert "toolFan:true:avatar-floating-guide-prepare-tool-fan" not in result["beforeRelease"]
    assert "spotlight:tool-toggle" in result["beforeRelease"]
    assert "cursor:tool-toggle:move:0:0" in result["beforeRelease"]
    assert "cursor:tool-toggle:click:0:420" not in result["beforeRelease"]
    assert "click:start:420" in result["beforeRelease"]
    assert "toolFan:true:avatar-floating-guide-open-tool-fan" in result["beforeRelease"]
    assert result["beforeRelease"].index("click:start:420") < result["beforeRelease"].index(
        "toolFan:true:avatar-floating-guide-open-tool-fan"
    )
    assert result["afterRelease"].count("click:start:420") == 1
    assert result["afterRelease"].count("cursor:tool-toggle:click:0:420") == 0
    assert "avatarMenu:true:avatar-floating-guide-open-avatar-tool-menu" not in result["afterRelease"]
    assert "spotlight:avatar-tools" not in result["afterRelease"]


@pytest.mark.frontend
def test_day3_avatar_tools_externalized_waits_for_anchor_before_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools'
            );
            let releaseCursorAnchor;
            const cursorAnchorReached = new Promise((resolve) => { releaseCursorAnchor = resolve; });
            director.waitForExternalizedChatCursorMove = async (sceneId, maxWaitMs) => {
                events.push('anchorWait:' + String(sceneId || '') + ':' + String(maxWaitMs || 0));
                return cursorAnchorReached;
            };
            director.waitForSceneDelay = (durationMs) => {
                events.push('delay:' + String(durationMs || 0));
                return Promise.resolve(true);
            };
            director.clickCursorAndWait = async (durationMs) => {
                events.push('click:start:' + String(durationMs || 0));
                return true;
            };
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => events.push(
                    'cursor:'
                    + String(kind || '')
                    + ':'
                    + String(options && options.effect || '')
                    + ':'
                    + String(options && options.durationMs || 0)
                    + ':'
                    + String(options && options.effectDurationMs || 0)
                ),
                setExternalizedChatCompactToolFanOpen: (open, reason) => events.push(
                    'toolFan:' + String(open) + ':' + String(reason || '')
                ),
                setExternalizedChatAvatarToolMenuOpen: (open, reason) => events.push(
                    'avatarMenu:' + String(open) + ':' + String(reason || '')
                ),
                clickExternalizedChatAvatarToolButton: (reason) => events.push(
                    'avatarClick:' + String(reason || '')
                ),
            };
            director.speakGuideLine = async () => null;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};

            const playPromise = director.playAvatarFloatingScene(scene, 3, 1, 8);
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeAnchorRelease = events.slice();
            releaseCursorAnchor(true);
            await playPromise;
            return {
                beforeAnchorRelease,
                afterRelease: events,
            };
        }
        """
    )

    assert "cursor:tool-toggle:move:0:0" in result["beforeAnchorRelease"]
    assert "anchorWait:day3_avatar_tools:0" in result["beforeAnchorRelease"]
    assert "cursor:tool-toggle:click:0:420" not in result["beforeAnchorRelease"]
    assert "click:start:420" not in result["beforeAnchorRelease"]
    assert "toolFan:true:avatar-floating-guide-open-tool-fan" not in result["beforeAnchorRelease"]
    assert "delay:1480" not in result["afterRelease"]
    assert "cursor:tool-toggle:click:0:420" not in result["afterRelease"]
    assert "click:start:420" in result["afterRelease"]
    assert result["afterRelease"].index("anchorWait:day3_avatar_tools:0") < result["afterRelease"].index(
        "click:start:420"
    )
    assert result["afterRelease"].index("click:start:420") < result["afterRelease"].index(
        "toolFan:true:avatar-floating-guide-open-tool-fan"
    )


@pytest.mark.frontend
def test_day3_externalized_click_waits_for_future_anchor_report_before_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools'
            );
            director.currentSceneId = 'day3_avatar_tools';
            director.screenPointToLocalPoint = (point) => ({ x: point.x, y: point.y });
            director.overlay.isPcOverlayActive = () => true;
            director.overlay.getCursorPosition = () => ({ x: 320, y: 280 });
            director.overlay.syncCursorPosition = (x, y) => {
                events.push('sync:' + String(x) + ':' + String(y));
                return true;
            };
            director.cursor.hasPosition = () => true;
            director.cursor.hasVisiblePosition = () => true;
            director.cursor.moveToPoint = async (x, y) => {
                events.push('move:' + String(x) + ':' + String(y));
                return true;
            };
            director.clickCursorAndWait = async (durationMs) => {
                events.push('click:start:' + String(durationMs || 0));
                return true;
            };

            const playPromise = director.moveExternalizedChatCursor(scene, {
                onClickStart: () => events.push('operation:start'),
            });
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeAnchor = events.slice();
            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
                    x: 640,
                    y: 430,
                    kind: 'tool-toggle',
                    effect: '',
                    source: 'external-chat',
                    settled: true,
                    timestamp: Date.now(),
                },
            }));
            await playPromise;
            return {
                beforeAnchor,
                afterAnchor: events,
            };
        }
        """
    )

    assert "click:start:420" not in result["beforeAnchor"]
    assert "operation:start" not in result["beforeAnchor"]
    assert "sync:640:430" in result["afterAnchor"]
    assert "click:start:420" in result["afterAnchor"]
    assert result["afterAnchor"].index("sync:640:430") < result["afterAnchor"].index("click:start:420")
    assert result["afterAnchor"].index("click:start:420") < result["afterAnchor"].index("operation:start")


@pytest.mark.frontend
def test_settled_externalized_anchor_syncs_home_cursor_without_second_move(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.currentSceneId = 'day3_avatar_tools';
            director.overlay.isPcOverlayActive = () => true;
            director.overlay.getCursorPosition = () => ({ x: 320, y: 280 });
            director.overlay.syncCursorPosition = (x, y, visible) => {
                events.push('sync:' + String(x) + ':' + String(y) + ':' + String(visible));
                return true;
            };
            director.cursor.hasPosition = () => true;
            director.cursor.hasVisiblePosition = () => true;
            director.cursor.showAt = (x, y) => events.push('show:' + String(x) + ':' + String(y));
            director.cursor.moveToPoint = async (x, y) => {
                events.push('move:' + String(x) + ':' + String(y));
                return true;
            };

            director.moveHomeCursorToExternalizedChatAnchor(
                { x: 640, y: 430 },
                { kind: 'tool-toggle', effect: '', settled: true }
            );
            await director.waitForExternalizedChatCursorMove('day3_avatar_tools', 50);
            return events;
        }
        """
    )

    assert "sync:640:430:true" in result
    assert all(not event.startswith("move:") for event in result)
    assert all(not event.startswith("show:") for event in result)


@pytest.mark.frontend
def test_day3_externalized_click_uses_cursor_move_helper_like_local_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools'
            );
            director.moveExternalizedChatCursor = async (playedScene, options) => {
                events.push('externalizedMove:' + String(playedScene && playedScene.id || ''));
                const operationPromise = options && typeof options.onClickStart === 'function'
                    ? options.onClickStart({ scene: playedScene })
                    : Promise.resolve();
                events.push('externalizedClickDone');
                await operationPromise;
            };
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => events.push(
                    'cursor:'
                    + String(kind || '')
                    + ':'
                    + String(options && options.effect || '')
                    + ':'
                    + String(options && options.durationMs || 0)
                    + ':'
                    + String(options && options.effectDurationMs || 0)
                ),
                setExternalizedChatCompactToolFanOpen: (open, reason) => events.push(
                    'toolFan:' + String(open) + ':' + String(reason || '')
                ),
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};

            await director.playAvatarFloatingScene(scene, 3, 1, 8);
            return events;
        }
        """
    )

    assert "externalizedMove:day3_avatar_tools" in result
    assert result.index("externalizedMove:day3_avatar_tools") < result.index(
        "toolFan:true:avatar-floating-guide-open-tool-fan"
    )


@pytest.mark.frontend
def test_day3_galgame_entry_drags_wheel_down_and_moves_to_centered_galgame(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        id="tool-toggle"
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:absolute; left:260px; top:164px; width:42px; height:42px;"
                    ></button>
                    <div
                        id="tool-fan"
                        class="compact-input-tool-fan"
                        style="position:absolute; left:220px; top:120px; width:232px; height:232px;"
                    >
                        <button
                            id="galgame"
                            class="compact-input-tool-item-galgame composer-galgame-btn"
                            style="position:absolute; left:164px; top:56px; width:42px; height:42px;"
                        ></button>
                    </div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_galgame_entry'
            );
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.applyGuideHighlights = () => {};
            director.cursor.hasPosition = () => true;
            director.cursor.click = (durationMs) => events.push({ type: 'click', durationMs });
            director.cursor.moveToPoint = async (x, y, options) => {
                events.push({
                    type: 'drag',
                    x: Math.round(x),
                    y: Math.round(y),
                    durationMs: options && options.durationMs || 0,
                });
                return true;
            };
            director.moveCursorToElement = async (element, durationMs) => {
                events.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.setCompactToolFanOpen = (open, reason) => {
                events.push({ type: 'toolFan', open, reason });
                return true;
            };
            director.rotateCompactToolWheelForGuide = (direction, stepCount, reason) => {
                events.push({ type: 'rotate', direction, stepCount, reason });
                return true;
            };

            await director.playAvatarFloatingScene(scene, 3, 3, 8);
            return events;
        }
        """
    )

    assert {"type": "move", "id": "galgame", "durationMs": 760} in result
    assert {"type": "click", "durationMs": 900} in result
    assert {"type": "drag", "x": 405, "y": 297, "durationMs": 260} in result
    assert {
        "type": "rotate",
        "direction": 1,
        "stepCount": 1,
        "reason": "avatar-floating-guide-galgame-drag",
    } in result
    assert result.index({"type": "click", "durationMs": 900}) < result.index({
        "type": "drag",
        "x": 405,
        "y": 297,
        "durationMs": 260,
    })
    assert result.index({
        "type": "rotate",
        "direction": 1,
        "stepCount": 1,
        "reason": "avatar-floating-guide-galgame-drag",
    }) < result.index({"type": "move", "id": "galgame", "durationMs": 520})
    assert result[-1] == {"type": "move", "id": "galgame", "durationMs": 520}


@pytest.mark.frontend
def test_day3_galgame_entry_rotates_wheel_before_local_drag_settles(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        id="tool-toggle"
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:absolute; left:260px; top:164px; width:42px; height:42px;"
                    ></button>
                    <div
                        id="tool-fan"
                        class="compact-input-tool-fan"
                        style="position:absolute; left:220px; top:120px; width:232px; height:232px;"
                    >
                        <button
                            id="galgame"
                            class="compact-input-tool-item-galgame composer-galgame-btn"
                            style="position:absolute; left:164px; top:56px; width:42px; height:42px;"
                        ></button>
                    </div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_galgame_entry'
            );
            let releaseDrag;
            director.waitForSceneDelay = async (durationMs) => {
                events.push({ type: 'delay', durationMs });
                return true;
            };
            director.cursor.click = (durationMs) => events.push({ type: 'click', durationMs });
            director.cursor.moveToPoint = async (x, y, options) => {
                events.push({
                    type: 'dragStart',
                    x: Math.round(x),
                    y: Math.round(y),
                    durationMs: options && options.durationMs || 0,
                });
                return new Promise((resolve) => {
                    releaseDrag = () => {
                        events.push({ type: 'dragEnd' });
                        resolve(true);
                    };
                });
            };
            director.moveCursorToElement = async (element, durationMs) => {
                events.push({ type: 'move', id: element && element.id, durationMs });
                return true;
            };
            director.setCompactToolFanOpen = (open, reason) => {
                events.push({ type: 'toolFan', open, reason });
                return true;
            };
            director.rotateCompactToolWheelForGuide = (direction, stepCount, reason) => {
                events.push({ type: 'rotate', direction, stepCount, reason });
                return true;
            };

            const runPromise = director.runDay3GalgameWheelDragScene(scene, document.getElementById('galgame'));
            await Promise.resolve();
            await Promise.resolve();
            const beforeDragSettles = events.slice();
            releaseDrag();
            const completed = await runPromise;
            return {
                completed,
                beforeDragSettles,
                afterDragSettles: events.slice(),
            };
        }
        """
    )

    rotate = {
        "type": "rotate",
        "direction": 1,
        "stepCount": 1,
        "reason": "avatar-floating-guide-galgame-drag",
    }
    assert result["completed"] is True
    assert {"type": "dragStart", "x": 405, "y": 297, "durationMs": 260} in result["beforeDragSettles"]
    assert {"type": "delay", "durationMs": 57} in result["beforeDragSettles"]
    assert rotate in result["beforeDragSettles"]
    assert {"type": "dragEnd"} not in result["beforeDragSettles"]
    assert result["afterDragSettles"].index(rotate) < result["afterDragSettles"].index({"type": "dragEnd"})


@pytest.mark.frontend
def test_day3_galgame_entry_waits_for_rotated_slot_before_final_local_move(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        id="tool-toggle"
                        class="send-button-circle compact-input-tool-toggle"
                        style="position:absolute; left:260px; top:164px; width:42px; height:42px;"
                    ></button>
                    <div
                        id="tool-fan"
                        class="compact-input-tool-fan"
                        style="position:absolute; left:220px; top:120px; width:232px; height:232px;"
                    >
                        <button
                            id="galgame"
                            class="compact-input-tool-item-galgame composer-galgame-btn"
                            data-compact-tool-wheel-slot="2"
                            style="position:absolute; left:164px; top:56px; width:42px; height:42px;"
                        ></button>
                    </div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_galgame_entry'
            );
            const galgame = document.getElementById('galgame');
            director.waitForSceneDelay = async () => true;
            director.cursor.click = () => {};
            director.cursor.moveToPoint = async () => true;
            director.cursor.moveToRect = async (rect, options) => {
                events.push({
                    type: 'finalMove',
                    x: Math.round(rect.left + rect.width / 2),
                    y: Math.round(rect.top + rect.height / 2),
                    durationMs: options && options.durationMs || 0,
                    slot: galgame && galgame.getAttribute('data-compact-tool-wheel-slot'),
                });
                return true;
            };
            director.setCompactToolFanOpen = () => true;
            director.rotateCompactToolWheelForGuide = () => {
                events.push({ type: 'rotate' });
                window.setTimeout(() => {
                    galgame.setAttribute('data-compact-tool-wheel-slot', '1');
                    galgame.style.left = '124px';
                    galgame.style.top = '96px';
                }, 0);
                return true;
            };

            const completed = await director.runDay3GalgameWheelDragScene(scene, galgame);
            return {
                completed,
                events,
            };
        }
        """
    )

    assert result["completed"] is True
    assert {"type": "rotate"} in result["events"]
    assert result["events"][-1] == {
        "type": "finalMove",
        "x": 365,
        "y": 237,
        "durationMs": 520,
        "slot": "1",
    }


@pytest.mark.frontend
def test_day3_galgame_entry_externalized_drags_wheel_before_final_galgame_move(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_galgame_entry'
            );
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async (durationMs) => {
                events.push({ type: 'delay', durationMs });
                return true;
            };
            director.waitForExternalizedChatCursorMove = async (sceneId, maxWaitMs) => {
                events.push({ type: 'anchorWait', sceneId, maxWaitMs });
                return true;
            };
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.applyGuideHighlights = () => {};
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push({
                    type: 'spotlight',
                    kind,
                }),
                setExternalizedChatCursor: (kind, options) => events.push({
                    type: 'cursor',
                    kind,
                    effect: options && options.effect || '',
                    durationMs: options && options.durationMs || 0,
                    effectDurationMs: options && options.effectDurationMs || 0,
                }),
                setExternalizedChatCompactToolFanOpen: (open, reason) => events.push({
                    type: 'toolFan',
                    open,
                    reason,
                }),
                dragExternalizedChatCursor: (kind, options) => events.push({
                    type: 'drag',
                    kind,
                    deltaY: options && options.deltaY || 0,
                    hasDurationMs: !!(options && Object.prototype.hasOwnProperty.call(options, 'durationMs')),
                    durationMs: options && options.durationMs || 0,
                    effect: options && options.effect || '',
                    effectDurationMs: options && options.effectDurationMs || 0,
                }),
                rotateExternalizedChatCompactToolWheel: (direction, stepCount, reason) => events.push({
                    type: 'rotate',
                    direction,
                    stepCount,
                    reason,
                }),
            };

            await director.playAvatarFloatingScene(scene, 3, 3, 8);
            return events;
        }
        """
    )

    initial_move = {
        "type": "cursor",
        "kind": "galgame",
        "effect": "move",
        "durationMs": 0,
        "effectDurationMs": 0,
    }
    drag = {
        "type": "drag",
        "kind": "galgame",
        "deltaY": 100,
        "hasDurationMs": True,
        "durationMs": 260,
        "effect": "click",
        "effectDurationMs": 900,
    }
    rotate = {
        "type": "rotate",
        "direction": 1,
        "stepCount": 1,
        "reason": "avatar-floating-guide-galgame-drag",
    }
    final_move = {
        "type": "cursor",
        "kind": "galgame",
        "effect": "move",
        "durationMs": 520,
        "effectDurationMs": 0,
    }
    initial_move = dict(initial_move)
    cursor_move_indices = [
        index
        for index, event in enumerate(result)
        if event == initial_move or event == final_move
    ]
    assert len(cursor_move_indices) >= 2
    assert {"type": "anchorWait", "sceneId": "day3_galgame_entry", "maxWaitMs": 1800} in result
    assert {"type": "anchorWait", "sceneId": "day3_galgame_entry", "maxWaitMs": 1020} in result
    assert drag in result
    assert rotate in result
    assert {"type": "delay", "durationMs": 57} in result
    assert cursor_move_indices[0] < result.index({
        "type": "anchorWait",
        "sceneId": "day3_galgame_entry",
        "maxWaitMs": 1800,
    })
    assert result.index({
        "type": "anchorWait",
        "sceneId": "day3_galgame_entry",
        "maxWaitMs": 1800,
    }) < result.index(drag)
    assert result.index(drag) < result.index({"type": "delay", "durationMs": 57})
    assert result.index({"type": "delay", "durationMs": 57}) < result.index(rotate)
    assert final_move in result
    assert result.index(rotate) < cursor_move_indices[-1]
    assert result.index(final_move) < result.index({
        "type": "anchorWait",
        "sceneId": "day3_galgame_entry",
        "maxWaitMs": 1020,
    })
    assert result.index(rotate) < len(result) - 1


@pytest.mark.frontend
def test_externalized_chat_drag_without_duration_uses_default_click_drag_motion(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__externalChatOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__externalChatOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
                relayToPet: () => {},
            };
            window.localStorage.setItem('yuiGuidePcOverlayRunId', 'test-run');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        class="compact-input-tool-item-galgame"
                        style="position:fixed; left:600px; top:400px; width:42px; height:42px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_drag_chat_cursor',
                    kind: 'galgame',
                    deltaY: 48,
                    effect: 'click',
                    effectDurationMs: 900,
                    timestamp: Date.now(),
                    tutorialRunId: 'test-run',
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 80));
            return window.__externalChatOverlayUpdates
                .map((update) => update && update.payload && update.payload.cursor)
                .filter(Boolean);
        }
        """
    )

    assert len(result) >= 2
    assert result[0]["effect"] == "click"
    assert result[0]["durationMs"] == 0
    assert result[-1]["effect"] == "click"
    assert result[-1]["durationMs"] == 260
    assert result[-1]["y"] - result[0]["y"] == 48
    assert result[-1]["effectDurationMs"] == 900


@pytest.mark.frontend
def test_externalized_chat_drag_message_omits_duration_when_not_supplied(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__messages = [];
        """,
        script_names=("tutorial/core/interaction-takeover.js",),
    )

    result = mock_page.evaluate(
        """
        () => {
            const controller = window.TutorialInteractionTakeover.createController({
                page: 'home',
                externalizedChatDetector: () => true,
                externalChatChannelProvider: () => ({
                    postMessage: (message) => window.__messages.push(message),
                }),
            });
            controller.dragExternalizedChatCursor('galgame', {
                deltaY: 48,
                effect: 'click',
                effectDurationMs: 900,
            });
            controller.destroy();
            return window.__messages.find((message) => message.action === 'yui_guide_drag_chat_cursor') || null;
        }
        """
    )

    assert result["kind"] == "galgame"
    assert result["deltaY"] == 48
    assert result["effect"] == "click"
    assert result["effectDurationMs"] == 900
    assert "durationMs" not in result


@pytest.mark.frontend
def test_externalized_compact_tool_wheel_rotate_request_reaches_chat_host(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            window.reactChatWindowHost = {
                rotateCompactToolWheel: (direction, stepCount, reason) => {
                    window.__hostRequests.push({ direction, stepCount, reason });
                },
            };
            document.body.innerHTML = `<div id="react-chat-window-root"></div>`;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_rotate_compact_tool_wheel',
                    direction: 1,
                    stepCount: 2,
                    reason: 'avatar-floating-guide-galgame-drag',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 0));
            return window.__hostRequests;
        }
        """
    )

    assert result == [{
        "direction": 1,
        "stepCount": 2,
        "reason": "avatar-floating-guide-galgame-drag",
    }]


@pytest.mark.frontend
def test_externalized_compact_tool_wheel_rotate_broadcast_reaches_chat_host(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            window.reactChatWindowHost = {
                rotateCompactToolWheel: (direction, stepCount, reason) => {
                    window.__hostRequests.push({ direction, stepCount, reason });
                },
            };
            document.body.innerHTML = `<div id="react-chat-window-root"></div>`;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const channel = new BroadcastChannel('neko_page_channel');
            channel.postMessage({
                action: 'yui_guide_rotate_compact_tool_wheel',
                direction: 1,
                stepCount: 2,
                reason: 'avatar-floating-guide-galgame-drag',
                timestamp: Date.now(),
            });
            await new Promise((resolve) => setTimeout(resolve, 80));
            channel.close();
            return window.__hostRequests;
        }
        """
    )

    assert result == [{
        "direction": 1,
        "stepCount": 2,
        "reason": "avatar-floating-guide-galgame-drag",
    }]


@pytest.mark.frontend
def test_externalized_compact_tool_wheel_index_request_reaches_chat_host(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            window.reactChatWindowHost = {
                setCompactToolWheelIndex: (index, reason) => {
                    window.__hostRequests.push({ index, reason });
                },
            };
            document.body.innerHTML = `<div id="react-chat-window-root"></div>`;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_compact_tool_wheel_index',
                    index: 0,
                    reason: 'avatar-floating-guide-day3-entry-reset',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 0));
            return window.__hostRequests;
        }
        """
    )

    assert result == [{
        "index": 0,
        "reason": "avatar-floating-guide-day3-entry-reset",
    }]


@pytest.mark.frontend
def test_externalized_compact_tool_wheel_rotate_retries_until_chat_host_ready(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            document.body.innerHTML = `<div id="react-chat-window-root"></div>`;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_rotate_compact_tool_wheel',
                    direction: 1,
                    stepCount: 2,
                    reason: 'avatar-floating-guide-galgame-drag',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 80));
            window.reactChatWindowHost = {
                rotateCompactToolWheel: (direction, stepCount, reason) => {
                    window.__hostRequests.push({ direction, stepCount, reason });
                },
            };
            await new Promise((resolve) => setTimeout(resolve, 900));
            return window.__hostRequests;
        }
        """
    )

    assert result == [{
        "direction": 1,
        "stepCount": 2,
        "reason": "avatar-floating-guide-galgame-drag",
    }]


@pytest.mark.frontend
def test_day3_avatar_tools_props_externalized_uses_single_cursor_click_and_opens_menu(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools_props'
            );
            const avatarMenuOpened = new Promise((resolve) => {
                window.__resolveAvatarMenuOpened = resolve;
            });
            director.clickCursorAndWait = async (durationMs) => {
                events.push('click:start:' + String(durationMs || 0));
                return true;
            };
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => {
                    events.push(
                        'cursor:'
                        + String(kind || '')
                        + ':'
                        + String(options && options.effect || '')
                        + ':'
                        + String(options && options.durationMs || 0)
                        + ':'
                        + String(options && options.effectDurationMs || 0)
                    );
                    window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                        detail: {
                            x: 660,
                            y: 420,
                            kind,
                            effect: options && options.effect || '',
                            source: 'external-chat',
                            timestamp: Date.now(),
                        },
                    }));
                },
                setExternalizedChatCompactToolFanOpen: (open, reason) => events.push(
                    'toolFan:' + String(open) + ':' + String(reason || '')
                ),
                setExternalizedChatAvatarToolMenuOpen: (open, reason) => {
                    events.push('avatarMenu:' + String(open) + ':' + String(reason || ''));
                    if (open && window.__resolveAvatarMenuOpened) {
                        window.__resolveAvatarMenuOpened();
                    }
                },
                clickExternalizedChatAvatarToolButton: (reason) => events.push(
                    'avatarClick:' + String(reason || '')
                ),
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.getAvatarFloatingNarrationDurationMs = () => 1000;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};

            const playPromise = director.playAvatarFloatingScene(scene, 3, 2, 8);
            await avatarMenuOpened;
            const beforeRelease = events.slice();
            await playPromise;
            return {
                beforeRelease,
                afterRelease: events,
            };
        }
        """
    )

    assert "spotlight:tool-toggle" in result["beforeRelease"]
    assert "cursor:avatar-tools:move:0:0" in result["beforeRelease"]
    assert "cursor:avatar-tools:click:0:420" not in result["beforeRelease"]
    assert "click:start:420" in result["beforeRelease"]
    assert "toolFan:true:avatar-floating-guide-open-avatar-tool-fan" not in result["beforeRelease"]
    assert "avatarClick:avatar-floating-guide-open-avatar-tool-menu" in result["beforeRelease"]
    assert "avatarMenu:true:avatar-floating-guide-open-avatar-tool-menu" in result["beforeRelease"]
    assert result["beforeRelease"].index("click:start:420") < result["beforeRelease"].index(
        "avatarMenu:true:avatar-floating-guide-open-avatar-tool-menu"
    )
    assert result["afterRelease"].count("click:start:420") == 1
    assert result["afterRelease"].count("cursor:avatar-tools:click:0:420") == 0
    assert "avatarMenu:false:avatar-floating-guide-close-avatar-tool-menu-after-narration" in result["afterRelease"]
    assert "avatarClick:avatar-floating-guide-close-avatar-tool-menu-after-narration" in result["afterRelease"]


@pytest.mark.frontend
def test_day3_avatar_tools_props_externalized_waits_for_cursor_move_before_open_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js", "tutorial/yui-guide/days/day3-interaction-guide.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const scene = window.YuiGuideDailyGuides[3].round.scenes.find(
                (candidate) => candidate.id === 'day3_avatar_tools_props'
            );
            let releaseCursorAnchor;
            const cursorAnchorReached = new Promise((resolve) => { releaseCursorAnchor = resolve; });
            director.waitForExternalizedChatCursorMove = async (sceneId, maxWaitMs) => {
                events.push('anchorWait:' + String(sceneId || '') + ':' + String(maxWaitMs || 0));
                return cursorAnchorReached;
            };
            director.waitForSceneDelay = (durationMs) => {
                events.push('delay:' + String(durationMs || 0));
                return Promise.resolve(true);
            };
            director.clickCursorAndWait = async (durationMs) => {
                events.push('click:start:' + String(durationMs || 0));
                return true;
            };
            director.getAvatarFloatingNarrationDurationMs = () => 1000;
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => events.push(
                    'cursor:'
                    + String(kind || '')
                    + ':'
                    + String(options && options.effect || '')
                    + ':'
                    + String(options && options.durationMs || 0)
                    + ':'
                    + String(options && options.effectDurationMs || 0)
                ),
                setExternalizedChatCompactToolFanOpen: (open, reason) => events.push(
                    'toolFan:' + String(open) + ':' + String(reason || '')
                ),
                setExternalizedChatAvatarToolMenuOpen: (open, reason) => events.push(
                    'avatarMenu:' + String(open) + ':' + String(reason || '')
                ),
                clickExternalizedChatAvatarToolButton: (reason) => events.push(
                    'avatarClick:' + String(reason || '')
                ),
            };
            director.speakGuideLine = async () => null;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};

            const playPromise = director.playAvatarFloatingScene(scene, 3, 2, 8);
            await new Promise((resolve) => setTimeout(resolve, 0));
            const beforeAnchorRelease = events.slice();
            releaseCursorAnchor(true);
            await playPromise;
            return {
                beforeAnchorRelease,
                afterRelease: events,
            };
        }
        """
    )

    assert "cursor:avatar-tools:move:0:0" in result["beforeAnchorRelease"]
    assert "anchorWait:day3_avatar_tools_props:0" in result["beforeAnchorRelease"]
    assert "cursor:avatar-tools:click:0:420" not in result["beforeAnchorRelease"]
    assert "click:start:420" not in result["beforeAnchorRelease"]
    assert "avatarMenu:true:avatar-floating-guide-open-avatar-tool-menu" not in result["beforeAnchorRelease"]
    assert "delay:760" not in result["afterRelease"]
    assert "cursor:avatar-tools:click:0:420" not in result["afterRelease"]
    assert "click:start:420" in result["afterRelease"]
    assert result["afterRelease"].index("anchorWait:day3_avatar_tools_props:0") < result["afterRelease"].index(
        "click:start:420"
    )
    assert result["afterRelease"].index("click:start:420") < result["afterRelease"].index(
        "avatarMenu:true:avatar-floating-guide-open-avatar-tool-menu"
    )


@pytest.mark.frontend
def test_day3_externalized_avatar_tool_menu_operation_does_not_send_cursor_effect(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.waitForSceneDelay = async () => true;
            director.interactionTakeover = {
                setExternalizedChatCursor: (kind, options) => events.push(
                    'cursor:'
                    + String(kind || '')
                    + ':'
                    + String(options && options.effect || '')
                    + ':'
                    + String(options && options.effectDurationMs || 0)
                ),
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCompactToolFanOpen: (open, reason) => events.push(
                    'toolFan:' + String(open) + ':' + String(reason || '')
                ),
                setExternalizedChatAvatarToolMenuOpen: (open, reason) => events.push(
                    'avatarMenu:' + String(open) + ':' + String(reason || '')
                ),
            };

            await director.runAvatarFloatingSceneOperation({
                id: 'day3_avatar_tools_props',
                persistent: 'chat-tool-toggle',
                operation: 'open-avatar-tool-menu',
            }, null, Date.now());
            return events;
        }
        """
    )

    assert "cursor:avatar-tools:wobble:0" not in result
    assert "cursor:avatar-tools:click:0" not in result
    assert "avatarMenu:true:avatar-floating-guide-open-avatar-tool-menu" in result


@pytest.mark.frontend
def test_externalized_compact_tool_fan_request_opens_fan_immediately_when_toggle_disabled(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            window.reactChatWindowHost = {
                setCompactToolFanOpen: (open, reason) => {
                    window.__hostRequests.push({ open, reason });
                    const fan = document.querySelector('.compact-input-tool-fan');
                    if (fan) {
                        fan.dataset.compactInputToolFanOpen = open ? 'true' : 'false';
                    }
                },
            };
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        class="send-button-circle compact-input-tool-toggle"
                        aria-expanded="false"
                        disabled
                        style="position:absolute; left:100px; top:100px; width:40px; height:40px;"
                    ></button>
                    <div
                        class="compact-input-tool-fan"
                        data-compact-input-tool-fan-open="false"
                    ></div>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_compact_tool_fan_open',
                    open: true,
                    reason: 'avatar-floating-guide-open-avatar-tool-fan',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                hostRequests: window.__hostRequests,
                fanOpen: document.querySelector('.compact-input-tool-fan')
                    .dataset.compactInputToolFanOpen,
            };
        }
        """
    )

    assert result["hostRequests"] == [{
        "open": True,
        "reason": "avatar-floating-guide-open-avatar-tool-fan",
    }]
    assert result["fanOpen"] == "true"


@pytest.mark.frontend
def test_externalized_avatar_tool_menu_request_opens_menu_when_button_disabled(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            window.__buttonClicks = 0;
            window.reactChatWindowHost = {
                setAvatarToolMenuOpen: (open, reason) => {
                    window.__hostRequests.push({ open, reason });
                    if (open) {
                        const popover = document.createElement('div');
                        popover.id = 'composer-tool-popover-compact';
                        document.getElementById('react-chat-window-root').appendChild(popover);
                    } else {
                        const popover = document.getElementById('composer-tool-popover-compact');
                        if (popover) {
                            popover.remove();
                        }
                    }
                },
            };
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div class="compact-input-tool-item-avatar" data-compact-tool-fan-interactive="true">
                        <button
                            class="composer-emoji-btn"
                            disabled
                            style="position:absolute; left:100px; top:100px; width:40px; height:40px;"
                            onclick="window.__buttonClicks += 1"
                        ></button>
                    </div>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_set_avatar_tool_menu_open',
                    open: true,
                    reason: 'avatar-floating-guide-open-avatar-tool-menu',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                hostRequests: window.__hostRequests,
                buttonClicks: window.__buttonClicks,
                menuOpen: !!document.getElementById('composer-tool-popover-compact'),
            };
        }
        """
    )

    assert result["buttonClicks"] == 0
    assert result["hostRequests"] == [{
        "open": True,
        "reason": "avatar-floating-guide-open-avatar-tool-menu",
    }]
    assert result["menuOpen"] is True


@pytest.mark.frontend
def test_externalized_avatar_tool_menu_request_replays_after_early_relay_duplicate(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const timestamp = Date.now();
            const payload = {
                action: 'yui_guide_set_avatar_tool_menu_open',
                open: true,
                reason: 'avatar-floating-guide-open-avatar-tool-menu',
                timestamp,
            };
            window.dispatchEvent(new CustomEvent('neko:tutorial-overlay-relay', { detail: payload }));
            window.reactChatWindowHost = {
                setAvatarToolMenuOpen: (open, reason) => {
                    window.__hostRequests.push({ open, reason });
                    if (open) {
                        const popover = document.createElement('div');
                        popover.id = 'composer-tool-popover-compact';
                        document.getElementById('react-chat-window-root').appendChild(popover);
                    }
                },
            };
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div class="compact-input-tool-item-avatar" data-compact-tool-fan-interactive="true">
                        <button
                            class="composer-emoji-btn"
                            disabled
                            style="position:absolute; left:100px; top:100px; width:40px; height:40px;"
                        ></button>
                    </div>
                </div>
            `;
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload,
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                hostRequests: window.__hostRequests,
                menuOpen: !!document.getElementById('composer-tool-popover-compact'),
            };
        }
        """
    )

    assert result["hostRequests"] == [{
        "open": True,
        "reason": "avatar-floating-guide-open-avatar-tool-menu",
    }]
    assert result["menuOpen"] is True


@pytest.mark.frontend
def test_externalized_avatar_tool_click_request_triggers_button_click_without_host_fallback(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/chat');
            window.__hostRequests = [];
            window.__buttonClicks = 0;
            window.reactChatWindowHost = {
                setAvatarToolMenuOpen: (open, reason) => {
                    window.__hostRequests.push({ open, reason });
                },
            };
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div class="compact-input-tool-item-avatar">
                        <button
                            class="composer-emoji-btn"
                            style="position:absolute; left:100px; top:100px; width:40px; height:40px;"
                            onclick="window.__buttonClicks += 1"
                        ></button>
                    </div>
                </div>
            `;
        """,
        script_names=("app-interpage.js",),
    )

    result = mock_page.evaluate(
        """
        async () => {
            window.postMessage({
                __nekoTutorialOverlayRelay: true,
                payload: {
                    action: 'yui_guide_click_avatar_tool_button',
                    reason: 'avatar-floating-guide-open-avatar-tool-menu',
                    timestamp: Date.now(),
                },
            }, '*');
            await new Promise((resolve) => setTimeout(resolve, 0));
            return {
                hostRequests: window.__hostRequests,
                buttonClicks: window.__buttonClicks,
            };
        }
        """
    )

    assert result["buttonClicks"] == 1
    assert result["hostRequests"] == []


@pytest.mark.frontend
def test_avatar_floating_avatar_tool_menu_api_fires_with_cursor_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button class="composer-emoji-btn" style="position:absolute; left:80px; top:80px; width:40px; height:40px;"></button>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            let releaseClick;
            const clickStarted = new Promise((resolve) => {
                director.clickCursorAndWait = () => {
                    events.push('click:start');
                    resolve();
                    return new Promise((release) => {
                        releaseClick = release;
                    });
                };
            });
            director.cursor.hasPosition = () => true;
            director.moveCursorToElement = async () => {
                events.push('move');
                return true;
            };
            director.setChatAvatarToolMenuOpen = (open, reason) => {
                events.push('menu:' + String(open) + ':' + String(reason));
                return true;
            };

            const primaryTarget = document.querySelector('.composer-emoji-btn');
            const movePromise = director.moveAvatarFloatingCursor({
                id: 'day3_avatar_tools_props',
                operation: 'open-avatar-tool-menu',
                cursorAction: 'click',
            }, primaryTarget, null, 'day3_avatar_tools');

            await clickStarted;
            const eventsBeforeClickRelease = events.slice();
            releaseClick();
            await movePromise;

            return {
                eventsBeforeClickRelease,
                eventsAfterClickRelease: events.slice(),
            };
        }
        """
    )

    assert result["eventsBeforeClickRelease"] == [
        "move",
        "click:start",
        "menu:true:avatar-floating-guide-open-avatar-tool-menu",
    ]
    assert result["eventsAfterClickRelease"] == result["eventsBeforeClickRelease"]


@pytest.mark.frontend
def test_avatar_floating_click_scene_operation_starts_with_cursor_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="click-target" style="position:absolute; left:80px; top:80px; width:40px; height:40px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const target = document.getElementById('click-target');
            const events = [];
            let releaseClick;
            const clickStarted = new Promise((resolve) => {
                director.clickCursorAndWait = () => {
                    events.push('click:start');
                    resolve();
                    return new Promise((release) => {
                        releaseClick = release;
                    });
                };
            });
            director.cursor.hasPosition = () => true;
            director.moveCursorToElement = async () => {
                events.push('move');
                return true;
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.prepareAvatarFloatingScene = async () => true;
            director.resolveAvatarFloatingPersistent = async () => null;
            director.resolveAvatarFloatingTarget = async (scene, role) => role === 'primary' ? target : null;
            director.applyGuideHighlights = () => {};
            director.enableInterrupts = () => {};
            director.runAvatarFloatingSceneOperation = async (scene) => {
                events.push('operation:' + String(scene.operation || ''));
                return true;
            };

            const scenePromise = director.playAvatarFloatingScene({
                id: 'test_click_scene',
                target: '#click-target',
                cursorAction: 'click',
                operation: 'open-agent',
            }, 2, 0, 1);

            await clickStarted;
            const eventsBeforeClickRelease = events.slice();
            releaseClick();
            await scenePromise;

            return {
                eventsBeforeClickRelease,
                eventsAfterClickRelease: events.slice(),
            };
        }
        """
    )

    assert result["eventsBeforeClickRelease"] == [
        "move",
        "click:start",
        "operation:open-agent",
    ]
    assert result["eventsAfterClickRelease"] == result["eventsBeforeClickRelease"]


@pytest.mark.frontend
def test_day1_externalized_history_click_starts_operation_with_externalized_click(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: () => Promise.resolve({ ok: true }),
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const operationStarted = new Promise((resolve) => {
                window.__resolveOperationStarted = resolve;
            });
            director.clickCursorAndWait = (durationMs) => {
                events.push('click:start:' + String(durationMs || 0));
                return Promise.resolve(true);
            };
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + kind),
                setExternalizedChatCursor: (kind, options) => {
                    events.push(
                        'cursor:'
                        + kind
                        + ':'
                        + String(options && options.effect || '')
                        + ':'
                        + String(options && options.effectDurationMs || 0)
                    );
                    window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                        detail: {
                            x: 640,
                            y: 430,
                            kind,
                            effect: '',
                            effectDurationMs: 0,
                            source: 'external-chat',
                            timestamp: Date.now(),
                        },
                    }));
                },
            };
            director.cursor.showAt(500, 360);
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.runAvatarFloatingSceneOperation = async (scene) => {
                events.push('operation:' + String(scene.operation || ''));
                if (window.__resolveOperationStarted) {
                    window.__resolveOperationStarted();
                }
                return true;
            };

            const scenePromise = director.playAvatarFloatingScene({
                id: 'day1_history_handle',
                text: '戳一下聊天框上面的【蓝色小条条】，就能看到我们最近聊过的话题啦！',
                voiceKey: 'day1_history_handle',
                target: 'chat-input',
                cursorTarget: 'chat-history-handle',
                cursorAction: 'click',
                operation: 'open-compact-history-during-narration',
            }, 1, 2, 8);

            await operationStarted;
            const eventsBeforeClickRelease = events.slice();
            await scenePromise;

            return {
                eventsBeforeClickRelease,
                eventsAfterClickRelease: events.slice(),
            };
        }
        """
    )

    events_before_click_release = result["eventsBeforeClickRelease"]
    assert "cursor:history:move:0" in events_before_click_release
    assert "cursor:history:click:420" in events_before_click_release
    assert "click:start:420" not in events_before_click_release
    assert "operation:open-compact-history-during-narration" in events_before_click_release
    assert events_before_click_release.index("cursor:history:move:0") < events_before_click_release.index(
        "cursor:history:click:420"
    )
    assert events_before_click_release.index("cursor:history:click:420") < events_before_click_release.index(
        "operation:open-compact-history-during-narration"
    )
    assert result["eventsAfterClickRelease"] == events_before_click_release


@pytest.mark.frontend
def test_day1_externalized_capsule_and_history_do_not_spotlight_chat_input(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            document.body.innerHTML = `<div id="react-chat-window-overlay" style="display:none;"></div>`;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind || '')),
                setExternalizedChatCursor: (kind, options) => {
                    events.push('cursor:' + String(kind || '') + ':' + String(options && options.effect || ''));
                    window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                        detail: {
                            x: kind === 'history' ? 640 : 520,
                            y: kind === 'history' ? 430 : 390,
                            kind,
                            effect: options && options.effect || '',
                            source: 'external-chat',
                            timestamp: Date.now(),
                        },
                    }));
                },
            };
            director.speakGuideLine = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.prepareAvatarFloatingScene = async () => {};
            director.enableInterrupts = () => {};
            director.runAvatarFloatingSceneOperation = async () => true;
            director.clickCursorAndWait = async () => {};

            await director.playAvatarFloatingScene({
                id: 'day1_capsule_drag_hint',
                text: '把鼠标移到这里，长按就可以拉着聊天框到处跑啦~ 点击一下就能随时发消息给我哦！',
                target: 'chat-capsule-input',
                cursorAction: 'wobble',
                cursorWobbleDurationMs: 2000,
                spotlight: false,
            }, 1, 1, 8);
            await director.playAvatarFloatingScene({
                id: 'day1_history_handle',
                text: '戳一下聊天框上面的【蓝色小条条】，就能看到我们最近聊过的话题啦！',
                target: 'chat-input',
                cursorTarget: 'chat-history-handle',
                cursorAction: 'click',
                operation: 'open-compact-history-during-narration',
                spotlight: false,
            }, 1, 2, 8);

            return events;
        }
        """
    )

    assert "spotlight:input" not in result
    assert "cursor:capsule-input:wobble" in result
    assert "cursor:history:move" in result


@pytest.mark.frontend
def test_day1_takeover_operation_uses_round_operation_registry(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.sceneRunId = 42;
            director.getAgentSwitchSnapshot = async () => ({ agent: false });
            director.clearExternalizedChatGuideTarget = () => events.push('clear-external-chat');
            director.runTakeoverKeyboardControlSequence = async (step, performance, runId) => {
                events.push('keyboard:' + String(step && step.anchor || ''));
                events.push('voice:' + String(performance && performance.voiceKey || ''));
                events.push('run:' + String(runId));
                return true;
            };

            const keepGoing = await director.runAvatarFloatingSceneOperation({
                id: 'day1_takeover_capture_cursor',
                target: '#live2d-btn-agent',
                cursorTarget: '#live2d-btn-agent',
                voiceKey: 'takeover_capture_cursor',
                operation: 'day1-managed-scene:takeover_capture_cursor',
            }, null, Date.now(), Promise.resolve());

            return {
                keepGoing,
                sceneRunId: director.sceneRunId,
                events,
            };
        }
        """
    )

    assert result["keepGoing"] is True
    assert result["sceneRunId"] == 43
    assert result["events"] == [
        "keyboard:#live2d-btn-agent",
        "voice:takeover_capture_cursor",
        "run:42",
    ]


@pytest.mark.frontend
def test_day1_takeover_capture_cursor_does_not_highlight_chat_capsule(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:20px; top:20px; width:280px; height:48px;"
                    ></div>
                </div>
                <button id="live2d-btn-agent" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const events = [];
            const registry = {
                getStep: (stepId) => stepId === 'takeover_capture_cursor' ? {
                    page: 'home',
                    anchor: '#live2d-btn-agent',
                    performance: {
                        bubbleText: '超级魔法开关出现！',
                        voiceKey: 'takeover_capture_cursor',
                        emotion: 'happy',
                        cursorAction: 'click',
                        cursorTarget: '#live2d-btn-agent',
                        interruptible: true,
                    },
                    interrupts: {},
                } : null,
            };
            const director = window.createYuiGuideDirector({ page: 'home', registry });
            const persistentSpotlights = [];
            const realPersistentSpotlight = director.overlay.setPersistentSpotlight.bind(director.overlay);
            director.overlay.setPersistentSpotlight = (target) => {
                persistentSpotlights.push(target ? target.id || target.getAttribute('data-compact-geometry-item') || target.tagName : '');
                return realPersistentSpotlight(target);
            };
            director.syncPersistentGhostCursorLookAtForScene = async () => null;
            director.ensurePersistentGhostCursorLookAtPerformance = async () => null;
            director.stopPersistentGhostCursorLookAtPerformance = async () => null;
            director.stopPluginDashboardCornerPeekPerformance = async () => null;
            director.speakGuideLine = async () => null;
            director.runTakeoverKeyboardControlSequence = async () => null;
            director.waitForSceneDelay = async () => true;
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.enableInterrupts = () => {};
            director.clearRetainedExtraSpotlights = () => {};
            director.clearVirtualSpotlight = (key) => events.push('clear-virtual:' + key);
            director.highlightChatWindow = () => events.push('highlight-chat-window');

            await director.playAvatarFloatingScene({
                id: 'day1_takeover_capture_cursor',
                text: '超级魔法开关出现！',
                voiceKey: 'takeover_capture_cursor',
                emotion: 'happy',
                target: '#live2d-btn-agent',
                cursorTarget: '#live2d-btn-agent',
                cursorAction: 'click',
                operation: 'day1-managed-scene:takeover_capture_cursor',
            }, 1, 7, 9);

            return { events, persistentSpotlights };
        }
        """
    )

    assert "highlight-chat-window" not in result["events"]
    assert "compact-chat-input" not in result["persistentSpotlights"]


@pytest.mark.frontend
def test_day1_intro_greeting_restore_keeps_capsule_spotlight_target(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <div
                        id="compact-chat-input"
                        data-compact-geometry-owner="surface"
                        data-compact-geometry-item="input"
                        style="position:absolute; left:20px; top:20px; width:280px; height:48px;"
                    ></div>
                    <div
                        id="compact-chat-capsule"
                        data-compact-geometry-part="capsuleBody"
                        style="position:absolute; left:80px; top:90px; width:360px; height:56px;"
                    ></div>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const persistentSpotlights = [];
            director.overlay.setPersistentSpotlight = (target) => {
                persistentSpotlights.push(target ? target.id : '');
            };
            director.overlay.activateSpotlight = () => {};
            director.overlay.clearActionSpotlight = () => {};
            director.overlay.clearPersistentSpotlight = () => {};
            director.overlay.showBubble = () => {};
            director.applyCircularFloatingButtonSpotlightHint = () => {};
            director.appendGuideChatMessage = () => {};
            director.applyGuideEmotion = () => {};
            director.currentSceneId = 'day1_intro_greeting';
            director.currentStep = {
                performance: {
                    bubbleText: 'hello',
                    emotion: 'happy',
                    cursorTarget: 'chat-capsule-input',
                },
                anchor: 'chat-capsule-input',
            };

            director.restoreCurrentScenePresentation({});

            return { persistentSpotlights };
        }
        """
    )

    assert result["persistentSpotlights"] == ["compact-chat-capsule"]


@pytest.mark.frontend
def test_day1_intro_basic_voice_waits_for_history_cursor_move_before_voice_button(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-mic" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            let releaseHistoryMove;
            director.latestExternalizedChatCursorMoveSceneId = 'day1_history_handle';
            director.latestExternalizedChatCursorMovePromise = new Promise((resolve) => {
                releaseHistoryMove = () => {
                    events.push('history:move:done');
                    resolve(true);
                };
            });
            director.avatarFloatingSceneCursorAnchorPoints.day1_history_handle = { x: 540, y: 380 };
            director.moveCursorToElement = async (element) => {
                events.push('move-to:' + element.id);
                return true;
            };
            director.waitForSceneDelay = async () => true;

            const showcasePromise = director.runIntroVoiceControlButtonShowcase(
                'intro_basic',
                '这里有一个神奇的按钮！'
            );
            await Promise.resolve();
            const beforeRelease = events.slice();
            releaseHistoryMove();
            await showcasePromise;

            return {
                beforeRelease,
                afterRelease: events,
            };
        }
        """
    )

    assert result["beforeRelease"] == []
    assert result["afterRelease"] == [
        "history:move:done",
        "move-to:live2d-btn-mic",
    ]


@pytest.mark.frontend
def test_day1_history_to_intro_basic_voice_preserves_externalized_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return director.shouldPreserveExternalizedChatCursor(
                'day1_history_handle',
                { id: 'day1_intro_basic_voice' }
            );
        }
        """
    )

    assert result is True


@pytest.mark.frontend
def test_day1_intro_basic_voice_to_screen_entry_preserves_externalized_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return director.shouldPreserveExternalizedChatCursor(
                'day1_intro_basic_voice',
                { id: 'day1_screen_entry' }
            );
        }
        """
    )

    assert result is True


@pytest.mark.frontend
def test_day1_screen_entry_invite_preserves_externalized_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return director.shouldPreserveExternalizedChatCursor(
                'day1_screen_entry',
                { id: 'day1_screen_entry_invite' }
            );
        }
        """
    )

    assert result is True


@pytest.mark.frontend
def test_day1_screen_entry_invite_to_takeover_capture_preserves_externalized_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            return director.shouldPreserveExternalizedChatCursor(
                'day1_screen_entry_invite',
                { id: 'day1_takeover_capture_cursor' }
            );
        }
        """
    )

    assert result is True


@pytest.mark.frontend
def test_day1_takeover_capture_from_screen_entry_invite_does_not_clear_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="window.history.pushState({}, '', '/');",
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.sceneRunId = 42;
            director.getAgentSwitchSnapshot = async () => ({ agent: false });
            director.clearExternalizedChatGuideTarget = () => events.push('clear-external-chat');
            director.runTakeoverKeyboardControlSequence = async (step, performance, runId) => {
                events.push('keyboard:' + String(step && step.anchor || ''));
                events.push('voice:' + String(performance && performance.voiceKey || ''));
                events.push('run:' + String(runId));
                return true;
            };

            await director.playAvatarFloatingScene({
                id: 'day1_takeover_capture_cursor',
                target: '#live2d-btn-agent',
                cursorTarget: '#live2d-btn-agent',
                cursorAction: 'click',
                voiceKey: 'takeover_capture_cursor',
                operation: 'day1-managed-scene:takeover_capture_cursor',
            }, 1, 7, 9);

            return events;
        }
        """
    )

    assert result == [
        "keyboard:#live2d-btn-agent",
        "voice:takeover_capture_cursor",
        "run:43",
    ]


@pytest.mark.frontend
def test_normal_externalized_panel_cleanup_preserves_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind)),
                setExternalizedChatCursor: (kind) => events.push('cursor:' + String(kind)),
            };
            director.closeChatToolPopover = () => events.push('close-tool-popover');
            director.collapseAvatarFloatingSidePanelsExcept = () => events.push('collapse-sidepanels');
            director.clearSceneExtraSpotlights = () => {};
            director.clearRetainedExtraSpotlights = () => {};
            director.clearSpotlightGeometryHints = () => {};
            director.clearSpotlightVariantHints = () => {};
            director.overlay.clearActionSpotlight = () => {};
            director.closeManagedPanels = async () => {};
            director.collapseAgentSidePanel = () => {};
            director.collapseCharacterSettingsSidePanel = () => {};

            await director.closeAvatarFloatingGuidePanels();
            return events;
        }
        """
    )

    assert "spotlight:" in result
    assert "cursor:" not in result


@pytest.mark.frontend
def test_exit_externalized_panel_cleanup_clears_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.interactionTakeover = {
                setExternalizedChatSpotlight: (kind) => events.push('spotlight:' + String(kind)),
                setExternalizedChatCursor: (kind) => events.push('cursor:' + String(kind)),
            };
            director.closeChatToolPopover = () => {};
            director.collapseAvatarFloatingSidePanelsExcept = () => {};
            director.clearSceneExtraSpotlights = () => {};
            director.clearRetainedExtraSpotlights = () => {};
            director.clearSpotlightGeometryHints = () => {};
            director.clearSpotlightVariantHints = () => {};
            director.overlay.clearActionSpotlight = () => {};
            director.closeManagedPanels = async () => {};
            director.collapseAgentSidePanel = () => {};
            director.collapseCharacterSettingsSidePanel = () => {};

            await director.closeAvatarFloatingGuidePanels({ clearCursor: true });
            return events;
        }
        """
    )

    assert "spotlight:" in result
    assert "cursor:" in result


@pytest.mark.frontend
def test_cross_window_handoff_does_not_hide_pc_overlay_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            director.overlay.isPcOverlayActive = () => true;
            director.overlay.hideCursor = () => events.push('hide-cursor');
            director.cursor.cancel = () => events.push('cancel-cursor');
            director.cursor.clearPosition = () => events.push('clear-position');

            director.hideHomeCursorForExternalizedChat();
            return events;
        }
        """
    )

    assert result == ["cancel-cursor", "clear-position"]


@pytest.mark.frontend
def test_externalized_chat_handoff_forgets_home_pc_cursor_cache(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__homeOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__homeOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <section id="agent-task-hud" style="position:absolute; left:650px; top:80px; width:260px; height:140px;"></section>
                <div
                    id="compact-chat-input"
                    data-compact-geometry-owner="surface"
                    data-compact-geometry-item="input"
                    style="position:absolute; left:140px; top:250px; width:360px; height:60px;"
                ></div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.interactionTakeover = {
                setExternalizedChatSpotlight: () => {},
                setExternalizedChatCursor: () => {},
            };
            director.cursor.showAt(780, 150);
            director.setExternalizedChatGuideTarget('input', { effect: 'move' });
            director.overlay.setPersistentSpotlight(document.getElementById('compact-chat-input'));
            await new Promise((resolve) => setTimeout(resolve, 30));
            return window.__homeOverlayUpdates.map((update) => update.payload || {});
        }
        """
    )

    assert any(
        payload.get("cursor", {}).get("x") == 880
        and payload["cursor"].get("y") == 200
        for payload in result
    )
    assert result[-1].get("spotlights")
    assert "cursor" not in result[-1]


@pytest.mark.frontend
def test_home_owned_cursor_move_reenables_pc_overlay_after_externalized_handoff(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__homeOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__homeOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <section id="agent-task-hud" style="position:absolute; left:650px; top:80px; width:260px; height:140px;"></section>
                <div
                    id="compact-chat-input"
                    data-compact-geometry-owner="surface"
                    data-compact-geometry-item="input"
                    style="position:absolute; left:140px; top:250px; width:360px; height:60px;"
                ></div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.interactionTakeover = {
                setExternalizedChatSpotlight: () => {},
                setExternalizedChatCursor: () => {},
            };
            director.waitUntilSceneResumed = async () => {};
            director.cursor.showAt(320, 280);
            director.setExternalizedChatGuideTarget('input', { effect: 'move' });
            await director.moveCursorToElement(
                document.getElementById('agent-task-hud'),
                0,
                { exactDuration: true }
            );
            await new Promise((resolve) => setTimeout(resolve, 30));
            return window.__homeOverlayUpdates.map((update) => update.payload || {});
        }
        """
    )

    assert result[-1].get("cursor", {}).get("x") == 880
    assert result[-1].get("cursor", {}).get("y") == 200


@pytest.mark.frontend
def test_day1_screen_entry_starts_from_intro_basic_voice_anchor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="live2d-btn-screen" style="position:absolute; left:320px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.avatarFloatingSceneCursorAnchorPoints.day1_intro_basic_voice = { x: 242, y: 202 };
            const screenButton = document.getElementById('live2d-btn-screen');
            return director.resolveAvatarFloatingCursorStartPoint(
                { id: 'day1_screen_entry' },
                [screenButton],
                'day1_intro_basic_voice'
            );
        }
        """
    )

    assert result == {"x": 242, "y": 202}


@pytest.mark.frontend
def test_day1_intro_basic_voice_sends_pc_overlay_move_from_history_to_voice_button(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            window.__NEKO_MULTI_WINDOW__ = true;
            window.__pcOverlayUpdates = [];
            window.nekoTutorialOverlay = {
                getWindowMetricsSync: () => ({
                    bounds: { x: 100, y: 50, width: 1200, height: 800 },
                    contentBounds: { x: 100, y: 50, width: 1200, height: 800 },
                    zoomFactor: 1,
                }),
                update: (payload) => {
                    window.__pcOverlayUpdates.push(payload);
                    return Promise.resolve({ ok: true });
                },
                begin: () => Promise.resolve({ ok: true }),
                clear: () => Promise.resolve({ ok: true }),
            };
            document.body.innerHTML = `
                <button id="live2d-btn-mic" style="position:absolute; left:220px; top:180px; width:44px; height:44px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.currentSceneId = 'day1_history_handle';
            director.cursor.showAt(500, 360);
            window.dispatchEvent(new CustomEvent('neko:yui-guide:external-chat-cursor-anchor', {
                detail: {
                    x: 640,
                    y: 430,
                    kind: 'history',
                    effect: 'move',
                    source: 'external-chat',
                    timestamp: Date.now(),
                },
            }));
            await director.waitForExternalizedChatCursorMove('day1_history_handle', 1800);
            director.currentSceneId = 'day1_intro_basic_voice';
            await director.runIntroVoiceControlButtonShowcase(
                'intro_basic',
                '这里有一个神奇的按钮！'
            );
            const cursorUpdates = window.__pcOverlayUpdates
                .map((update) => update && update.payload && update.payload.cursor)
                .filter(Boolean);
            return {
                cursorUpdates,
                currentPosition: director.overlay.getCursorPosition(),
            };
        }
        """
    )

    assert result["currentPosition"] == {"x": 242, "y": 202}
    assert any(
        update["visible"] is True
        and update["x"] == 342
        and update["y"] == 252
        and update["durationMs"] >= 900
        for update in result["cursorUpdates"]
    )


@pytest.mark.frontend
def test_highlighted_api_click_starts_action_with_cursor_click(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <button id="click-target" style="position:absolute; left:80px; top:80px; width:40px; height:40px;"></button>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const target = document.getElementById('click-target');
            const events = [];
            let releaseClickDelay;
            director.sceneRunId = 1;
            director.waitForSceneDelay = () => new Promise((resolve) => {
                releaseClickDelay = resolve;
            });
            director.moveCursorToElement = async () => {
                events.push('move');
                return true;
            };
            director.applyGuideHighlights = () => {};
            director.cursor.click = () => {
                events.push('click:start');
            };
            const clickFlow = director.performHighlightedApiClick({
                target,
                runId: 1,
                action: () => {
                    events.push('api:start');
                    return true;
                },
            });

            await new Promise((resolve) => setTimeout(resolve, 0));
            const eventsBeforeClickRelease = events.slice();
            releaseClickDelay(true);
            const result = await clickFlow;

            return {
                result,
                eventsBeforeClickRelease,
                eventsAfterClickRelease: events.slice(),
            };
        }
        """
    )

    assert result["result"] is True
    assert result["eventsBeforeClickRelease"] == [
        "move",
        "click:start",
        "api:start",
    ]
    assert result["eventsAfterClickRelease"] == result["eventsBeforeClickRelease"]


@pytest.mark.frontend
def test_avatar_floating_open_avatar_tool_menu_retries_until_three_tools_visible(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button class="composer-emoji-btn" style="position:absolute; left:80px; top:80px; width:40px; height:40px;"></button>
                </div>
            `;
            window.__avatarToolMenuOpenRequests = [];
            window.reactChatWindowHost = {
                setAvatarToolMenuOpen: (open, reason) => {
                    window.__avatarToolMenuOpenRequests.push({ open, reason });
                    if (!open) {
                        const existing = document.getElementById('composer-tool-popover');
                        if (existing) existing.remove();
                        return;
                    }
                    if (window.__avatarToolMenuOpenRequests.filter((request) => request.open).length < 2) {
                        return;
                    }
                    const popover = document.createElement('div');
                    popover.id = 'composer-tool-popover';
                    popover.style.position = 'absolute';
                    popover.style.left = '130px';
                    popover.style.top = '80px';
                    popover.style.width = '180px';
                    popover.style.height = '60px';
                    ['lollipop', 'fist', 'hammer'].forEach((toolId, index) => {
                        const button = document.createElement('button');
                        button.className = 'composer-icon-button';
                        button.dataset.avatarToolId = toolId;
                        button.style.position = 'absolute';
                        button.style.left = String(index * 54) + 'px';
                        button.style.top = '4px';
                        button.style.width = '44px';
                        button.style.height = '44px';
                        popover.appendChild(button);
                    });
                    document.getElementById('react-chat-window-root').appendChild(popover);
                },
            };
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            director.waitForSceneDelay = async () => true;
            director.cursor.wobble = () => {};
            director.keepAvatarToolButtonHighlightedAfterMenuOpen = () => true;
            const primaryTarget = document.querySelector('.composer-emoji-btn');
            const opened = await director.runAvatarFloatingSceneOperation({
                id: 'day3_avatar_tools_props',
                operation: 'open-avatar-tool-menu',
            }, primaryTarget, Date.now());
            return {
                opened,
                requests: window.__avatarToolMenuOpenRequests,
                toolCount: document.querySelectorAll('#composer-tool-popover .composer-icon-button[data-avatar-tool-id]').length,
            };
        }
        """
    )

    assert result["opened"] is True
    assert result["toolCount"] == 3
    assert result["requests"] == [
        {
            "open": True,
            "reason": "avatar-floating-guide-open-avatar-tool-menu",
        },
        {
            "open": True,
            "reason": "avatar-floating-guide-open-avatar-tool-menu-retry",
        },
    ]


@pytest.mark.frontend
def test_day3_avatar_tools_props_opens_tools_on_click_then_closes_after_narration(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.history.pushState({}, '', '/');
            document.body.innerHTML = `
                <div id="react-chat-window-root">
                    <button
                        id="avatar-tool"
                        class="compact-input-tool-item-avatar composer-emoji-btn"
                        style="position:absolute; left:318px; top:198px; width:40px; height:40px;"
                    ></button>
                </div>
            `;
        """,
        script_names=("tutorial/yui-guide/overlay.js", "tutorial/yui-guide/director.js"),
    )

    result = mock_page.evaluate(
        """
        async () => {
            const director = window.createYuiGuideDirector({ page: 'home' });
            const events = [];
            const root = document.getElementById('react-chat-window-root');
            const avatarButton = document.getElementById('avatar-tool');
            avatarButton.addEventListener('click', () => events.push({ type: 'button:click' }));
            director.getAvatarFloatingNarrationDurationMs = () => 1000;
            director.waitForSceneDelay = async (durationMs) => {
                events.push({ type: 'delay', durationMs });
                return true;
            };
            director.setCompactToolFanOpen = (open, reason) => {
                events.push({ type: 'toolFan', open, reason });
                return true;
            };
            director.setChatAvatarToolMenuOpen = (open, reason) => {
                events.push({ type: 'avatarMenu', open, reason });
                const existing = document.getElementById('composer-tool-popover');
                if (existing) {
                    existing.remove();
                }
                if (open) {
                    const popover = document.createElement('div');
                    popover.id = 'composer-tool-popover';
                    ['lollipop', 'fist', 'hammer'].forEach((toolId) => {
                        const button = document.createElement('button');
                        button.className = 'composer-icon-button';
                        button.dataset.avatarToolId = toolId;
                        popover.appendChild(button);
                    });
                    root.appendChild(popover);
                }
                return true;
            };
            director.keepAvatarToolButtonHighlightedAfterMenuOpen = (target) => {
                events.push({ type: 'keepHighlight', id: target && target.id });
                return true;
            };

            const opened = await director.runAvatarFloatingSceneOperation({
                id: 'day3_avatar_tools_props',
                voiceKey: 'avatar_floating_day3_avatar_tools_props',
                text: '你可以随时来摸摸我的头，或者给我吃一根甜甜的棒棒糖。',
                operation: 'show-avatar-tools-then-hide-after-narration',
            }, avatarButton, Date.now() - 250);
            return {
                opened,
                events,
                toolCount: document.querySelectorAll('#composer-tool-popover .composer-icon-button[data-avatar-tool-id]').length,
            };
        }
        """
    )

    assert result["opened"] is True
    assert {"type": "toolFan", "open": True, "reason": "avatar-floating-guide-open-avatar-tool-fan"} not in result["events"]
    assert {"type": "button:click"} in result["events"]
    assert {
        "type": "avatarMenu",
        "open": True,
        "reason": "avatar-floating-guide-open-avatar-tool-menu",
    } in result["events"]
    assert {"type": "keepHighlight", "id": "avatar-tool"} in result["events"]
    narration_delays = [
        event["durationMs"]
        for event in result["events"]
        if event.get("type") == "delay"
    ]
    assert any(700 <= duration_ms <= 750 for duration_ms in narration_delays)
    assert {
        "type": "avatarMenu",
        "open": False,
        "reason": "avatar-floating-guide-close-avatar-tool-menu-after-narration",
    } in result["events"]
    assert result["events"].count({"type": "button:click"}) == 2
    assert result["events"][-1] == {
        "type": "avatarMenu",
        "open": False,
        "reason": "avatar-floating-guide-close-avatar-tool-menu-after-narration",
    }
    assert result["toolCount"] == 0


@pytest.mark.frontend
def test_react_chat_close_deactivates_active_tool_cursor(mock_page: Page):
    _bootstrap_page(
        mock_page,
        setup_js="""
            document.body.innerHTML = `
                <div id="react-chat-window-overlay" hidden>
                    <div id="react-chat-window-shell">
                        <div id="react-chat-window-drag-handle"></div>
                        <div id="react-chat-window-root"></div>
                    </div>
                </div>
            `;
            window.NekoChatWindow = {
                mount: (_root, props) => {
                    window.__lastReactChatProps = props;
                },
            };
        """,
        script_names=("app/app-react-chat-window.js",),
    )

    mock_page.evaluate(
        """
        async () => {
            const host = window.reactChatWindowHost;
            await host.ensureBundleLoaded();
            host.openWindow();
            window.__toolCursorResetKeys = [];
            window.__avatarToolStateEvents = [];
            host.setOnAvatarToolStateChange((detail) => {
                window.__avatarToolStateEvents.push(detail);
            });
        }
        """
    )
    mock_page.wait_for_function(
        "() => !!window.__lastReactChatProps",
        timeout=5000,
    )
    mock_page.evaluate(
        """
        () => {
            const host = window.reactChatWindowHost;
            host.deactivateToolCursor();
            window.__toolCursorResetKeys.push(window.__lastReactChatProps._toolCursorResetKey);
            host.closeWindow();
            window.__toolCursorResetKeys.push(window.__lastReactChatProps._toolCursorResetKey);
        }
        """
    )

    result = mock_page.evaluate(
        """
        () => ({
            resetKeys: window.__toolCursorResetKeys.slice(),
            avatarToolStateEvents: window.__avatarToolStateEvents.slice(),
        })
        """
    )

    assert len(result["resetKeys"]) == 2
    assert result["resetKeys"][0]
    assert result["resetKeys"][1]
    assert result["resetKeys"][1] != result["resetKeys"][0]
    assert result["avatarToolStateEvents"][-1]["active"] is False
    assert result["avatarToolStateEvents"][-1]["toolId"] is None


@pytest.mark.frontend
def test_tutorial_heartbeat_does_not_report_completed_while_tutorial_is_running(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
            window.__tutorialHeartbeatBodies = [];
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
        """,
        fetch_js="""
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
        """,
    )

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
def test_autostart_foreground_timer_starts_after_character_onboarding_settles(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
            window.__now = 1000;
            Date.now = function() { return window.__now; };
            window.__autostartHeartbeatBodies = [];
            window.__resolveCharacterOnboarding = null;
            window.CharacterPersonalityOnboarding = {
                whenSettled: function() {
                    if (!window.__characterOnboardingPromise) {
                        window.__characterOnboardingPromise = new Promise(function(resolve) {
                            window.__resolveCharacterOnboarding = resolve;
                        });
                    }
                    return window.__characterOnboardingPromise;
                },
            };
            window.nekoAutostartProvider = {
                getStatus: async function() {
                    window.__now = 1000 + (4 * 60 * 1000);
                    return {
                        ok: true,
                        supported: true,
                        enabled: false,
                        authoritative: true,
                        provider: 'neko-pc',
                    };
                },
                enable: async function() {
                    throw new Error('enable should not be called');
                },
            };
        """,
        fetch_js="""
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
        """,
    )

    mock_page.wait_for_function("() => window.__autostartHeartbeatBodies.length > 0")

    first_body = mock_page.evaluate("() => window.__autostartHeartbeatBodies[0]")

    assert first_body["foreground_ms_delta"] == 0

    mock_page.evaluate("() => window.__resolveCharacterOnboarding()")
    mock_page.wait_for_timeout(50)
    mock_page.evaluate(
        """
        () => {
            window.__now = 1000 + (4 * 60 * 1000) + 10000;
            window.dispatchEvent(new CustomEvent('neko:autostart-status-changed', {
                detail: {
                    supported: true,
                    enabled: false,
                    authoritative: true,
                    provider: 'neko-pc',
                },
            }));
        }
        """
    )
    mock_page.wait_for_function(
        "() => window.__autostartHeartbeatBodies.some((body) => body.foreground_ms_delta > 0)"
    )


@pytest.mark.frontend
def test_autostart_foreground_timer_starts_immediately_for_settled_character_onboarding(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
            window.__now = 1000;
            Date.now = function() { return window.__now; };
            window.__autostartHeartbeatBodies = [];
            window.CharacterPersonalityOnboarding = {
                whenSettled: function() {
                    return Promise.resolve();
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
                    throw new Error('enable should not be called');
                },
            };
        """,
        fetch_js="""
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
        """,
    )

    mock_page.wait_for_function("() => window.__autostartHeartbeatBodies.length > 0")
    mock_page.evaluate(
        """
        () => {
            window.__now = 1000 + 10000;
            window.dispatchEvent(new CustomEvent('neko:autostart-status-changed', {
                detail: {
                    supported: true,
                    enabled: false,
                    authoritative: true,
                    provider: 'neko-pc',
                },
            }));
        }
        """
    )
    mock_page.wait_for_timeout(1300)

    mock_page.wait_for_function(
        "() => window.__autostartHeartbeatBodies.some((body) => body.foreground_ms_delta > 0)"
    )


@pytest.mark.frontend
def test_autostart_prompt_display_continues_when_startup_gate_rejects(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
            window.__promptTitles = [];
            window.waitForStorageLocationStartupBarrier = function() {
                return Promise.reject(new Error('startup gate unavailable'));
            };
            window.showDecisionPrompt = async function(options) {
                window.__promptTitles.push(String(options && options.title || ''));
                if (options && typeof options.onShown === 'function') {
                    await options.onShown();
                }
                return null;
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
                    throw new Error('enable should not be called');
                },
            };
        """,
        fetch_js="""
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
        """,
    )

    mock_page.wait_for_function("() => window.__promptTitles.length === 1")

    assert mock_page.evaluate("() => window.__promptTitles[0]") == "要不要让 N.E.K.O. 开机自动启动？"


@pytest.mark.frontend
def test_started_manual_home_tutorial_does_not_suppress_reload_auto_start(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        setup_js="""
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
        """,
        fetch_js="""
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
        """,
    )

    mock_page.wait_for_function(
        "() => window.appTutorialPrompt && window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart",
        timeout=5000,
    )

    assert mock_page.evaluate(
        "() => window.appTutorialPrompt.shouldSuppressAutomaticHomeTutorialStart()"
    ) is False


@pytest.mark.frontend
def test_autostart_provider_enable_syncs_prompt_heartbeat_state(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_provider=True,
        setup_js="""
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
        """,
        fetch_js="""
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
        """,
    )

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
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_provider=True,
        setup_js="""
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
        """,
        fetch_js="""
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
        """,
    )

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
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_provider=True,
        setup_js="""
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
        """,
        fetch_js="""
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
        """,
    )

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
def test_autostart_provider_reports_unsupported_status_when_desktop_bridge_missing(
    mock_page: Page,
):
    _bootstrap_autostart_provider_page(
        mock_page,
        setup_js="""
            window.__requestLog = [];
        """,
        fetch_js="""
            window.__requestLog.push(requestUrl);
            throw new Error('backend autostart API should not be called when desktop bridge is missing');
        """,
    )

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
    _bootstrap_autostart_provider_page(
        mock_page,
        setup_js="""
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
        """,
        fetch_js="""
            window.__requestLog.push(requestUrl);
            throw new Error('backend fallback should not be called when desktop bridge exists');
        """,
    )

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
    _bootstrap_autostart_provider_page(
        mock_page,
        setup_js="""
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
        """,
    )
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
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_provider=True,
        setup_js="""
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
        """,
        fetch_js="""
            const csrfToken = headers['X-CSRF-Token'] || headers['x-csrf-token'] || '';

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
        """,
    )

    mock_page.wait_for_function(
        """
        () => window.__mutationTokens.length > 0
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

    assert result["pageConfigFetchCount"] >= 1
    assert "fresh-token" in result["mutationTokens"]
    assert result["tutorialHeartbeatBodies"] or result["autostartHeartbeatBodies"]


@pytest.mark.frontend
def test_fire_and_forget_json_uses_cached_csrf_token_without_awaiting_during_unload(
    mock_page: Page,
):
    _bootstrap_page(
        mock_page,
        setup_js="""
            window.__beacons = [];
            window.__fetchCalls = [];
            navigator.sendBeacon = function(url, data) {
                Promise.resolve(
                    typeof data === 'string'
                        ? data
                        : (data && typeof data.text === 'function' ? data.text() : '')
                ).then(function(body) {
                    window.__beacons.push({ url: String(url || ''), body: body });
                });
                return true;
            };
        """,
        fetch_js="""
            window.__fetchCalls.push({
                url: requestUrl,
                method: method,
                headers: headers,
                body: body,
            });
            return jsonResponse({ ok: true });
        """,
        script_names=("app-prompt-shared.js",),
    )

    mock_page.evaluate(
        """
        async () => {
            const helper = window.nekoLocalMutationSecurity;
            await helper.getMutationHeaders();
            helper.getMutationHeaders = function () {
                return new Promise(function () {});
            };
            const tools = window.nekoPromptShared.createPromptTools({
                loggerName: 'HarnessPrompt',
            });
            window.dispatchEvent(new Event('beforeunload'));
            void tools.fireAndForgetJson('/api/tutorial-prompt/heartbeat', {
                heartbeat_token: 'hb-token',
            });
        }
        """
    )

    mock_page.wait_for_function("() => window.__beacons.length === 1", timeout=5000)
    result = mock_page.evaluate(
        """
        () => ({
            beacon: window.__beacons[0],
            fetchCalls: window.__fetchCalls.slice(),
        })
        """
    )

    assert result["fetchCalls"] == []
    assert result["beacon"]["url"] == "/api/tutorial-prompt/heartbeat"
    assert '"_csrf_token":"test-token"' in result["beacon"]["body"]


@pytest.mark.frontend
def test_autostart_provider_disable_without_desktop_bridge_method_updates_cached_status_and_emits_event(
    mock_page: Page,
):
    _bootstrap_autostart_provider_page(
        mock_page,
        setup_js="""
            window.__statusEvents = [];
            window.nekoAutostart = {
                getStatus: async function() {
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
            };
            window.addEventListener('neko:autostart-status-changed', function(event) {
                window.__statusEvents.push(event.detail);
            });
        """,
        fetch_js="""
            throw new Error('backend fallback should not be called');
        """,
    )

    result = mock_page.evaluate(
        """
        async () => {
            const disabled = await window.nekoAutostartProvider.disable();
            return {
                disabled,
                cached: window.nekoAutostartProvider.getCachedStatus(),
                events: window.__statusEvents.slice(),
            };
        }
        """
    )

    assert result["disabled"]["ok"] is False
    assert result["disabled"]["supported"] is False
    assert result["disabled"]["enabled"] is False
    assert result["disabled"]["error_code"] == "autostart_not_supported"
    assert result["cached"]["error_code"] == "autostart_not_supported"
    assert result["events"] == [result["disabled"]]


@pytest.mark.frontend
def test_autostart_prompt_acceptance_tracks_pending_system_approval_without_failure(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        include_autostart_prompt=True,
        setup_js="""
            window.__toastMessages = [];
            window.showStatusToast = function(message) {
                window.__toastMessages.push(String(message));
            };
            window.__autostartDecisionBodies = [];
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
        """,
        fetch_js="""
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
        """,
    )

    expect(mock_page.locator(".modal-title")).to_have_text(
        "要不要让 N.E.K.O. 开机自动启动？",
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
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
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
        """,
        fetch_js="""
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
        """,
    )

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
def test_autostart_prompt_omits_never_button_and_keeps_later_action(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
            window.__promptButtons = [];
            window.__promptSkins = [];
            window.__autostartDecisionBodies = [];
            window.showDecisionPrompt = async function(config) {
                window.__promptSkins.push(config.skin);
                window.__promptButtons.push(
                    (config.buttons || []).map(function(button) {
                        return { value: button.value, text: button.text };
                    })
                );
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
        """,
        fetch_js="""
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
                        status: 'deferred',
                        never_remind: false,
                        deferred_until: Date.now() + 3 * 24 * 60 * 60 * 1000,
                        autostart_enabled: false,
                    },
                });
            }
        """,
    )

    mock_page.wait_for_function(
        "() => window.__autostartDecisionBodies.length === 1",
        timeout=5000,
    )

    result = mock_page.evaluate(
        """
        () => ({
            promptSkin: window.__promptSkins[0],
            promptButtons: window.__promptButtons[0],
            decisionBody: window.__autostartDecisionBodies[0],
        })
        """
    )

    assert result["promptButtons"] == [
        {"value": "later", "text": "以后提醒"},
        {"value": "accept", "text": "开启自启动"},
    ]
    assert result["promptSkin"] == "autostart-retention"
    assert result["decisionBody"]["decision"] == "later"


@pytest.mark.frontend
def test_autostart_prompt_plays_voice_on_show_and_stops_immediately_on_decision(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        include_autostart_prompt=True,
        setup_js="""
            window.__audioEvents = [];
            window.__requestLog = [];
            window.i18next = { language: 'ko-KR' };
            window.Audio = function(src) {
                this.src = String(src || '');
                this.currentTime = 0;
                window.__audioEvents.push({ event: 'create', src: this.src });
                this.play = function() {
                    window.__audioEvents.push({ event: 'play', src: this.src });
                    return Promise.resolve();
                };
                this.pause = function() {
                    window.__audioEvents.push({
                        event: 'pause',
                        src: this.src,
                        currentTime: this.currentTime,
                    });
                };
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
        """,
        fetch_js="""
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
                        deferred_until: Date.now() + 3 * 24 * 60 * 60 * 1000,
                        autostart_enabled: false,
                    },
                });
            }
        """,
    )

    expect(mock_page.locator(".modal-dialog-autostart-retention")).to_have_count(1, timeout=5000)
    mock_page.wait_for_function(
        "() => window.__audioEvents.some((entry) => entry.event === 'play')",
        timeout=5000,
    )

    events_before_click = mock_page.evaluate("() => window.__audioEvents.slice()")
    assert events_before_click[:2] == [
        {"event": "create", "src": "http://neko.test/static/autostart_prompt_voices/ko.mp3"},
        {"event": "play", "src": "http://neko.test/static/autostart_prompt_voices/ko.mp3"},
    ]

    mock_page.get_by_role("button", name="以后提醒").click()

    events_after_click = mock_page.evaluate("() => window.__audioEvents.slice()")
    assert events_after_click[-1] == {
        "event": "pause",
        "src": "http://neko.test/static/autostart_prompt_voices/ko.mp3",
        "currentTime": 0,
    }


@pytest.mark.frontend
def test_autostart_prompt_missing_voice_degrades_to_text_only(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_common_dialogs=True,
        include_autostart_prompt=True,
        setup_js="""
            window.__audioEvents = [];
            window.i18next = { language: 'ja' };
            window.Audio = function(src) {
                window.__audioEvents.push({ event: 'create', src: String(src || '') });
                this.play = function() {
                    window.__audioEvents.push({ event: 'play', src: String(src || '') });
                    return Promise.resolve();
                };
                this.pause = function() {
                    window.__audioEvents.push({ event: 'pause', src: String(src || '') });
                };
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
        """,
        fetch_js="""
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
                return jsonResponse({
                    ok: true,
                    state: {
                        status: 'deferred',
                        never_remind: false,
                        deferred_until: Date.now() + 3 * 24 * 60 * 60 * 1000,
                        autostart_enabled: false,
                    },
                });
            }
        """,
    )

    expect(mock_page.locator(".modal-dialog-autostart-retention")).to_have_count(1, timeout=5000)
    mock_page.get_by_role("button", name="以后提醒").click()
    expect(mock_page.locator(".modal-overlay")).to_have_count(0, timeout=5000)
    assert mock_page.evaluate("() => window.__audioEvents.slice()") == []


@pytest.mark.frontend
def test_autostart_decision_failure_retries_without_reopening_prompt(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
            window.__promptTitles = [];
            window.__autostartDecisionBodies = [];
            window.__autostartHeartbeatBodies = [];
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
        """,
        fetch_js="""
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
        """,
    )

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

    assert result["promptTitles"] == ["要不要让 N.E.K.O. 开机自动启动？"]
    assert len(result["decisionBodies"]) == 2
    assert result["decisionBodies"][0]["decision"] == "later"
    assert result["decisionBodies"][1]["decision"] == "later"
    assert len(result["heartbeatBodies"]) >= 2


@pytest.mark.frontend
def test_autostart_prompt_does_not_retry_later_decision_after_permanent_client_error(
    mock_page: Page,
):
    _bootstrap_tutorial_prompt_page(
        mock_page,
        include_autostart_prompt=True,
        setup_js="""
            window.__autostartDecisionBodies = [];
            window.__autostartHeartbeatBodies = [];
            window.__promptTitles = [];
            window.showDecisionPrompt = async function(config) {
                window.__promptTitles.push(config.title);
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
        """,
        fetch_js="""
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
                return jsonResponse({
                    ok: false,
                    error: 'invalid decision payload',
                }, 400);
            }
        """,
    )

    mock_page.wait_for_function(
        "() => window.__autostartDecisionBodies.length === 1",
        timeout=5000,
    )
    mock_page.wait_for_timeout(2000)

    result = mock_page.evaluate(
        """
        () => ({
            promptTitles: window.__promptTitles.slice(),
            decisionBodies: window.__autostartDecisionBodies.slice(),
            heartbeatBodies: window.__autostartHeartbeatBodies.slice(),
        })
        """
    )

    assert result["promptTitles"] == ["要不要让 N.E.K.O. 开机自动启动？"]
    assert len(result["decisionBodies"]) == 1
    assert result["decisionBodies"][0]["decision"] == "later"
    assert len(result["heartbeatBodies"]) == 1
