import json

import pytest
import re
from pathlib import Path
from playwright.sync_api import BrowserContext, Page, expect

from utils.file_utils import atomic_write_json
from utils.storage_policy import save_storage_policy


def _request_json(route):
    post_data_json = route.request.post_data_json
    return post_data_json() if callable(post_data_json) else post_data_json


def _assert_tutorial_reset_notice(page: Page, message: str) -> None:
    notice = page.locator(".tutorial-reset-notice-backdrop")
    expect(notice).to_be_visible()
    expect(page.locator(".tutorial-reset-notice-message")).to_have_text(message)
    page.locator(".tutorial-reset-notice-ok").click()
    expect(notice).to_have_count(0)


def _open_auxiliary_panel(page: Page, panel_name: str) -> None:
    trigger = page.locator(f"#memory-{panel_name}-trigger")
    panel = page.locator(f"#memory-{panel_name}-panel")
    expect(trigger).to_be_enabled()
    trigger.click()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(panel).to_be_visible()


def _run_and_observe_row_exit(
    page: Page,
    trigger_selector: str,
    expected_remaining: int,
) -> dict:
    """Trigger a routine removal and observe its complete, bounded lifecycle."""
    return page.evaluate(
        """
        async ({ triggerSelector, expectedRemaining }) => {
            const edit = document.getElementById('memory-chat-edit');
            const trigger = document.querySelector(triggerSelector);
            const startedAt = performance.now();
            const nativeSetTimeout = window.setTimeout;
            const nativeAnimate = Element.prototype.animate;
            const scheduledDelays = [];
            const animationOptions = [];
            let sawLeaving = false;
            let sawReflowing = false;
            let sawParticleCanvas = false;
            let sawClearDisabled = false;
            let sawClearOpacityChange = false;
            let sawDeleteDisabled = false;
            let completionTransitions = 0;
            let previousCount = edit.querySelectorAll('.chat-item').length;
            const initialEditClientWidth = edit.clientWidth;
            const exitingRow = trigger.closest('.chat-item');
            const followingRole = exitingRow && exitingRow.nextElementSibling
                ? exitingRow.nextElementSibling.dataset.role
                : '';
            const followingRowTops = [];
            const batchRows = exitingRow ? [] : Array.from(
                edit.querySelectorAll('.chat-item[data-role="human"], .chat-item[data-role="ai"]')
            );
            const batchHeightSamples = [];
            const clearButton = document.getElementById('clear-memory-btn');
            const initialClearOpacity = getComputedStyle(clearButton).opacity;

            const sample = () => {
                sawLeaving = sawLeaving || !!edit.querySelector('.chat-item.is-leaving');
                sawReflowing = sawReflowing || !!edit.querySelector('.chat-item.is-reflowing');
                sawParticleCanvas = sawParticleCanvas || !!document.getElementById('memory-particle-canvas');
                sawClearDisabled = sawClearDisabled || clearButton.disabled;
                sawClearOpacityChange = sawClearOpacityChange
                    || getComputedStyle(clearButton).opacity !== initialClearOpacity;
                sawDeleteDisabled = sawDeleteDisabled || Array.from(
                    edit.querySelectorAll('.delete-btn')
                ).some(button => button.disabled);
                const count = edit.querySelectorAll('.chat-item').length;
                if (count === expectedRemaining && previousCount !== expectedRemaining) {
                    completionTransitions += 1;
                }
                previousCount = count;
                return count;
            };
            const observer = new MutationObserver(sample);
            observer.observe(edit, { attributes: true, childList: true, subtree: true });
            window.setTimeout = function (callback, delay, ...args) {
                scheduledDelays.push(Number(delay));
                return nativeSetTimeout.call(window, callback, delay, ...args);
            };
            Element.prototype.animate = function (keyframes, options) {
                animationOptions.push({
                    duration: Number(options && options.duration) || 0,
                    delay: Number(options && options.delay) || 0,
                    keyframes,
                });
                return nativeAnimate.call(this, keyframes, options);
            };
            try {
                trigger.click();
            } catch (error) {
                window.setTimeout = nativeSetTimeout;
                Element.prototype.animate = nativeAnimate;
                observer.disconnect();
                throw error;
            }

            return await new Promise(resolve => {
                const poll = () => {
                    const count = sample();
                    const elapsed = performance.now() - startedAt;
                    if (followingRole) {
                        const followingRow = edit.querySelector(
                            `.chat-item[data-role="${CSS.escape(followingRole)}"]`
                        );
                        if (followingRow) {
                            followingRowTops.push(followingRow.getBoundingClientRect().top);
                        }
                    }
                    if (batchRows.length && batchRows.every(row => row.isConnected)) {
                        batchHeightSamples.push(batchRows.reduce(
                            (height, row) => height + row.getBoundingClientRect().height,
                            0,
                        ));
                    }
                    const reflowFinished = count === expectedRemaining
                        && !edit.querySelector('.chat-item.is-reflowing');
                    if (reflowFinished || elapsed > 750) {
                        window.setTimeout = nativeSetTimeout;
                        Element.prototype.animate = nativeAnimate;
                        observer.disconnect();
                        resolve({
                            elapsed,
                            count,
                            initialEditClientWidth,
                            finalEditClientWidth: edit.clientWidth,
                            sawLeaving,
                            sawReflowing,
                            sawParticleCanvas,
                            sawClearDisabled,
                            sawClearOpacityChange,
                            sawDeleteDisabled,
                            completionTransitions,
                            scheduledDelays,
                            animationOptions,
                            followingRowTops,
                            batchHeightSamples,
                        });
                        return;
                    }
                    requestAnimationFrame(poll);
                };
                requestAnimationFrame(poll);
            });
        }
        """,
        {
            "triggerSelector": trigger_selector,
            "expectedRemaining": expected_remaining,
        },
    )


@pytest.fixture
def seed_memory_file(clean_user_data_dir, running_server):
    """Create a seed memory file in the test memory directory."""
    app_root = Path(clean_user_data_dir) / "N.E.K.O"
    save_storage_policy(
        None,
        selected_root=app_root,
        anchor_root=app_root,
        selection_source="test",
    )

    memory_dir = app_root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    catgirl_dir = memory_dir / "测试猫娘"
    catgirl_dir.mkdir(parents=True, exist_ok=True)

    # Create a minimal recent memory file for a test catgirl
    test_data = [
        {
            "type": "system",
            "data": {
                "content": "先前对话的备忘录: 这是测试备忘录内容。",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "system",
                "name": None,
                "id": None,
                "example": False
            }
        },
        {
            "type": "human",
            "data": {
                "content": "你好，测试猫娘！",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "human",
                "name": None,
                "id": None,
                "example": False
            }
        },
        {
            "type": "ai",
            "data": {
                "content": "[2026-01-01 12:00:00] 你好主人！我是测试猫娘喵~",
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "ai",
                "name": None,
                "id": None,
                "example": False,
                "tool_calls": [],
                "invalid_tool_calls": [],
                "usage_metadata": None
            }
        }
    ]

    memory_file = catgirl_dir / "recent.json"
    atomic_write_json(memory_file, test_data, ensure_ascii=False, indent=2)

    return memory_file


def _install_ready_memory_browser_routes(
    page: Page | BrowserContext,
    memory_file: Path,
    *,
    recent_files: list[str] | None = None,
    current_catgirl: str | None = None,
    tutorial_seen: bool = True,
) -> None:
    """Mock storage + memory APIs so the page (or whole context) is tested in ready mode."""
    if tutorial_seen:
        page.add_init_script(
            "window.localStorage.setItem('neko_tutorial_memory_browser', 'true')"
        )
    app_root = memory_file.parents[2]
    review_state = {"enabled": True}
    strong_memory_state = {"enabled": False}

    def handle_bootstrap(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "current_root": str(app_root),
                "recommended_root": str(app_root),
                "legacy_sources": [],
                "selection_required": False,
                "migration_pending": False,
                "recovery_required": False,
                "blocking_reason": "",
            },
        )

    def handle_recent_files(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"files": recent_files or ["recent_测试猫娘.json"]},
        )

    def handle_current_catgirl(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"current_catgirl": current_catgirl or "测试猫娘"},
        )

    def handle_recent_file(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"content": memory_file.read_text(encoding="utf-8")},
        )

    def handle_review_config(route):
        if route.request.method == "POST":
            payload = _request_json(route)
            review_state["enabled"] = bool(payload.get("enabled"))
            route.fulfill(status=200, content_type="application/json", json={"success": True, "enabled": review_state["enabled"]})
            return
        route.fulfill(status=200, content_type="application/json", json={"enabled": review_state["enabled"]})

    def handle_powerful_memory_config(route):
        if route.request.method == "POST":
            payload = _request_json(route)
            strong_memory_state["enabled"] = bool(payload.get("enabled"))
            route.fulfill(
                status=200,
                content_type="application/json",
                json={"success": True, "enabled": strong_memory_state["enabled"]},
            )
            return
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"enabled": strong_memory_state["enabled"]},
        )

    def handle_save(route):
        route.fulfill(status=200, content_type="application/json", json={"success": True, "need_refresh": False})

    page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    page.route("**/api/memory/recent_files", handle_recent_files)
    page.route("**/api/characters/current_catgirl", handle_current_catgirl)
    page.route("**/api/memory/recent_file?**", handle_recent_file)
    page.route("**/api/memory/review_config", handle_review_config)
    page.route("**/api/memory/powerful_memory_config", handle_powerful_memory_config)
    page.route("**/api/memory/recent_file/save", handle_save)


@pytest.mark.frontend
def test_memory_browser_page_load(mock_page: Page, running_server: str, seed_memory_file):
    """Test that the memory browser page loads and displays the file list."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    # Navigate to the memory browser page
    mock_page.goto(f"{running_server}/memory_browser")

    # Wait for the file list to populate (the JS fetches /api/memory/recent_files on load)
    # We should see a button with the catgirl name in the list
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    # The list should show our seeded catgirl
    expect(mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘")).to_have_count(1, timeout=5000)

    # Stage 1 storage-location entry is read-only and must not auto-start migration.
    _open_auxiliary_panel(mock_page, "settings")
    expect(mock_page.locator(".storage-location-section")).to_be_visible()
    expect(mock_page.locator("[data-i18n='memory.applicationDataLocation']")).to_have_count(0)
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_enabled()
    expect(mock_page.locator("#storage-recommended-root")).to_have_count(0)
    expect(mock_page.locator("#storage-current-root")).not_to_have_text("加载中...", timeout=5000)
    expect(mock_page.locator("#storage-location-overlay")).to_have_count(0)

    locale_dir = Path(__file__).parents[2] / "static" / "locales"
    for locale_file in sorted(locale_dir.glob("*.json")):
        memory_locale = json.loads(locale_file.read_text(encoding="utf-8"))["memory"]
        assert "applicationDataLocation" not in memory_locale
    expect(mock_page.locator("#tutorial-reset-select option[value='current_personality']")).to_have_count(1)
    expect(mock_page.locator("#home-tutorial-reset-controls")).to_have_count(0)
    expect(mock_page.locator(".tutorial-day-reset-menu")).to_have_count(0)
    mock_page.keyboard.press("Escape")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    expect(tutorial.locator(".tutorial-cascader-day-column [data-tutorial-day]")).to_have_count(7)
    expect(tutorial.locator(":scope > .tutorial-cascader-popup")).to_be_hidden()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_disabled()
    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    expect(tutorial.locator(":scope > .tutorial-cascader-popup")).to_be_visible()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    expect(tutorial.locator(".tutorial-cascader-day-column")).to_be_visible()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_disabled()
    expect(tutorial.locator(".tutorial-cascader-option[data-tutorial-home-all='true']")).to_have_count(1)
    tutorial.locator(".tutorial-cascader-option[data-tutorial-day='1']").click()
    expect(tutorial.locator(".tutorial-reset-value")).to_have_text("主页 / 第 1 天")
    expect(tutorial.locator(":scope > .tutorial-cascader-popup")).to_be_hidden()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_enabled()
    assert mock_page.evaluate("typeof window.AvatarFloatingGuideReset") == "object"
    assert mock_page.evaluate("typeof window.appStorageLocation") == "object"
    assert mock_page.evaluate("typeof window.waitForStorageLocationStartupBarrier") == "undefined"
    assert mock_page.evaluate("typeof window.__nekoStorageLocationStartupBarrier") == "undefined"


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("width", "role_panel_initially_open"),
    [(840, True), (720, False)],
)
def test_memory_browser_tutorial_frames_current_surfaces_and_restores_panels(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
    width: int,
    role_panel_initially_open: bool,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        tutorial_seen=False,
    )
    mock_page.add_init_script(
        """
        window.localStorage.removeItem('neko_tutorial_memory_browser');
        window.localStorage.removeItem('neko_tutorial_memory_browser_manual_intent');
        window.localStorage.removeItem('neko_yui_guide_handoff_token');
        """
    )
    mock_page.set_viewport_size({"width": width, "height": 640})
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )

    role_panel = mock_page.locator("#memory-role-panel")
    settings_panel = mock_page.locator("#memory-settings-panel")
    expect(settings_panel).to_be_hidden()

    highlight = mock_page.locator(".driver-highlight")
    title = mock_page.locator(".driver-popover-title")
    expect(highlight).to_be_visible(timeout=10000)

    def assert_highlight_matches(selector: str) -> None:
        mock_page.wait_for_function(
            """
            selector => {
                const target = document.querySelector(selector);
                const frame = document.querySelector('.driver-highlight');
                const popover = document.querySelector('.driver-popover');
                if (!target || !frame || !popover) return false;
                const targetRect = target.getBoundingClientRect();
                const frameRect = frame.getBoundingClientRect();
                const popoverRect = popover.getBoundingClientRect();
                const frameStyle = getComputedStyle(frame);
                const horizontalBorder = parseFloat(frameStyle.borderLeftWidth)
                    + parseFloat(frameStyle.borderRightWidth);
                const verticalBorder = parseFloat(frameStyle.borderTopWidth)
                    + parseFloat(frameStyle.borderBottomWidth);
                const padding = 8;
                return Math.abs(frameRect.left - (targetRect.left - padding)) <= 3
                    && Math.abs(frameRect.top - (targetRect.top - padding)) <= 3
                    && Math.abs(frameRect.width - (targetRect.width + padding * 2 + horizontalBorder)) <= 4
                    && Math.abs(frameRect.height - (targetRect.height + padding * 2 + verticalBorder)) <= 4
                    && popoverRect.left >= 8
                    && popoverRect.top >= 8
                    && popoverRect.right <= window.innerWidth - 8
                    && popoverRect.bottom <= window.innerHeight - 8;
            }
            """,
            arg=selector,
            timeout=5000,
        )

    expect(title).to_have_text("🐱 角色记忆库")
    expect(role_panel).to_be_visible()
    role_library_target = (
        "#memory-role-library" if width >= 840 else "#memory-role-panel"
    )
    assert_highlight_matches(role_library_target)

    mock_page.locator(".driver-next").click()
    expect(title).to_have_text("📝 浏览与校对记忆")
    expect(role_panel).to_be_visible()
    assert_highlight_matches(".editor")

    mock_page.locator(".driver-next").click()
    expect(title).to_have_text("🧰 功能区")
    expect(role_panel).to_be_visible()
    assert_highlight_matches(".memory-global-actions")

    mock_page.locator(".driver-finish").click()
    expect(highlight).to_be_hidden()
    expect(settings_panel).to_be_hidden()
    if role_panel_initially_open:
        expect(role_panel).to_be_visible()
    else:
        expect(role_panel).to_be_hidden()


@pytest.mark.frontend
def test_memory_browser_export_logs_desktop_flow_is_deduplicated_and_localized(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.__exportLogCalls = [];
        window.__exportLogResolvers = [];
        window.nekoHost = {
            exportLogs: (...args) => {
                window.__exportLogCalls.push(args);
                return new Promise(resolve => window.__exportLogResolvers.push(resolve));
            },
        };
        """
    )
    mock_page.goto(f"{running_server}/memory_browser")
    trigger = mock_page.locator("#memory-export-logs-trigger")
    status = mock_page.locator("#memory-export-logs-status")
    expect(trigger).to_be_visible()
    expect(trigger).to_be_enabled()
    expect(status).to_have_attribute("role", "status")
    expect(status).to_have_attribute("aria-live", "polite")
    # The Steam language probe completes asynchronously. Wait for the localized
    # control before asserting the localized status values below.
    expect(trigger).to_have_text("导出日志")
    mock_page.wait_for_function("typeof window.t === 'function'", timeout=10000)
    assert mock_page.evaluate("window.t && window.t('memory.exportLogsPending')") == "正在准备日志…"

    trigger.click()
    trigger.evaluate("button => button.click()")
    expect(trigger).to_be_disabled()
    expect(status).to_have_text("正在准备日志…")
    expect(trigger).to_have_text("导出中…")
    expect(trigger).to_have_attribute("data-export-state", "pending")
    assert mock_page.evaluate("window.__exportLogCalls") == [[]]

    mock_page.evaluate("window.__exportLogResolvers.shift()({ ok: true, cancelled: true })")
    expect(trigger).to_be_enabled()
    expect(status).to_be_empty()
    expect(trigger).to_have_text("导出日志")

    outcomes = [
        ({"ok": False, "code": "EXPORT_PACKAGE_FAILED"}, "日志打包失败，请重试", "error", "导出失败"),
        ({"ok": False, "code": "EXPORT_WRITE_FAILED"}, "无法保存到所选位置，请更换位置后重试", "error", "导出失败"),
        ({"ok": True, "cancelled": False, "empty": True}, "未找到日志，已导出诊断说明", "success", "已导出"),
        ({"ok": True, "cancelled": False, "empty": False}, "日志已导出", "success", "已导出"),
    ]
    for result, expected_text, expected_state, expected_label in outcomes:
        trigger.click()
        expect(trigger).to_be_disabled()
        mock_page.evaluate(
            "result => window.__exportLogResolvers.shift()(result)",
            result,
        )
        expect(trigger).to_be_enabled()
        expect(status).to_have_text(expected_text)
        expect(trigger).to_have_attribute("data-export-state", expected_state)
        expect(trigger).to_have_text(expected_label)
        expect(trigger).to_have_attribute("title", expected_text)

    assert mock_page.evaluate("window.__exportLogCalls.every(args => args.length === 0)")
    expect(status).to_be_empty(timeout=4000)
    expect(trigger).to_have_attribute("data-export-state", "")
    expect(trigger).to_have_text("导出日志")


@pytest.mark.frontend
def test_memory_browser_export_logs_without_host_stays_visible_and_has_no_http_fallback(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    requested_urls: list[str] = []
    mock_page.on("request", lambda request: requested_urls.append(request.url))
    mock_page.goto(f"{running_server}/memory_browser")

    trigger = mock_page.locator("#memory-export-logs-trigger")
    expect(trigger).to_be_visible()
    expect(trigger).to_be_disabled()
    expect(trigger).to_have_attribute("title", "仅桌面版可导出本机日志")
    expect(trigger).to_have_attribute("aria-label", "导出日志（仅桌面版可用）")
    trigger.evaluate("button => button.click()")

    assert not any("export" in url.lower() and "log" in url.lower() for url in requested_urls)
    assert mock_page.evaluate("document.querySelector('a[download]') === null")


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("width", "height"),
    [(1280, 720), (840, 600), (839, 600), (720, 560)],
)
def test_memory_browser_export_logs_header_fits_and_keyboard_focus_is_visible(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
    width: int,
    height: int,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        "window.nekoHost = { exportLogs: async () => ({ ok: true, cancelled: false, empty: false }) }"
    )
    mock_page.set_viewport_size({"width": width, "height": height})
    mock_page.goto(f"{running_server}/memory_browser")

    utility_bar = mock_page.locator(".memory-utility-bar")
    expect(utility_bar).to_have_count(1)
    expect(mock_page.locator(".container-header .memory-global-actions")).to_have_count(0)
    expect(utility_bar.locator(".memory-global-actions")).to_have_count(1)
    expect(utility_bar.locator("#memory-export-logs-status")).to_have_count(1)
    expect(mock_page.locator(".tips-container")).to_be_in_viewport()

    expect(mock_page.locator("#memory-compact-current-role-name")).to_have_text("测试猫娘")

    result = mock_page.evaluate(
        """
        () => {
            const bar = document.querySelector('.memory-utility-bar');
            const actions = document.querySelector('.memory-global-actions');
            const notice = document.querySelector('.tips-container');
            const tipIcon = notice.querySelector('img');
            const tipText = notice.querySelector('.tip-text');
            const barRect = bar.getBoundingClientRect();
            const actionRect = actions.getBoundingClientRect();
            const noticeRect = notice.getBoundingClientRect();
            const tipIconRect = tipIcon.getBoundingClientRect();
            const tipTextRect = tipText.getBoundingClientRect();
            const tipTextStyle = getComputedStyle(tipText);
            const barStyle = getComputedStyle(bar);
            const actionSizes = Array.from(actions.querySelectorAll('.memory-header-action')).map(button => {
                const rect = button.getBoundingClientRect();
                return [Math.round(rect.width), Math.round(rect.height)];
            });
            return {
                barHeight: Math.round(barRect.height),
                barHasCardShape: barStyle.borderTopWidth === '1px'
                    && barStyle.borderRightWidth === '1px'
                    && barStyle.borderBottomWidth === '1px'
                    && barStyle.borderLeftWidth === '1px'
                    && barStyle.borderRadius === '12px'
                    && barStyle.boxShadow !== 'none',
                actionsSingleRow: Math.round(actionRect.height) <= 36,
                actionsEqualSize: actionSizes.length === 3
                    && actionSizes.every(size => size[0] === 100 && size[1] === 36),
                actionsInsideBar: actionRect.top >= barRect.top && actionRect.bottom <= barRect.bottom,
                noticeBelowBar: noticeRect.top >= barRect.bottom,
                noticeContentsInside: tipIconRect.top >= noticeRect.top
                    && tipIconRect.bottom <= noticeRect.bottom
                    && tipTextRect.top >= noticeRect.top
                    && tipTextRect.bottom <= noticeRect.bottom,
                noticeSingleLine: tipTextStyle.whiteSpace === 'nowrap'
                    && tipTextRect.height <= parseFloat(tipTextStyle.lineHeight) + 0.5,
                noActionOverflow: actions.scrollWidth <= actions.clientWidth,
                noPageOverflow: document.documentElement.scrollWidth <= document.documentElement.clientWidth,
                pageMinWidth: getComputedStyle(document.documentElement).minWidth,
            };
        }
        """
    )
    assert result == {
        "barHeight": 52,
        "barHasCardShape": True,
        "actionsSingleRow": True,
        "actionsEqualSize": True,
        "actionsInsideBar": True,
        "noticeBelowBar": True,
        "noticeContentsInside": True,
        "noticeSingleLine": True,
        "noActionOverflow": True,
        "noPageOverflow": True,
        "pageMinWidth": "720px",
    }

    role_trigger = mock_page.locator("#memory-role-panel-trigger")
    expect(role_trigger).to_be_visible()
    expect(role_trigger).to_have_text("记忆库 · 测试猫娘")
    expect(role_trigger).to_have_attribute("aria-label", "记忆库 · 测试猫娘")
    expect(role_trigger).not_to_have_attribute("title", re.compile(r".+"))
    role_icon = role_trigger.locator(".memory-role-panel-trigger-icon")
    expect(role_icon).to_be_visible()
    expect(role_icon).to_have_attribute("viewBox", "0 0 20 20")
    expect(role_icon).to_have_css("width", "20px")
    expect(role_icon).to_have_css("color", "rgb(64, 197, 241)")
    expect(role_icon.locator(".memory-role-panel-trigger-frame")).to_have_count(1)
    header_action = mock_page.locator(".memory-header-action").first
    import_action = mock_page.locator("#memory-import-trigger")
    expect(header_action).to_have_css("color", "rgb(20, 126, 166)")
    expect(import_action).to_have_css("color", "rgb(20, 126, 166)")
    mock_page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    expect(role_icon).to_have_css("color", "rgb(56, 189, 248)")
    expect(header_action).to_have_css("color", "rgb(125, 211, 252)")
    expect(import_action).to_have_css("color", "rgb(125, 211, 252)")
    mock_page.evaluate("document.documentElement.removeAttribute('data-theme')")
    expect(role_trigger).to_have_css("width", "36px")
    expect(role_trigger).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    expect(role_trigger).to_have_css("border-color", "rgba(0, 0, 0, 0)")
    expect(role_trigger).to_have_css("box-shadow", "none")
    role_trigger.hover()
    expect(role_trigger).not_to_have_css("background-color", "rgba(0, 0, 0, 0)")
    role_tooltip = role_trigger.locator(".memory-role-panel-trigger-label")
    expect(role_tooltip).to_be_visible()
    expect(role_tooltip).to_have_css("opacity", "1")
    expect(role_tooltip).to_have_css("background-color", "rgb(255, 255, 255)")
    expect(role_tooltip).to_have_css("color", "rgb(92, 111, 126)")
    tooltip_position = role_tooltip.evaluate(
        """
        tooltip => {
            const tooltipRect = tooltip.getBoundingClientRect();
            const triggerRect = tooltip.parentElement.getBoundingClientRect();
            return {
                belowTrigger: tooltipRect.top >= triggerRect.bottom,
                afterTrigger: tooltipRect.left >= triggerRect.right,
                alignedLeft: Math.abs(tooltipRect.left - triggerRect.left) <= 1,
                alignedMiddle: Math.abs(
                    (tooltipRect.top + tooltipRect.height / 2)
                    - (triggerRect.top + triggerRect.height / 2)
                ) <= 1,
            };
        }
        """
    )
    assert tooltip_position == {
        "belowTrigger": False,
        "afterTrigger": True,
        "alignedLeft": False,
        "alignedMiddle": True,
    }
    if width >= 840:
        expect(role_trigger).to_have_attribute("aria-expanded", "true")
        expect(role_trigger.locator(".memory-role-panel-trigger-divider")).to_have_css("transform", "none")
        expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    else:
        expect(role_trigger).to_have_attribute("aria-expanded", "false")
        expect(role_trigger.locator(".memory-role-panel-trigger-divider")).not_to_have_css("transform", "none")
        if width <= 767:
            expect(mock_page.locator(".memory-header-action").first).to_have_css(
                "white-space",
                "nowrap",
            )

        locale_dir = Path(__file__).parents[2] / "static" / "locales"
        locale_files = sorted(locale_dir.glob("*.json"))
        compact_labels = {
            locale_file.name: json.loads(locale_file.read_text(encoding="utf-8"))["memory"]["compactLibraryLabel"]
            for locale_file in locale_files
        }
        assert set(compact_labels) == {
            "en.json",
            "es.json",
            "ja.json",
            "ko.json",
            "pt.json",
            "ru.json",
            "zh-CN.json",
            "zh-TW.json",
        }
        assert all(label.strip() for label in compact_labels.values())

    mock_page.locator("body").click(position={"x": 1, "y": 1})
    mock_page.locator("#memory-guide-trigger").focus()
    mock_page.keyboard.press("Tab")
    trigger = mock_page.locator("#memory-export-logs-trigger")
    expect(trigger).to_be_focused()
    focus = trigger.evaluate(
        """
        button => {
            const style = getComputedStyle(button);
            return {
                matches: button.matches(':focus-visible'),
                width: style.outlineWidth,
                boxShadow: style.boxShadow,
            };
        }
        """
    )
    assert focus["matches"] is True
    assert focus["width"] == "2px"
    assert focus["boxShadow"] == "none"


@pytest.mark.frontend
def test_memory_browser_header_controls_do_not_jump_at_layout_boundary(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 839, "height": 720})
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-compact-current-role-name")).not_to_be_empty(
        timeout=10000
    )

    def header_alignment() -> dict:
        return mock_page.evaluate(
            """
            () => {
                const bar = document.querySelector('.memory-utility-bar').getBoundingClientRect();
                const actions = document.querySelector('.memory-global-actions').getBoundingClientRect();
                const trigger = document.getElementById('memory-role-panel-trigger').getBoundingClientRect();
                return {
                    barLeft: bar.left,
                    barRight: window.innerWidth - bar.right,
                    actionsRight: bar.right - actions.right,
                    actionsTop: actions.top - bar.top,
                    triggerLeft: trigger.left - bar.left,
                    triggerTop: trigger.top - bar.top,
                };
            }
            """
        )

    compact_alignment = header_alignment()
    mock_page.set_viewport_size({"width": 840, "height": 720})
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-layout",
        "wide",
    )
    wide_alignment = header_alignment()

    for key in compact_alignment:
        assert abs(compact_alignment[key] - wide_alignment[key]) <= 0.1


@pytest.mark.frontend
def test_memory_browser_header_actions_clip_long_localized_labels(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 720, "height": 560})
    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_function("window.i18next && window.i18next.isInitialized")

    mock_page.evaluate("window.i18next.changeLanguage('es')")
    settings = mock_page.locator("#memory-settings-trigger")
    expect(settings).to_have_attribute("title", "Configuración de memoria")
    expect(settings.locator("span")).to_have_text("Configuración de memoria")
    role_trigger = mock_page.locator("#memory-role-panel-trigger")
    expect(role_trigger).to_have_attribute("aria-label", "Memoria · 测试猫娘")
    expect(role_trigger).not_to_have_attribute("title", re.compile(r".+"))

    geometry = settings.evaluate(
        """
        button => {
            const label = button.querySelector('span');
            const buttonRect = button.getBoundingClientRect();
            const labelRect = label.getBoundingClientRect();
            const labelStyle = getComputedStyle(label);
            return {
                buttonWidth: Math.round(buttonRect.width),
                labelInsideButton:
                    labelRect.left >= buttonRect.left
                    && labelRect.right <= buttonRect.right,
                overflow: labelStyle.overflow,
                textOverflow: labelStyle.textOverflow,
                whiteSpace: labelStyle.whiteSpace,
            };
        }
        """
    )
    assert geometry == {
        "buttonWidth": 100,
        "labelInsideButton": True,
        "overflow": "hidden",
        "textOverflow": "ellipsis",
        "whiteSpace": "nowrap",
    }


@pytest.mark.frontend
def test_memory_browser_horizontal_insets_do_not_jump_at_density_boundary(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 767, "height": 720})
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-compact-current-role-name")).not_to_be_empty(
        timeout=10000
    )

    def horizontal_insets() -> dict:
        return mock_page.evaluate(
            """
            () => {
                const bar = document.querySelector('.memory-utility-bar').getBoundingClientRect();
                const actions = document.querySelector('.memory-global-actions').getBoundingClientRect();
                const action = document.querySelector('.memory-header-action');
                const trigger = document.getElementById('memory-role-panel-trigger').getBoundingClientRect();
                const notice = document.querySelector('.tips-container').getBoundingClientRect();
                const tipIcon = document.querySelector('.tips-container img').getBoundingClientRect();
                const tipStyle = getComputedStyle(document.querySelector('.tip-text'));
                const actionRect = action.getBoundingClientRect();
                const actionStyle = getComputedStyle(action);
                return {
                    barLeft: bar.left,
                    barRight: window.innerWidth - bar.right,
                    actionsRight: bar.right - actions.right,
                    triggerLeft: trigger.left - bar.left,
                    noticeHeight: notice.height,
                    tipIconWidth: tipIcon.width,
                    tipFontSize: parseFloat(tipStyle.fontSize),
                    actionWidth: actionRect.width,
                    actionHeight: actionRect.height,
                    actionFontSize: parseFloat(actionStyle.fontSize),
                };
            }
            """
        )

    narrow_alignment = horizontal_insets()
    mock_page.set_viewport_size({"width": 768, "height": 720})
    wider_alignment = horizontal_insets()
    for key in narrow_alignment:
        assert abs(narrow_alignment[key] - wider_alignment[key]) <= 0.1


@pytest.mark.frontend
def test_memory_browser_auxiliary_panels_keep_mounted_controls_operable_and_restore_focus(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list button.cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )

    _open_auxiliary_panel(mock_page, "settings")
    settings_trigger = mock_page.locator("#memory-settings-trigger")
    expect(mock_page.locator("#memory-settings-scope")).to_be_visible()
    expect(mock_page.locator("#review-toggle-checkbox")).to_be_visible()
    expect(mock_page.locator("#strong-memory-toggle-checkbox")).to_be_visible()
    storage_manage = mock_page.locator("#storage-location-manage-btn")
    expect(storage_manage).to_be_visible()
    expect(storage_manage).to_have_css("background-color", "rgb(64, 197, 241)")
    expect(storage_manage).to_have_css("background-image", "none")
    expect(storage_manage).to_have_css("color", "rgb(255, 255, 255)")
    expect(storage_manage).to_have_css("box-shadow", "none")
    expect(mock_page.locator("#storage-location-open-btn")).to_have_css(
        "color",
        "rgb(20, 126, 166)",
    )

    settings_geometry = mock_page.evaluate(
        """
        () => {
            const panel = document.getElementById('memory-settings-panel');
            const reviews = Array.from(panel.querySelectorAll('.review-toggle'));
            const review = reviews[0];
            const strongReview = reviews[1];
            const storage = panel.querySelector('.storage-location-section');
            const toggle = review.querySelector('.auto-review-toggle-btn');
            const strongToggle = strongReview.querySelector('.auto-review-toggle-btn');
            const reviewHelp = review.querySelector('.memory-setting-help');
            const reviewTitle = review.querySelector('.review-toggle-title');
            const strongReviewTitle = strongReview.querySelector('.review-toggle-title');
            const manage = document.getElementById('storage-location-manage-btn');
            const open = document.getElementById('storage-location-open-btn');
            const reviewRect = review.getBoundingClientRect();
            const strongReviewRect = strongReview.getBoundingClientRect();
            const panelRect = panel.getBoundingClientRect();
            const storageRect = storage.getBoundingClientRect();
            const toggleRect = toggle.getBoundingClientRect();
            const strongToggleRect = strongToggle.getBoundingClientRect();
            const reviewHelpRect = reviewHelp.getBoundingClientRect();
            const reviewTitleRect = reviewTitle.getBoundingClientRect();
            const strongReviewTitleRect = strongReviewTitle.getBoundingClientRect();
            const manageRect = manage.getBoundingClientRect();
            const openRect = open.getBoundingClientRect();
            return {
                reviewHeight: Math.round(reviewRect.height),
                strongReviewHeight: Math.round(strongReviewRect.height),
                panelWidth: Math.round(panelRect.width),
                panelHeight: Math.round(panelRect.height),
                panelContentBottomGap: Math.round(panelRect.bottom - storageRect.bottom),
                toggleWidth: Math.round(toggleRect.width),
                toggleHeight: Math.round(toggleRect.height),
                strongToggleWidth: Math.round(strongToggleRect.width),
                strongToggleHeight: Math.round(strongToggleRect.height),
                helpWidth: Math.round(reviewHelpRect.width),
                helpHeight: Math.round(reviewHelpRect.height),
                helpHitTargetExtended: document.elementFromPoint(
                    reviewHelpRect.left - 6,
                    reviewHelpRect.top + (reviewHelpRect.height / 2)
                ) === reviewHelp,
                toggleHitTargetExtended: document.elementFromPoint(
                    toggleRect.left - 6,
                    toggleRect.top + (toggleRect.height / 2)
                ) === toggle,
                settingTitlesAligned: Math.abs(reviewTitleRect.left - strongReviewTitleRect.left) <= 1,
                settingSwitchesAligned: Math.abs(toggleRect.right - strongToggleRect.right) <= 1,
                storageButtonsStacked: openRect.top > manageRect.bottom,
                storageButtonsEqualWidth: Math.abs(manageRect.width - openRect.width) <= 1,
                storageButtonGap: Math.round(openRect.top - manageRect.bottom),
            };
        }
        """
    )
    panel_height = settings_geometry.pop("panelHeight")
    assert 440 <= panel_height <= 510
    assert settings_geometry == {
        "reviewHeight": 52,
        "strongReviewHeight": 52,
        "panelWidth": 360,
        "panelContentBottomGap": 17,
        "toggleWidth": 36,
        "toggleHeight": 20,
        "strongToggleWidth": 36,
        "strongToggleHeight": 20,
        "helpWidth": 22,
        "helpHeight": 22,
        "helpHitTargetExtended": True,
        "toggleHitTargetExtended": True,
        "settingTitlesAligned": True,
        "settingSwitchesAligned": True,
        "storageButtonsStacked": True,
        "storageButtonsEqualWidth": True,
        "storageButtonGap": 8,
    }

    checkbox = mock_page.locator("#review-toggle-checkbox")
    review_help = mock_page.locator(".review-toggle", has=checkbox).locator(".memory-setting-help")
    review_help.hover()
    expect(mock_page.locator("#review-setting-tooltip")).to_be_visible()
    mock_page.locator("#storage-location-manage-btn").hover()
    expect(mock_page.locator("#review-setting-tooltip")).to_be_hidden()

    review_help.focus()
    expect(mock_page.locator("#review-setting-tooltip")).to_be_visible()
    expect(review_help).to_be_focused()
    mock_page.locator("#storage-location-manage-btn").focus()
    expect(mock_page.locator("#review-setting-tooltip")).to_be_hidden()

    expected_review_state = not checkbox.is_checked()
    with mock_page.expect_response(
        lambda response: "/api/memory/review_config" in response.url
        and response.request.method == "POST"
    ):
        mock_page.locator("label.auto-review-toggle-btn[for='review-toggle-checkbox']").click()
    expect(checkbox).to_be_checked(checked=expected_review_state)
    expect(mock_page.locator("#review-setting-tooltip")).to_be_hidden()

    mock_page.keyboard.press("Escape")
    expect(mock_page.locator("#memory-settings-panel")).to_be_hidden()
    expect(settings_trigger).to_be_focused()

    _open_auxiliary_panel(mock_page, "guide")
    guide_trigger = mock_page.locator("#memory-guide-trigger")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    tutorial.locator("[data-tutorial-page='memory_browser']").click()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_enabled()

    # aria-modal panels block pointer access to header actions until dismissed.
    assert mock_page.evaluate(
        """
        () => {
            const trigger = document.getElementById('memory-settings-trigger');
            const backdrop = document.getElementById('memory-aux-panel-backdrop');
            const rect = trigger.getBoundingClientRect();
            return document.elementFromPoint(
                rect.left + rect.width / 2,
                rect.top + rect.height / 2
            ) === backdrop;
        }
        """
    )
    mock_page.keyboard.press("Escape")
    expect(mock_page.locator("#memory-guide-panel")).to_be_hidden()
    expect(guide_trigger).to_be_focused()
    expect(mock_page.locator("#tutorial-reset-select")).to_have_count(1)

    _open_auxiliary_panel(mock_page, "settings")
    mock_page.keyboard.press("Escape")

    expect(mock_page.locator("#memory-import-trigger")).to_be_enabled()
    _open_auxiliary_panel(mock_page, "import")
    import_trigger = mock_page.locator("#memory-import-trigger")
    import_panel = mock_page.locator("#memory-import-panel")
    import_help = import_panel.locator(".external-memory-import-help")
    import_tooltip = import_panel.locator("#external-memory-import-note-tooltip")
    import_geometry = import_panel.evaluate(
        """
        panel => {
            const rect = panel.getBoundingClientRect();
            return {
                height: Math.round(rect.height),
                clearsViewportBottom: window.innerHeight - rect.bottom > 100,
            };
        }
        """
    )
    assert import_geometry["height"] < 420
    assert import_geometry["clearsViewportBottom"] is True
    expect(import_panel.locator(".external-memory-import-note")).to_have_count(0)
    expect(import_panel.locator(".external-memory-import-section > .file-list-title")).to_have_count(0)
    expect(import_tooltip).to_be_hidden()
    import_help.hover()
    expect(import_tooltip).to_be_visible()
    mock_page.locator("#external-memory-pick-btn").hover()
    expect(import_tooltip).to_be_hidden()
    import_help.focus()
    expect(import_tooltip).to_be_visible()
    mock_page.locator("#external-memory-pick-btn").focus()
    expect(import_tooltip).to_be_hidden()
    expect(mock_page.locator("#external-memory-pick-btn")).to_have_css(
        "color",
        "rgb(20, 126, 166)",
    )
    expect(mock_page.locator("#external-memory-target")).to_contain_text("测试猫娘")
    format_cascader = mock_page.locator("#external-memory-format-cascader")
    format_trigger = format_cascader.locator(".external-memory-format-trigger")
    format_geometry_before = import_panel.evaluate(
        """
        panel => ({
            panelHeight: panel.getBoundingClientRect().height,
            triggerHeight: panel.querySelector('.external-memory-format-trigger')
                .getBoundingClientRect().height,
        })
        """
    )
    format_trigger.click()
    format_cascader.locator("[data-external-memory-format='openclaw']").click()
    expect(mock_page.locator("#external-memory-format")).to_have_value("openclaw")
    format_geometry_after = import_panel.evaluate(
        """
        panel => ({
            panelHeight: panel.getBoundingClientRect().height,
            triggerHeight: panel.querySelector('.external-memory-format-trigger')
                .getBoundingClientRect().height,
        })
        """
    )
    assert format_geometry_before == format_geometry_after
    assert format_geometry_after["triggerHeight"] == 44

    mock_page.keyboard.press("Escape")
    expect(mock_page.locator("#memory-import-panel")).to_be_hidden()
    expect(import_trigger).to_be_focused()
    assert import_trigger.evaluate("button => button.matches(':focus-visible')") is True
    expect(guide_trigger).to_have_attribute("aria-expanded", "false")

    _open_auxiliary_panel(mock_page, "import")
    import_panel.locator("[data-memory-panel-close]").click()
    expect(import_panel).to_be_hidden()
    expect(import_trigger).to_be_focused()
    pointer_focus = import_trigger.evaluate(
        """
        button => ({
            focusVisible: button.matches(':focus-visible'),
            outlineWidth: getComputedStyle(button).outlineWidth,
            boxShadow: getComputedStyle(button).boxShadow,
        })
        """
    )
    assert pointer_focus == {
        "focusVisible": False,
        "outlineWidth": "0px",
        "boxShadow": "none",
    }


@pytest.mark.frontend
def test_memory_browser_auxiliary_panel_traps_tab_focus(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")

    panel = mock_page.locator("#memory-settings-panel")
    close_button = panel.locator("[data-memory-panel-close]")
    expect(panel).to_be_focused()
    mock_page.keyboard.press("Tab")
    expect(close_button).to_be_focused()
    close_button.press("Shift+Tab")
    assert panel.evaluate("panel => panel.contains(document.activeElement)")


@pytest.mark.frontend
def test_memory_browser_guide_panel_skips_hidden_options_when_trapping_tab(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")

    panel = mock_page.locator("#memory-guide-panel")
    close_button = panel.locator("[data-memory-panel-close]")
    tutorial_trigger = panel.locator("#tutorial-reset-cascader > .tutorial-cascader-trigger")
    reset_button = panel.locator("#tutorial-reset-btn")
    expect(tutorial_trigger).to_have_css("color", "rgb(20, 126, 166)")
    geometry = panel.evaluate(
        """
        panel => {
            const cascade = panel.querySelector('#tutorial-reset-cascader').getBoundingClientRect();
            const reset = panel.querySelector('#tutorial-reset-btn').getBoundingClientRect();
            const panelRect = panel.getBoundingClientRect();
            return {
                width: Math.round(panelRect.width),
                height: Math.round(panelRect.height),
                stacked: reset.top > cascade.bottom,
                gap: Math.round(reset.top - cascade.bottom),
                aligned: Math.abs(cascade.left - reset.left) <= 1
                    && Math.abs(cascade.right - reset.right) <= 1,
                equalWidth: Math.abs(cascade.width - reset.width) <= 1,
                controlWidth: Math.round(cascade.width),
                duplicateTitles: panel.querySelectorAll('.file-list-title').length,
            };
        }
        """
    )
    panel_height = geometry.pop("height")
    control_width = geometry.pop("controlWidth")
    assert 180 <= panel_height <= 220
    assert 320 <= control_width <= 330
    assert geometry == {
        "width": 360,
        "stacked": True,
        "gap": 8,
        "aligned": True,
        "equalWidth": True,
        "duplicateTitles": 0,
    }
    expect(panel).to_be_focused()
    mock_page.keyboard.press("Tab")
    expect(close_button).to_be_focused()
    close_button.press("Tab")
    expect(tutorial_trigger).to_be_focused()
    tutorial_trigger.press("Tab")
    expect(close_button).to_be_focused()

    tutorial_trigger.click()
    tutorial_option = panel.locator("[data-tutorial-page='memory_browser']")
    expect(tutorial_option).to_have_css("color", "rgb(20, 126, 166)")
    mock_page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    expect(tutorial_trigger).to_have_css("color", "rgb(125, 211, 252)")
    expect(tutorial_option).to_have_css("color", "rgb(125, 211, 252)")
    expect(tutorial_trigger).to_have_css("box-shadow", "none")
    mock_page.evaluate("document.documentElement.removeAttribute('data-theme')")
    tutorial_option.click()
    expect(reset_button).to_be_enabled()
    expect(reset_button).to_have_css("color", "rgb(20, 126, 166)")


@pytest.mark.frontend
def test_memory_browser_auxiliary_panels_use_existing_icon_assets(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")

    close_buttons = mock_page.locator("[data-memory-panel-close]")
    expect(close_buttons).to_have_count(3)
    expect(
        close_buttons.locator(
            "img[src='/static/icons/close_button.png'][alt=''][aria-hidden='true']"
        )
    ).to_have_count(3)

    setting_help = mock_page.locator(".memory-setting-help")
    expect(setting_help).to_have_count(3)
    expect(setting_help.locator("img[src='/static/icons/exclamation.png'][alt=''][aria-hidden='true']")).to_have_count(3)
    expect(mock_page.locator(".review-toggle-heading > [role='tooltip']")).to_have_count(2)


@pytest.mark.frontend
def test_memory_browser_wide_workspace_keeps_roles_and_save_actions_in_view(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 1280, "height": 720})

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    role_trigger = mock_page.locator("#memory-role-panel-trigger")
    expect(role_trigger).to_be_visible()
    expect(role_trigger).to_have_attribute("aria-expanded", "true")
    expect(role_trigger).to_have_text("记忆库 · 测试猫娘")
    expect(mock_page.locator("#memory-file-list button.cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    expect(mock_page.locator(".tips-container")).to_be_in_viewport()
    expect(mock_page.locator("#save-row")).to_be_in_viewport()
    result = _run_and_observe_row_exit(mock_page, "#clear-memory-btn", 1)
    assert result["elapsed"] <= 320
    assert result["scheduledDelays"]
    assert 300 in result["scheduledDelays"]
    expect(mock_page.locator("#save-status")).not_to_be_empty()
    expect(mock_page.locator("#save-status")).to_be_in_viewport()
    assert mock_page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.frontend
def test_memory_browser_wide_role_button_toggles_the_full_sidebar(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 1280, "height": 720})
    mock_page.goto(f"{running_server}/memory_browser")

    trigger = mock_page.locator("#memory-role-panel-trigger")
    sidebar = mock_page.locator("#memory-role-sidebar")
    expect(sidebar).to_be_visible(timeout=10000)
    expect(trigger).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(trigger.locator(".memory-role-panel-trigger-divider")).to_have_css("transform", "none")

    trigger.click()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    expect(sidebar).to_be_hidden()
    expect(trigger.locator(".memory-role-panel-trigger-divider")).not_to_have_css("transform", "none")
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "collapsed",
    )
    collapsed_geometry = mock_page.evaluate(
        """
        () => {
            const main = document.querySelector('.main').getBoundingClientRect();
            const editor = document.querySelector('.editor').getBoundingClientRect();
            return {
                editorUsesFullRow: Math.abs(editor.left - main.left) <= 1
                    && Math.abs(editor.right - main.right) <= 1,
                noPageOverflow:
                    document.documentElement.scrollWidth
                    <= document.documentElement.clientWidth,
            };
        }
        """
    )
    assert collapsed_geometry == {
        "editorUsesFullRow": True,
        "noPageOverflow": True,
    }

    trigger.click()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(sidebar).to_be_visible()
    expect(trigger.locator(".memory-role-panel-trigger-divider")).to_have_css("transform", "none")
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "expanded",
    )
    mock_page.locator(".tips-container").click()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(sidebar).to_be_visible()


@pytest.mark.frontend
@pytest.mark.parametrize(("width", "height"), [(720, 560), (767, 600)])
def test_memory_browser_compact_role_panel_reuses_selection_and_restores_focus(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
    width: int,
    height: int,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": width, "height": height})
    mock_page.emulate_media(reduced_motion="no-preference")

    mock_page.goto(f"{running_server}/memory_browser")
    selected_role = mock_page.locator("#memory-file-list button.cat-btn[aria-current='true']")
    expect(selected_role).to_have_count(1, timeout=10000)
    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    expect(mock_page.locator(".tips-container")).to_be_in_viewport()
    expect(mock_page.locator("#memory-compact-current-role-name")).not_to_be_empty()
    expect(mock_page.locator("#save-row")).to_be_in_viewport()

    trigger = mock_page.locator("#memory-role-panel-trigger")
    panel = mock_page.locator("#memory-role-panel")
    expect(trigger).to_be_visible()
    expect(trigger).to_be_in_viewport()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    expect(panel).to_be_hidden()

    trigger.click()
    expect(panel).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(panel).to_have_css("transform", "none")
    geometry = mock_page.evaluate(
        """
        () => {
            const panel = document.getElementById('memory-role-panel');
            const utilityBar = document.querySelector('.memory-utility-bar');
            const panelRect = panel.getBoundingClientRect();
            const utilityBarRect = utilityBar.getBoundingClientRect();
            const isHit = (element) => {
                const rect = element.getBoundingClientRect();
                const hit = document.elementFromPoint(
                    rect.left + rect.width / 2,
                    rect.top + rect.height / 2,
                );
                return element.contains(hit);
            };
            return {
                panelBelowUtilityBar: panelRect.top >= utilityBarRect.bottom,
                panelUsesLeftDrawerWidth:
                    Math.abs(panelRect.left - 10) <= 1 && panelRect.width <= 321,
                triggerHit: isHit(document.getElementById('memory-role-panel-trigger')),
                guideHit: isHit(document.getElementById('memory-guide-trigger')),
                exportHit: isHit(document.getElementById('memory-export-logs-trigger')),
                settingsHit: isHit(document.getElementById('memory-settings-trigger')),
            };
        }
        """
    )
    assert geometry == {
        "panelBelowUtilityBar": True,
        "panelUsesLeftDrawerWidth": True,
        "triggerHit": True,
        "guideHit": True,
        "exportHit": True,
        "settingsHit": True,
    }

    trigger.click()
    expect(panel).to_be_hidden()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    expect(trigger).to_be_focused()

    trigger.click()
    with mock_page.expect_request("**/api/memory/recent_file?**"):
        selected_role.click()
    expect(panel).to_be_hidden()

    trigger.click()
    mock_page.keyboard.press("Escape")
    expect(panel).to_be_hidden()
    expect(trigger).to_be_focused()

    trigger.click()
    mock_page.mouse.click(width - 20, 120)
    expect(panel).to_be_hidden()
    result = _run_and_observe_row_exit(mock_page, "#clear-memory-btn", 1)
    assert result["elapsed"] <= 320
    assert result["scheduledDelays"]
    assert 300 in result["scheduledDelays"]
    expect(mock_page.locator("#save-status")).not_to_be_empty()
    expect(mock_page.locator("#save-status")).to_be_in_viewport()
    assert mock_page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.frontend
def test_memory_browser_compact_role_panel_stays_inside_low_viewport(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=[
            "recent_测试猫娘.json",
            *[f"recent_角色{index}.json" for index in range(1, 9)],
        ],
    )
    mock_page.set_viewport_size({"width": 720, "height": 300})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list button.cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    trigger = mock_page.locator("#memory-role-panel-trigger")
    panel = mock_page.locator("#memory-role-panel")
    trigger.click()
    expect(panel).to_be_visible()

    geometry = mock_page.evaluate(
        """
        () => {
            const panelRect = document.getElementById('memory-role-panel').getBoundingClientRect();
            const utilityBarRect = document.querySelector('.memory-utility-bar').getBoundingClientRect();
            return {
                panelBelowUtilityBar: panelRect.top >= utilityBarRect.bottom,
                panelWithinViewport: panelRect.bottom <= window.innerHeight - 12,
            };
        }
        """
    )
    assert geometry == {"panelBelowUtilityBar": True, "panelWithinViewport": True}


@pytest.mark.frontend
@pytest.mark.parametrize(
    ("width", "layout"),
    [(839, "compact"), (840, "wide")],
)
def test_memory_browser_role_layout_uses_exact_two_state_boundary(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
    width: int,
    layout: str,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": width, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout=layout)


def _assert_memory_layout(page: Page, layout: str) -> None:
    assert layout in {"wide", "compact"}
    expect(page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-layout",
        layout,
    )
    if layout == "compact":
        expect(page.locator("#memory-role-panel-trigger")).to_be_visible()
        expect(page.locator("#memory-role-sidebar")).to_be_hidden()
        expected_expanded = "false"
    else:
        expect(page.locator("#memory-role-panel-trigger")).to_be_visible()
        expect(page.locator("#memory-role-sidebar")).to_be_visible()
        expect(page.locator(
            "#memory-file-list .cat-btn[aria-current='true'] .memory-role-name"
        )).to_be_visible()
        expected_expanded = "true"
    assert page.locator("#memory-role-panel-trigger").get_attribute("aria-expanded") == expected_expanded
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth"
    )


@pytest.mark.frontend
def test_memory_browser_responsive_breakpoint_does_not_use_view_transition(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        (() => {
            window.__memoryLayoutTransitionCalls = 0;
            document.startViewTransition = update => {
                window.__memoryLayoutTransitionCalls += 1;
                update();
                return {
                    finished: Promise.resolve(),
                    skipTransition() {},
                };
            };
        })();
        """
    )
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")
    mock_page.evaluate(
        """
        () => {
            window.__memoryResponsiveClassSnapshots = [];
            const body = document.body;
            window.__memoryResponsiveClassObserver = new MutationObserver(() => {
                window.__memoryResponsiveClassSnapshots.push(body.className);
            });
            window.__memoryResponsiveClassObserver.observe(body, {
                attributes: true,
                attributeFilter: ['class'],
            });
        }
        """
    )

    mock_page.set_viewport_size({"width": 839, "height": 600})

    _assert_memory_layout(mock_page, layout="compact")
    mock_page.set_viewport_size({"width": 840, "height": 600})

    _assert_memory_layout(mock_page, layout="wide")
    assert mock_page.evaluate("window.__memoryLayoutTransitionCalls") == 0
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-layout-transitioning\b")
    )
    snapshots = mock_page.evaluate(
        """
        () => {
            window.__memoryResponsiveClassObserver.disconnect();
            return window.__memoryResponsiveClassSnapshots;
        }
        """
    )
    assert any("is-memory-responsive-collapsing" in value for value in snapshots)
    assert any("is-memory-responsive-wide-start" in value for value in snapshots)
    assert any("is-memory-responsive-expanding" in value for value in snapshots)
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-responsive-transitioning\b")
    )


@pytest.mark.frontend
def test_memory_browser_responsive_transition_reverses_without_snapshot_restart(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        (() => {
            window.__memoryLayoutTransitionCalls = 0;
            document.startViewTransition = update => {
                window.__memoryLayoutTransitionCalls += 1;
                update();
                return {
                    finished: Promise.resolve(),
                    skipTransition() {},
                };
            };
        })();
        """
    )
    mock_page.set_viewport_size({"width": 840, "height": 600})
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")

    mock_page.set_viewport_size({"width": 839, "height": 600})
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-responsive-collapsing\b")
    )
    mock_page.set_viewport_size({"width": 840, "height": 600})

    _assert_memory_layout(mock_page, layout="wide")
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-responsive-transitioning\b")
    )
    assert mock_page.evaluate("window.__memoryLayoutTransitionCalls") == 0


@pytest.mark.frontend
def test_memory_browser_manual_wide_sidebar_toggle_uses_live_layout_transition(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        (() => {
            window.__memoryLayoutTransitionCalls = 0;
            document.startViewTransition = update => {
                window.__memoryLayoutTransitionCalls += 1;
                update();
                return {
                    finished: Promise.resolve(),
                    skipTransition() {},
                };
            };
        })();
        """
    )
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")
    mock_page.evaluate(
        """
        () => {
            window.__memoryManualSidebarClassSnapshots = [];
            const body = document.body;
            window.__memoryManualSidebarObserver = new MutationObserver(() => {
                window.__memoryManualSidebarClassSnapshots.push(body.className);
            });
            window.__memoryManualSidebarObserver.observe(body, {
                attributes: true,
                attributeFilter: ['class'],
            });
        }
        """
    )

    trigger = mock_page.locator("#memory-role-panel-trigger")
    expect(trigger).to_contain_text("记忆库")
    expect(trigger).to_contain_text("测试猫娘")
    collapse_start = mock_page.evaluate(
        """
        () => {
            const trigger = document.getElementById('memory-role-panel-trigger');
            trigger.click();
            return {
                ariaExpanded: trigger.getAttribute('aria-expanded'),
                sidebarState: document.body.dataset.memorySidebar,
                isCollapsing: document.body.classList.contains(
                    'is-memory-manual-sidebar-collapsing'
                ),
            };
        }
        """
    )
    assert collapse_start == {
        "ariaExpanded": "false",
        "sidebarState": "expanded",
        "isCollapsing": True,
    }

    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-collapsing\b")
    )
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "expanded",
    )
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "collapsed",
    )
    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    expect(trigger).to_contain_text("记忆库")
    expect(trigger).to_contain_text("测试猫娘")

    trigger.click()
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "expanded",
    )
    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(trigger).to_contain_text("记忆库")
    expect(trigger).to_contain_text("测试猫娘")
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )
    snapshots = mock_page.evaluate(
        """
        () => {
            window.__memoryManualSidebarObserver.disconnect();
            return window.__memoryManualSidebarClassSnapshots;
        }
        """
    )
    assert any("is-memory-manual-sidebar-collapsing" in value for value in snapshots)
    assert any("is-memory-manual-sidebar-wide-start" in value for value in snapshots)
    assert any("is-memory-manual-sidebar-expanding" in value for value in snapshots)
    assert mock_page.evaluate("window.__memoryLayoutTransitionCalls") == 0


@pytest.mark.frontend
def test_memory_browser_wide_collapsed_sidebar_has_hover_preview_without_reflow(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        "window.localStorage.setItem('neko_tutorial_memory_browser', 'true')"
    )
    mock_page.set_viewport_size({"width": 1000, "height": 700})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")

    trigger = mock_page.locator("#memory-role-panel-trigger")
    hover_target = mock_page.locator("#memory-role-hover-target")
    sidebar = mock_page.locator("#memory-role-sidebar")
    editor = mock_page.locator(".editor")
    main = mock_page.locator(".main")
    utility_bar = mock_page.locator(".memory-utility-bar")

    trigger.click()
    expect(sidebar).to_be_hidden()
    expect(hover_target).to_be_visible()
    collapsed_editor_box = editor.bounding_box()
    collapsed_hover_box = hover_target.bounding_box()
    assert collapsed_editor_box is not None
    assert collapsed_hover_box is not None

    mock_page.evaluate(
        """
        y => document.dispatchEvent(new MouseEvent('mouseleave', {
            clientX: 0,
            clientY: y,
            relatedTarget: null,
        }))
        """,
        collapsed_hover_box["y"] - 8,
    )
    expect(sidebar).to_be_hidden()

    mock_page.evaluate(
        """
        y => document.dispatchEvent(new MouseEvent('mouseleave', {
            clientX: 0,
            clientY: y,
            relatedTarget: null,
        }))
        """,
        collapsed_hover_box["y"] + 100,
    )
    expect(sidebar).to_be_visible()
    editor.evaluate(
        "element => element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: null }))"
    )
    expect(sidebar).to_be_hidden(timeout=1000)

    mock_page.evaluate(
        """
        ({ x, y }) => {
            const event = new MouseEvent('mousemove', {
                bubbles: true,
                clientX: x,
                clientY: y,
            });
            Object.defineProperty(event, 'movementX', { value: x + 20 });
            document.dispatchEvent(event);
        }
        """,
        {
            "x": collapsed_hover_box["x"] + 80,
            "y": collapsed_hover_box["y"] + 100,
        },
    )
    expect(sidebar).to_be_visible()
    editor.evaluate(
        "element => element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: null }))"
    )
    expect(sidebar).to_be_hidden(timeout=1000)

    mock_page.mouse.move(collapsed_hover_box["x"] + 4, collapsed_hover_box["y"] + 100)
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-role-hover-preview-open\b")
    )
    expect(sidebar).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "false")
    mock_page.wait_for_timeout(180)
    main_box = main.bounding_box()
    preview_box = mock_page.locator(".left-column").bounding_box()
    hover_box = hover_target.bounding_box()
    role_list_box = mock_page.locator("#memory-file-list").bounding_box()
    selected_role_box = mock_page.locator(
        "#memory-file-list .cat-btn[aria-current='true']"
    ).bounding_box()
    assert main_box is not None
    assert preview_box is not None
    assert hover_box is not None
    assert role_list_box is not None
    assert selected_role_box is not None
    assert preview_box["x"] == pytest.approx(main_box["x"], abs=0.5)
    assert preview_box["y"] == pytest.approx(main_box["y"], abs=0.5)
    assert preview_box["height"] == pytest.approx(main_box["height"], abs=0.5)
    assert hover_box["y"] == pytest.approx(main_box["y"], abs=0.5)
    assert hover_box["height"] == pytest.approx(main_box["height"], abs=0.5)
    assert selected_role_box["y"] >= role_list_box["y"] - 0.5
    preview_editor_box = editor.bounding_box()
    assert preview_editor_box is not None
    assert preview_editor_box["x"] == pytest.approx(collapsed_editor_box["x"], abs=0.5)
    assert preview_editor_box["width"] == pytest.approx(collapsed_editor_box["width"], abs=0.5)

    mock_page.locator(".left-column").hover()
    mock_page.wait_for_timeout(150)
    expect(sidebar).to_be_visible()

    mock_page.locator(".left-column").evaluate(
        "element => element.dispatchEvent(new MouseEvent('mouseleave', { relatedTarget: null }))"
    )
    mock_page.wait_for_timeout(180)
    expect(sidebar).to_be_visible()

    mock_page.evaluate("window.dispatchEvent(new Event('blur'))")
    mock_page.wait_for_timeout(180)
    expect(sidebar).to_be_visible()

    utility_bar.evaluate(
        "element => element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: null }))"
    )
    mock_page.wait_for_timeout(180)
    expect(sidebar).to_be_visible()

    editor.evaluate(
        "element => element.dispatchEvent(new MouseEvent('mouseover', { bubbles: true, relatedTarget: null }))"
    )
    expect(sidebar).to_be_hidden(timeout=1000)

    mock_page.mouse.move(collapsed_hover_box["x"] + 4, collapsed_hover_box["y"] + 100)
    expect(sidebar).to_be_visible()
    trigger.click()
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "expanded",
    )
    expect(sidebar).to_be_visible()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-role-hover-preview-open\b")
    )

    mock_page.set_viewport_size({"width": 839, "height": 700})
    _assert_memory_layout(mock_page, layout="compact")
    expect(hover_target).to_be_hidden()


@pytest.mark.frontend
def test_memory_browser_manual_sidebar_does_not_require_view_transition(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        (() => {
            Object.defineProperty(document, 'startViewTransition', {
                configurable: true,
                value: undefined,
            });
        })();
        """
    )
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")

    trigger = mock_page.locator("#memory-role-panel-trigger")
    trigger.click()
    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    trigger.click()
    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )


@pytest.mark.frontend
def test_memory_browser_compact_drawer_coalesces_position_measurement(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 839, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="compact")
    mock_page.evaluate(
        """
        (() => {
            const utilityBar = document.querySelector('.memory-utility-bar');
            const original = utilityBar.getBoundingClientRect.bind(utilityBar);
            window.__memoryRolePanelPositionReads = 0;
            utilityBar.getBoundingClientRect = () => {
                window.__memoryRolePanelPositionReads += 1;
                return original();
            };
        })()
        """
    )

    mock_page.evaluate(
        """
        Promise.all([
            ...Array.from({ length: 5 }, () => window.dispatchEvent(new Event('resize'))),
            new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        ])
        """
    )
    assert mock_page.evaluate("window.__memoryRolePanelPositionReads") == 0

    mock_page.locator("#memory-role-panel-trigger").click()
    expect(mock_page.locator("#memory-role-panel")).to_be_visible()
    mock_page.evaluate("window.__memoryRolePanelPositionReads = 0")
    mock_page.evaluate(
        """
        Promise.all([
            ...Array.from({ length: 5 }, () => window.dispatchEvent(new Event('resize'))),
            new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))),
        ])
        """
    )

    assert mock_page.evaluate("window.__memoryRolePanelPositionReads") == 1
    assert mock_page.locator("#memory-role-panel").evaluate(
        "element => element.style.getPropertyValue('--memory-role-panel-top')"
    ).endswith("px")


@pytest.mark.frontend
def test_memory_browser_manual_sidebar_rapidly_reverses_without_stuck_state(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")
    trigger = mock_page.locator("#memory-role-panel-trigger")

    trigger.click()
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-collapsing\b")
    )
    trigger.click()

    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    expect(mock_page.locator("body.memory-browser-page")).to_have_attribute(
        "data-memory-sidebar",
        "expanded",
    )
    expect(mock_page.locator("#memory-role-panel")).not_to_have_class(
        re.compile(r"\bis-open\b")
    )
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )

    trigger.click()
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-collapsing\b")
    )
    mock_page.evaluate("window.dispatchEvent(new Event('beforeunload'))")
    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )

    trigger.click()
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-wide-start\b|\bis-memory-manual-sidebar-expanding\b")
    )
    mock_page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )
    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()


@pytest.mark.frontend
def test_memory_browser_manual_sidebar_respects_reduced_motion(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.emulate_media(reduced_motion="reduce")
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")
    trigger = mock_page.locator("#memory-role-panel-trigger")
    trigger.click()
    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    trigger.click()
    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )

    mock_page.emulate_media(reduced_motion="no-preference")
    trigger.click()
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-collapsing\b")
    )
    mock_page.emulate_media(reduced_motion="reduce")
    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )

    css = (
        Path(__file__).parents[2] / "static/css/memory_browser.css"
    ).read_text(encoding="utf-8")
    reduced_motion_css = css.rsplit("@media (prefers-reduced-motion: reduce)", 1)[1]
    assert "body.is-memory-manual-sidebar-transitioning .main" in reduced_motion_css
    assert "body.is-memory-manual-sidebar-transitioning .left-column" in reduced_motion_css
    assert ".memory-role-panel-trigger-divider" in reduced_motion_css
    assert "transition: none" in reduced_motion_css


@pytest.mark.frontend
def test_memory_browser_runtime_reduced_motion_commits_pending_sidebar_toggle(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")

    mock_page.locator("#memory-role-panel-trigger").click()
    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-collapsing\b")
    )

    mock_page.emulate_media(reduced_motion="reduce")

    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )


@pytest.mark.frontend
def test_memory_browser_cancelled_beforeunload_commits_pending_sidebar_toggle(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.set_viewport_size({"width": 840, "height": 600})

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    _assert_memory_layout(mock_page, layout="wide")

    mock_page.locator("#memory-role-panel-trigger").click()
    expect(mock_page.locator("#memory-role-sidebar")).to_be_visible()
    expect(mock_page.locator("body.memory-browser-page")).to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-collapsing\b")
    )

    mock_page.evaluate(
        "window.dispatchEvent(new Event('beforeunload', { cancelable: true }))"
    )

    expect(mock_page.locator("#memory-role-sidebar")).to_be_hidden()
    expect(mock_page.locator("body.memory-browser-page")).not_to_have_class(
        re.compile(r"\bis-memory-manual-sidebar-transitioning\b")
    )


@pytest.mark.frontend
def test_memory_browser_beforeunload_blocks_unsaved_memory_changes(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )

    mock_page.locator("#clear-memory-btn").click()
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()

    result = mock_page.evaluate(
        """
        () => {
            const event = new Event('beforeunload', { cancelable: true });
            const dispatched = window.dispatchEvent(event);
            return {
                defaultPrevented: event.defaultPrevented,
                dispatched,
            };
        }
        """
    )

    assert result == {
        "defaultPrevented": True,
        "dispatched": False,
    }


def test_memory_browser_responsive_motion_static_performance_contract() -> None:
    project_root = Path(__file__).parents[2]
    css = (project_root / "static/css/memory_browser.css").read_text(encoding="utf-8")
    js = (project_root / "static/js/memory_browser.js").read_text(encoding="utf-8")
    responsive_live_css = css.split(
        "body.is-memory-responsive-transitioning .main", 1
    )[1].split('body[data-memory-layout="wide"][data-memory-sidebar="expanded"]', 1)[0]
    responsive_motion_js = js.split(
        "function getMemoryLayoutMode", 1
    )[1].split("function setMemoryCurrentRoleName", 1)[0]

    assert "grid-template-columns var(--memory-motion-fast)" in responsive_live_css
    assert "gap var(--memory-motion-fast)" in responsive_live_css
    assert "opacity var(--memory-motion-fast)" in responsive_live_css
    assert "transform var(--memory-motion-fast)" in responsive_live_css
    assert "grid-template-columns: 0 minmax(0, 1fr)" in responsive_live_css
    assert "transform: translateX(-12px)" in responsive_live_css
    assert "body.is-memory-manual-sidebar-transitioning .main" in responsive_live_css
    assert "body.is-memory-manual-sidebar-transitioning .left-column" in responsive_live_css
    assert "body.is-memory-manual-sidebar-collapsing .main" in responsive_live_css
    assert "body.is-memory-manual-sidebar-wide-start .main" in responsive_live_css
    assert re.search(r"transition\s*:\s*all\b", responsive_live_css) is None
    assert re.search(r"\bfilter\s*:", responsive_live_css) is None
    assert re.search(r"\bblur\s*\(", responsive_live_css) is None
    assert "::view-transition" not in css
    assert "document.startViewTransition" not in js
    assert "getBoundingClientRect" not in responsive_motion_js
    assert re.search(r"MEMORY_RESPONSIVE_LAYOUT_MS\s*=\s*170", js)
    assert "window.addEventListener('pagehide', teardownMemoryLayoutTransitionAndCommit)" in js

    responsive_update = re.search(
        r"function updateMemoryLayoutMode\(mode\)\s*\{(?P<body>[\s\S]*?)\n    \}",
        js,
    )
    assert responsive_update is not None
    assert "runMemoryLayoutTransition" not in responsive_update.group("body")
    assert "runMemoryResponsiveLayoutTransition(mode)" in responsive_update.group("body")
    assert "applyMemoryLayoutMode(mode)" in responsive_update.group("body")

    manual_update = re.search(
        r"function updateWideMemorySidebarExpanded\(expanded\)\s*\{(?P<body>[\s\S]*?)\n    \}",
        js,
    )
    assert manual_update is not None
    assert "runMemoryWideSidebarTransition(nextExpanded)" in manual_update.group("body")
    assert "runMemoryLayoutTransition" not in manual_update.group("body")

    position_scheduler = re.search(
        r"function scheduleMemoryRolePanelPositionSync\(\)\s*\{(?P<body>[\s\S]*?)\n    \}",
        js,
    )
    assert position_scheduler is not None
    assert "requestAnimationFrame" in position_scheduler.group("body")
    assert "trigger.getAttribute('aria-expanded') !== 'true'" in position_scheduler.group("body")
    assert "window.addEventListener('resize', scheduleMemoryRolePanelPositionSync)" in js
    assert "window.addEventListener('resize', syncMemoryRolePanelPosition)" not in js
    assert "new ResizeObserver(syncMemoryRolePanelPosition)" not in js


@pytest.mark.frontend
def test_memory_browser_tutorial_cascader_dark_mode_uses_readable_text(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script("window.localStorage.setItem('neko-dark-mode', 'true')")

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)
    tutorial.locator(":scope > .tutorial-cascader-trigger").click()

    colors = mock_page.evaluate(
        """
        () => {
            const tutorial = document.getElementById('tutorial-reset-cascader');
            const trigger = tutorial.querySelector(':scope > .tutorial-cascader-trigger');
            const popup = tutorial.querySelector(':scope > .tutorial-cascader-popup');
            const column = popup.querySelector('.tutorial-cascader-column');
            const option = tutorial.querySelector('.tutorial-cascader-option[data-tutorial-page="home"]');
                return {
                    theme: document.documentElement.getAttribute('data-theme'),
                    triggerColor: getComputedStyle(trigger).color,
                    popupBackground: getComputedStyle(popup).backgroundColor,
                    popupBorder: getComputedStyle(popup).borderTopColor,
                    columnBorder: getComputedStyle(column).borderRightColor,
                    optionColor: getComputedStyle(option).color,
                    optionBackground: getComputedStyle(option).backgroundColor,
                    guideSectionBackground: getComputedStyle(
                        document.querySelector('#memory-guide-panel .file-list')
                    ).backgroundColor,
                    guideSectionRadius: getComputedStyle(
                        document.querySelector('#memory-guide-panel .file-list')
                    ).borderRadius,
                    importSectionBackground: getComputedStyle(
                        document.querySelector('#memory-import-panel .file-list')
                    ).backgroundColor,
                    importSectionRadius: getComputedStyle(
                        document.querySelector('#memory-import-panel .file-list')
                    ).borderRadius,
                    semanticSurface: getComputedStyle(document.documentElement)
                        .getPropertyValue('--memory-surface').trim(),
                    semanticBorder: getComputedStyle(document.documentElement)
                        .getPropertyValue('--memory-border').trim(),
                    semanticDivider: getComputedStyle(document.documentElement)
                        .getPropertyValue('--memory-divider').trim(),
                    semanticText: getComputedStyle(document.documentElement)
                        .getPropertyValue('--memory-text').trim(),
                };
        }
        """
    )

    assert colors["theme"] == "dark"
    assert colors["optionBackground"] == "rgba(0, 0, 0, 0)"
    assert colors["guideSectionBackground"] == "rgba(0, 0, 0, 0)"
    assert colors["guideSectionRadius"] == "0px"
    assert colors["importSectionBackground"] == "rgba(0, 0, 0, 0)"
    assert colors["importSectionRadius"] == "0px"
    assert colors["popupBackground"] == "rgb(31, 42, 54)"
    assert colors["popupBorder"] == "rgba(125, 211, 252, 0.28)"
    assert colors["columnBorder"] == "rgba(125, 211, 252, 0.16)"
    assert colors["semanticSurface"] == "#1f2a36"
    assert colors["semanticBorder"] == "rgba(125, 211, 252, 0.28)"
    assert colors["semanticDivider"] == "rgba(125, 211, 252, 0.16)"
    assert colors["triggerColor"] == colors["optionColor"]
    assert colors["semanticText"] == "#e6edf3"
    assert colors["triggerColor"] == "rgb(125, 211, 252)"


@pytest.mark.frontend
def test_memory_browser_dark_components_resolve_from_semantic_tokens(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script("window.localStorage.setItem('neko-dark-mode', 'true')")

    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-chat-edit .delete-btn")).to_have_count(2, timeout=10000)
    memo = mock_page.locator(".memo-textarea:not(.memo-textarea--older)")
    memo.focus()
    expect(memo).to_have_css("outline-width", "0px")
    expect(memo).to_have_css("box-shadow", "none")
    mock_page.evaluate("document.activeElement.blur()")
    styles = mock_page.evaluate(
        """
        () => {
            const resolveToken = name => {
                const probe = document.createElement('span');
                probe.style.color = `var(${name})`;
                document.body.appendChild(probe);
                const value = getComputedStyle(probe).color;
                probe.remove();
                return value;
            };
            const read = (selector, property) => getComputedStyle(
                document.querySelector(selector)
            )[property];
            return {
                scopedBody: document.body.classList.contains('memory-browser-page'),
                tokens: {
                    sidebar: resolveToken('--memory-sidebar'),
                    surface: resolveToken('--memory-surface'),
                    surfaceSubtle: resolveToken('--memory-surface-subtle'),
                    controlRadius: getComputedStyle(document.documentElement)
                        .getPropertyValue('--memory-radius-control').trim(),
                    text: resolveToken('--memory-text'),
                    secondary: resolveToken('--memory-text-secondary'),
                    tertiary: resolveToken('--memory-text-tertiary'),
                    border: resolveToken('--memory-border'),
                    accent: resolveToken('--memory-accent'),
                    accentBrand: resolveToken('--memory-accent-brand'),
                    accentForeground: resolveToken('--memory-accent-foreground'),
                    onBrand: resolveToken('--memory-on-brand'),
                    danger: resolveToken('--memory-danger'),
                    dangerSoft: resolveToken('--memory-danger-soft'),
                    clearSurface: resolveToken('--memory-clear-surface'),
                },
                actual: {
                    noticeSurface: read('.tips-container', 'backgroundColor'),
                    noticeText: read('.tip-text', 'color'),
                    fileListSurface: read('.left-column > .file-list', 'backgroundColor'),
                    fileTitleText: read('#memory-role-panel-title', 'color'),
                    storageSectionSurface: read(
                        '#memory-settings-panel .storage-location-section',
                        'backgroundColor'
                    ),
                    storageSectionRadius: read(
                        '#memory-settings-panel .storage-location-section',
                        'borderRadius'
                    ),
                    editorSurface: read('.editor', 'backgroundColor'),
                    memoSurface: read('.memo-textarea:not(.memo-textarea--older)', 'backgroundColor'),
                    memoText: read('.memo-textarea:not(.memo-textarea--older)', 'color'),
                    memoBorder: read('.memo-textarea:not(.memo-textarea--older)', 'borderTopColor'),
                    memoResize: read('.memo-textarea:not(.memo-textarea--older)', 'resize'),
                    speakerText: read('.chat-speaker', 'color'),
                    timeText: read('.chat-time', 'color'),
                    saveSurface: read('.save-btn', 'backgroundColor'),
                    saveText: read('.save-btn', 'color'),
                    clearSurface: read('.clear-btn', 'backgroundColor'),
                    deleteSurface: read('.delete-btn', 'backgroundColor'),
                    reviewTitle: read('.review-toggle-title', 'color'),
                    settingsPrimaryText: read(
                        '#memory-settings-panel .storage-location-manage-btn',
                        'color'
                    ),
                    tutorialDisabledOpacity: read('#tutorial-reset-btn', 'opacity'),
                    importDisabledOpacity: read('#external-memory-import-btn', 'opacity'),
                },
            };
        }
        """
    )

    assert styles["scopedBody"] is True
    tokens = styles["tokens"]
    actual = styles["actual"]
    assert actual["noticeSurface"] == tokens["surface"]
    assert actual["noticeText"] == tokens["secondary"]
    assert actual["fileListSurface"] == tokens["sidebar"]
    assert actual["fileTitleText"] == tokens["text"]
    assert actual["storageSectionSurface"] == tokens["surfaceSubtle"]
    assert actual["storageSectionRadius"] == tokens["controlRadius"]
    assert actual["editorSurface"] == tokens["surface"]
    assert actual["memoSurface"] == tokens["surface"]
    assert actual["memoText"] == tokens["text"]
    assert actual["memoBorder"] == tokens["border"]
    assert actual["memoResize"] == "none"
    assert actual["speakerText"] == tokens["accentForeground"]
    assert actual["timeText"] == tokens["tertiary"]
    assert actual["saveSurface"] == tokens["accent"]
    assert actual["saveText"] == tokens["onBrand"]
    assert actual["clearSurface"] == tokens["clearSurface"]
    assert actual["deleteSurface"] == tokens["dangerSoft"]
    assert actual["reviewTitle"] == tokens["text"]
    assert actual["settingsPrimaryText"] == tokens["onBrand"]
    assert actual["tutorialDisabledOpacity"] == "0.74"
    assert actual["importDisabledOpacity"] == "0.74"


@pytest.mark.frontend
def test_memory_browser_contextual_delete_labels_and_quiet_panel_close(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    delete_buttons = mock_page.locator("#memory-chat-edit .delete-btn")
    expect(delete_buttons).to_have_count(2, timeout=10000)

    expect(mock_page.locator(".container-header h2")).to_have_attribute("aria-label", "记忆浏览")
    delete_labels = delete_buttons.evaluate_all(
        "buttons => buttons.map(button => button.getAttribute('aria-label'))"
    )
    assert delete_labels == [
        "删除 我 的第 1 条记忆",
        "删除 测试猫娘 的第 2 条记忆",
    ]
    delete_sizes = delete_buttons.evaluate_all(
        "buttons => buttons.map(button => ({ width: button.offsetWidth, height: button.offsetHeight }))"
    )
    assert delete_sizes == [
        {"width": 32, "height": 28},
        {"width": 32, "height": 28},
    ]

    _open_auxiliary_panel(mock_page, "settings")
    close_button = mock_page.locator("#memory-settings-panel .memory-aux-panel-close")
    expect(close_button).to_be_visible()
    expect(mock_page.locator("#memory-settings-panel")).to_be_focused()
    quiet_style = close_button.evaluate(
        "button => ({ background: getComputedStyle(button).backgroundColor, border: getComputedStyle(button).borderTopColor })"
    )
    assert quiet_style == {
        "background": "rgba(0, 0, 0, 0)",
        "border": "rgba(0, 0, 0, 0)",
    }

    close_button.hover()
    expect(close_button).to_have_css("background-color", "rgb(234, 248, 255)")
    expect(close_button).to_have_css("border-top-color", "rgb(64, 197, 241)")


@pytest.mark.frontend
def test_memory_browser_current_personality_reset_requests_home_reselect(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    request_log = []

    def handle_reselect(route):
        request_log.append({
            "url": route.request.url,
            "method": route.request.method,
        })
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "success": True,
                "state": {
                    "status": "completed",
                    "handled_at": "2026-04-29T12:00:00Z",
                    "manual_reselect_character_name": "测试猫娘",
                    "manual_reselect_requested_at": "2026-04-29T12:10:00Z",
                },
            },
        )

    mock_page.route("**/api/characters/persona-reselect-current", handle_reselect)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)
    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-page='current_personality']").click()
    with mock_page.expect_response(
        lambda r: "/api/characters/persona-reselect-current" in r.url
        and r.request.method == "POST"
        and r.status == 200
    ):
        mock_page.locator("#tutorial-reset-btn").click()

    assert request_log == [{
        "url": f"{running_server}/api/characters/persona-reselect-current",
        "method": "POST",
    }]
    _assert_tutorial_reset_notice(mock_page, "已记录当前角色的性格重选请求，请回到主页刷新后继续。")


@pytest.mark.frontend
def test_memory_browser_reset_notice_replaces_open_notice_without_pending_promise(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_function("typeof window.showTutorialResetNotice === 'function'")

    result = mock_page.evaluate(
        """
        async () => {
            const opener = document.createElement('button');
            opener.type = 'button';
            opener.textContent = 'Open notice';
            document.body.appendChild(opener);
            opener.focus();
            const first = window.showTutorialResetNotice('First message');
            const second = window.showTutorialResetNotice('Second message');
            const firstResult = await Promise.race([
                first,
                new Promise((resolve) => window.setTimeout(() => resolve('timeout'), 250)),
            ]);
            await new Promise((resolve) => window.setTimeout(resolve, 0));
            const visibleNotices = document.querySelectorAll('.tutorial-reset-notice-backdrop').length;
            const message = document.querySelector('.tutorial-reset-notice-message')?.textContent || '';
            const ok = document.querySelector('.tutorial-reset-notice-ok');
            const tabEvent = new KeyboardEvent('keydown', {
                key: 'Tab',
                bubbles: true,
                cancelable: true,
            });
            ok?.dispatchEvent(tabEvent);
            const tabStayedInDialog = tabEvent.defaultPrevented && document.activeElement === ok;
            ok?.click();
            const secondResult = await Promise.race([
                second,
                new Promise((resolve) => window.setTimeout(() => resolve('timeout'), 500)),
            ]);
            const result = {
                firstResult,
                secondResult,
                visibleNotices,
                message,
                tabStayedInDialog,
                focusRestored: document.activeElement === opener,
                remainingNotices: document.querySelectorAll('.tutorial-reset-notice-backdrop').length,
            };
            opener.remove();
            return result;
        }
        """
    )

    assert result == {
        "firstResult": False,
        "secondResult": True,
        "visibleNotices": 1,
        "message": "Second message",
        "tabStayedInDialog": True,
        "focusRestored": True,
        "remainingNotices": 0,
    }


@pytest.mark.frontend
def test_memory_browser_all_tutorial_reset_includes_avatar_guide_state(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)
    mock_page.evaluate(
        """
        () => {
            window.__tutorialResetCalls = [];
            window.AvatarFloatingGuideReset = Object.assign({}, window.AvatarFloatingGuideReset || {}, {
                resetAllAvatarFloatingGuideDays: async (options) => {
                    window.__tutorialResetCalls.push({ type: 'avatar', options });
                }
            });
            window.resetTutorialForPage = async (pageKey) => {
                window.__tutorialResetCalls.push({ type: 'legacy', pageKey });
            };
        }
        """
    )

    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-page='all']").click()
    expect(tutorial.locator(".tutorial-reset-value")).to_have_text("全部页面")
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_enabled()
    mock_page.locator("#tutorial-reset-btn").click()
    mock_page.wait_for_function("window.__tutorialResetCalls.length === 2")

    assert mock_page.evaluate("window.__tutorialResetCalls") == [
        {"type": "avatar", "options": {"source": "memory_browser_reset_all"}},
        {"type": "legacy", "pageKey": "all"},
    ]


@pytest.mark.frontend
def test_memory_browser_home_all_reset_restarts_avatar_guide_from_day_one(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)
    mock_page.evaluate(
        """
        () => {
            window.__tutorialResetCalls = [];
            window.AvatarFloatingGuideReset = Object.assign({}, window.AvatarFloatingGuideReset || {}, {
                resetAllAvatarFloatingGuideDays: async (options) => {
                    window.__tutorialResetCalls.push({ type: 'avatar', options });
                }
            });
            window.resetTutorialForPage = async (pageKey) => {
                window.__tutorialResetCalls.push({ type: 'legacy', pageKey });
            };
        }
        """
    )

    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-home-all='true']").click()
    expect(tutorial.locator(".tutorial-reset-value")).to_have_text("主页 / 全部重置")
    expect(tutorial.locator(":scope > .tutorial-cascader-popup")).to_be_hidden()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_enabled()

    mock_page.locator("#tutorial-reset-btn").click()
    mock_page.wait_for_function("window.__tutorialResetCalls.length === 1")

    assert mock_page.evaluate("window.__tutorialResetCalls") == [
        {"type": "avatar", "options": {"source": "memory_browser_reset_home_all"}},
    ]
    _assert_tutorial_reset_notice(mock_page, "已重置主页 7 天新手教程，请重新加载 Neko 后从第 1 天开始。")


@pytest.mark.frontend
def test_avatar_guide_all_reset_refreshes_first_seen_date(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)

    state = mock_page.evaluate(
        """
        async () => {
            const key = 'neko_avatar_floating_guide_v1';
            const today = (() => {
                const now = new Date();
                const year = now.getFullYear();
                const month = String(now.getMonth() + 1).padStart(2, '0');
                const day = String(now.getDate()).padStart(2, '0');
                return `${year}-${month}-${day}`;
            })();
            window.localStorage.setItem(key, JSON.stringify({
                version: 1,
                firstSeenDate: '2020-01-01',
                completedRounds: [1],
                skippedRounds: [2],
                lastAutoShownRound: 2,
                lastAutoShownDate: '2020-01-02',
            }));
            await window.AvatarFloatingGuideReset.resetAllAvatarFloatingGuideDays({
                source: 'test_reset_all',
            });
            return {
                today,
                state: JSON.parse(window.localStorage.getItem(key)),
            };
        }
        """
    )

    assert state["state"]["firstSeenDate"] == state["today"]
    assert state["state"]["completedRounds"] == []
    assert state["state"]["skippedRounds"] == []
    assert state["state"]["pendingRound"] == 1
    assert state["state"]["manualResetRound"] == 1


@pytest.mark.frontend
def test_memory_browser_home_day_reset_uses_unified_avatar_reset_only(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)
    mock_page.evaluate(
        """
        () => {
            window.__tutorialResetCalls = [];
            window.AvatarFloatingGuideReset = Object.assign({}, window.AvatarFloatingGuideReset || {}, {
                resetAvatarFloatingGuideDay: async (day, options) => {
                    window.__tutorialResetCalls.push({ type: 'avatar-day', day, options });
                }
            });
        }
        """
    )

    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-day='1']").click()
    mock_page.locator("#tutorial-reset-btn").click()

    mock_page.wait_for_function("window.__tutorialResetCalls.length === 1")
    assert mock_page.evaluate("window.__tutorialResetCalls") == [
        {"type": "avatar-day", "day": 1, "options": {"source": "memory_browser_reset_select"}},
    ]


@pytest.mark.frontend
def test_memory_browser_tutorial_cascader_localizes_home_day_labels_for_english(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script("window.localStorage.setItem('i18nextLng', 'en')")
    mock_page.route(
        "**/api/config/steam_language",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "success": True,
                "uiLanguage": None,
                "steam_language": "english",
                "i18n_language": "en",
                "ip_country": "US",
            },
        ),
    )

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "guide")
    tutorial = mock_page.locator("#tutorial-reset-cascader")
    tutorial.locator(":scope > .tutorial-cascader-trigger").wait_for(timeout=10000)
    tutorial.locator(":scope > .tutorial-cascader-trigger").click()
    expect(tutorial.locator(".tutorial-cascader-option[data-tutorial-page='home']")).to_have_text("Home")
    expect(tutorial.locator(".tutorial-cascader-option[data-tutorial-day='1']")).to_have_text("Day 1")
    tutorial.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    tutorial.locator(".tutorial-cascader-option[data-tutorial-day='1']").click()
    expect(tutorial.locator(".tutorial-reset-value")).to_have_text("Home / Day 1")


@pytest.mark.frontend
def test_memory_browser_select_file(mock_page: Page, running_server: str, seed_memory_file):
    """Test that selecting a memory file loads and renders its chat content."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    
    mock_page.goto(f"{running_server}/memory_browser")
    
    # Wait for the file list
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # Click the cat button to load the memory file
    target_cat_btn = mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘")
    expect(target_cat_btn).to_have_count(1, timeout=5000)
    target_cat_btn.first.click()
    
    # Wait for the chat content to render in the editor area
    # The chat items should appear in #memory-chat-edit
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)
    
    # Verify that chat items are displayed (we seeded 3: system, human, ai)
    chat_items = mock_page.locator("#memory-chat-edit .chat-item")
    expect(chat_items).to_have_count(3, timeout=5000)

    message_surfaces = mock_page.evaluate(
        """
        () => {
            const human = document.querySelector('.chat-item[data-role="human"]');
            const ai = document.querySelector('.chat-item[data-role="ai"]');
            const humanBubble = human.querySelector('.chat-bubble');
            const aiBubble = ai.querySelector('.chat-bubble');
            const humanContent = human.querySelector('.chat-item-content');
            const humanDelete = human.querySelector('.delete-btn-wrapper');
            const aiDelete = ai.querySelector('.delete-btn-wrapper');
            const humanStyle = getComputedStyle(human);
            const aiStyle = getComputedStyle(ai);
            const humanBubbleStyle = getComputedStyle(humanBubble);
            const aiBubbleStyle = getComputedStyle(aiBubble);
            const humanRect = human.getBoundingClientRect();
            const aiRect = ai.getBoundingClientRect();
            const contentRect = humanContent.getBoundingClientRect();
            const deleteRect = humanDelete.getBoundingClientRect();
            const aiDeleteRect = aiDelete.getBoundingClientRect();
            return {
                humanRowBackground: humanStyle.backgroundColor,
                aiRowBackground: aiStyle.backgroundColor,
                humanBubbleBackground: humanBubbleStyle.backgroundColor,
                aiBubbleBackground: aiBubbleStyle.backgroundColor,
                humanBubbleBorder: humanBubbleStyle.borderTopColor,
                humanBubblePaddingTop: parseFloat(humanBubbleStyle.paddingTop),
                aiBubblePaddingTop: parseFloat(aiBubbleStyle.paddingTop),
                deleteRightInset: Math.round((humanRect.right - deleteRect.right) * 10) / 10,
                deleteEdgesAligned: Math.abs(deleteRect.right - aiDeleteRect.right) <= 1,
                deleteClearsContent: deleteRect.left >= contentRect.right,
                rowGap: Math.round((aiRect.top - humanRect.bottom) * 10) / 10,
            };
        }
        """
    )
    assert message_surfaces["humanRowBackground"] == "rgba(0, 0, 0, 0)"
    assert message_surfaces["aiRowBackground"] == "rgba(0, 0, 0, 0)"
    assert message_surfaces["humanBubbleBackground"] != message_surfaces["aiBubbleBackground"]
    assert message_surfaces["humanBubbleBackground"] != "rgba(0, 0, 0, 0)"
    assert message_surfaces["aiBubbleBackground"] != "rgba(0, 0, 0, 0)"
    assert message_surfaces["humanBubbleBorder"] != "rgba(0, 0, 0, 0)"
    assert message_surfaces["humanBubblePaddingTop"] == 8
    assert message_surfaces["aiBubblePaddingTop"] == 8
    assert message_surfaces["deleteRightInset"] == 0
    assert message_surfaces["deleteEdgesAligned"] is True
    assert message_surfaces["deleteClearsContent"] is True
    assert 8 <= message_surfaces["rowGap"] <= 12
    
    # Verify the save row is now visible
    expect(mock_page.locator("#save-row")).to_be_visible()


@pytest.mark.frontend
def test_memory_browser_clear_fades_batch_without_crumpling_and_keeps_system_memo(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Clear uses a restrained stagger without collapsing rows and preserves the memo."""
    memory_data = json.loads(seed_memory_file.read_text(encoding="utf-8"))
    original_turns = memory_data[1:]
    for _ in range(3):
        memory_data.extend(json.loads(json.dumps(original_turns)))
    atomic_write_json(seed_memory_file, memory_data, ensure_ascii=False, indent=2)
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(9)
    actions_before = mock_page.locator(".actions-right").bounding_box()
    assert actions_before is not None

    result = _run_and_observe_row_exit(mock_page, "#clear-memory-btn", 1)

    assert result["sawLeaving"] is True
    assert result["sawParticleCanvas"] is False
    assert result["sawClearDisabled"] is False
    assert result["sawClearOpacityChange"] is False
    assert result["sawDeleteDisabled"] is True
    assert result["elapsed"] <= 560
    assert result["scheduledDelays"]
    assert result["completionTransitions"] == 1
    assert abs(result["finalEditClientWidth"] - result["initialEditClientWidth"]) <= 0.5
    assert len(result["batchHeightSamples"]) >= 4
    assert max(result["batchHeightSamples"]) - min(result["batchHeightSamples"]) <= 1
    exit_options = [option for option in result["animationOptions"] if option["duration"] == 160]
    assert len(exit_options) == 8
    exit_delays = [option["delay"] for option in exit_options]
    assert exit_delays == sorted(exit_delays)
    assert len(set(exit_delays)) == len(exit_delays)
    assert exit_delays[0] == 0
    assert exit_delays[-1] == 280
    assert all(
        option["keyframes"][-1]["transform"] == "translateY(-10px)"
        for option in exit_options
    )
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(1)
    expect(mock_page.locator("#memory-chat-edit .memo-textarea").first).to_have_value("这是测试备忘录内容。")
    status = mock_page.locator("#save-status")
    expect(status).to_contain_text("已清空对话记录，备忘录已保留")
    expect(status).to_have_class(re.compile(r"\bis-info\b"))
    expect(status).to_have_class(re.compile(r"\bis-visible\b"))
    expect(status).to_have_attribute("aria-live", "polite")
    unsaved = mock_page.locator("#memory-unsaved-status")
    expect(unsaved).to_be_visible()
    expect(unsaved).to_contain_text("未保存")
    expect(mock_page.locator("#save-memory-btn")).to_have_attribute(
        "aria-describedby",
        "memory-unsaved-status",
    )
    actions_after = mock_page.locator(".actions-right").bounding_box()
    assert actions_after is not None
    assert abs(actions_after["x"] - actions_before["x"]) <= 0.5
    expect(status).to_be_empty(timeout=5000)
    expect(status).to_be_hidden()
    expect(unsaved).to_be_visible()

    with mock_page.expect_response(
        lambda response: "/api/memory/recent_file/save" in response.url
        and response.request.method == "POST"
        and response.status == 200
    ):
        mock_page.locator("#save-memory-btn").click()
    expect(unsaved).to_be_hidden()
    expect(mock_page.locator("#save-memory-btn")).not_to_have_attribute(
        "aria-describedby",
        "memory-unsaved-status",
    )
    expect(status).to_have_class(re.compile(r"\bis-success\b"))


@pytest.mark.frontend
def test_memory_browser_command_buttons_use_consistent_desktop_styles(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=["recent_测试猫娘.json", "recent_备用猫娘.json"],
        current_catgirl="测试猫娘",
    )
    mock_page.goto(f"{running_server}/memory_browser")
    expect(
        mock_page.locator("#memory-file-list .cat-btn", has_text="测试猫娘")
    ).to_have_attribute("aria-current", "true", timeout=10000)

    editor_actions = mock_page.evaluate(
        """
        () => {
            const read = selector => {
                const element = document.querySelector(selector);
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return {
                    width: rect.width,
                    height: rect.height,
                    radius: style.borderRadius,
                    background: style.backgroundColor,
                    backgroundImage: style.backgroundImage,
                    color: style.color,
                    fontSize: style.fontSize,
                    fontWeight: style.fontWeight,
                    transform: style.transform,
                    transitionProperty: style.transitionProperty,
                    whiteSpace: style.whiteSpace,
                };
            };
            return {
                clear: read('#clear-memory-btn'),
                save: read('#save-memory-btn'),
            };
        }
        """
    )
    assert editor_actions["clear"] == {
        "width": 88,
        "height": 40,
        "radius": "8px",
        "background": "rgba(255, 82, 82, 0.06)",
        "backgroundImage": "none",
        "color": "rgb(255, 82, 82)",
        "fontSize": "14px",
        "fontWeight": "650",
        "transform": "none",
        "transitionProperty": "background-color, border-color, box-shadow, color",
        "whiteSpace": "nowrap",
    }
    assert editor_actions["save"]["width"] == 88
    assert editor_actions["save"]["height"] == 40
    assert editor_actions["save"]["radius"] == "8px"
    assert editor_actions["save"]["fontSize"] == "14px"
    assert editor_actions["save"]["fontWeight"] == "650"
    assert editor_actions["save"]["transform"] == "none"
    assert editor_actions["save"]["whiteSpace"] == "nowrap"
    assert editor_actions["save"]["background"] == "rgb(64, 197, 241)"
    assert editor_actions["save"]["backgroundImage"] == "none"
    assert editor_actions["save"]["color"] == "rgb(255, 255, 255)"

    mock_page.locator("#clear-memory-btn").click()
    mock_page.locator("#memory-file-list .cat-btn", has_text="备用猫娘").click()
    dialog = mock_page.locator("#memory-unsaved-switch-dialog")
    expect(dialog).to_be_visible()
    dialog_styles = mock_page.evaluate(
        """
        () => {
            const dialog = document.getElementById('memory-unsaved-switch-dialog');
            const actions = dialog.querySelector('.memory-unsaved-switch-actions');
            const read = id => {
                const element = document.getElementById(id);
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return {
                    width: rect.width,
                    height: rect.height,
                    radius: style.borderRadius,
                    background: style.backgroundColor,
                    backgroundImage: style.backgroundImage,
                    borderColor: style.borderTopColor,
                    color: style.color,
                    transform: style.transform,
                    whiteSpace: style.whiteSpace,
                };
            };
            return {
                dialogWidth: dialog.getBoundingClientRect().width,
                actionDividerWidth: getComputedStyle(actions).borderTopWidth,
                cancel: read('memory-unsaved-switch-cancel'),
                discard: read('memory-unsaved-switch-discard'),
                save: read('memory-unsaved-switch-save'),
            };
        }
        """
    )
    assert 400 <= dialog_styles["dialogWidth"] <= 420.5
    assert dialog_styles["actionDividerWidth"] == "1px"
    assert [
        dialog_styles[name]["height"] for name in ("cancel", "discard", "save")
    ] == pytest.approx([40, 40, 40], abs=1.2)
    assert [
        dialog_styles[name]["radius"] for name in ("cancel", "discard", "save")
    ] == ["8px", "8px", "8px"]
    assert [
        dialog_styles[name]["transform"] for name in ("cancel", "discard", "save")
    ] == ["none", "none", "none"]
    assert [
        dialog_styles[name]["whiteSpace"] for name in ("cancel", "discard", "save")
    ] == ["nowrap", "nowrap", "nowrap"]
    assert dialog_styles["cancel"]["width"] == pytest.approx(88, abs=1.2)
    assert dialog_styles["cancel"]["color"] == "rgb(20, 126, 166)"
    assert dialog_styles["discard"]["width"] == pytest.approx(104, abs=1.2)
    assert dialog_styles["discard"]["background"] == "rgba(255, 82, 82, 0.06)"
    assert dialog_styles["discard"]["borderColor"] == "rgba(255, 82, 82, 0.34)"
    assert dialog_styles["discard"]["color"] == "rgb(255, 82, 82)"
    assert dialog_styles["save"]["width"] == pytest.approx(116, abs=1.2)
    assert dialog_styles["save"]["background"] == "rgb(64, 197, 241)"
    assert dialog_styles["save"]["backgroundImage"] == "none"
    assert dialog_styles["save"]["color"] == "rgb(255, 255, 255)"


@pytest.mark.frontend
def test_memory_browser_unsaved_close_dialog_guards_cancel_discard_and_save(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")).to_have_count(
        1,
        timeout=10000,
    )
    mock_page.evaluate(
        """
        () => {
            window.__memoryCloseAttempts = 0;
            const recordClose = () => { window.__memoryCloseAttempts += 1; };
            window.close = recordClose;
            window.history.back = recordClose;
        }
        """
    )

    mock_page.locator("#clear-memory-btn").click()
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    close_button = mock_page.locator(".close-page-btn")
    close_button.click()

    dialog = mock_page.locator("#memory-unsaved-switch-dialog")
    expect(dialog).to_be_visible()
    expect(dialog.locator("#memory-unsaved-switch-title")).to_have_text("关闭记忆整理？")
    expect(dialog.locator("#memory-unsaved-switch-message")).to_have_text(
        "测试猫娘 的修改尚未保存"
    )
    expect(dialog.locator("#memory-unsaved-switch-save")).to_have_text("保存并关闭")
    expect(mock_page.locator("#memory-unsaved-switch-cancel")).to_be_focused()

    mock_page.locator("#memory-unsaved-switch-cancel").click()
    expect(dialog).to_be_hidden()
    expect(close_button).to_be_focused()
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    assert mock_page.evaluate("window.__memoryCloseAttempts") == 0

    close_button.click()
    expect(dialog).to_be_visible()
    mock_page.locator("#memory-unsaved-switch-discard").click()
    expect(dialog).to_be_hidden()
    assert mock_page.evaluate("window.__memoryCloseAttempts") == 1
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    discard_unload = mock_page.evaluate(
        """
        () => {
            const event = new Event('beforeunload', { cancelable: true });
            window.dispatchEvent(event);
            return event.defaultPrevented;
        }
        """
    )
    assert discard_unload is False

    close_button.click()
    expect(dialog).to_be_visible()
    with mock_page.expect_response(
        lambda response: "/api/memory/recent_file/save" in response.url
        and response.request.method == "POST"
        and response.status == 200
    ):
        mock_page.locator("#memory-unsaved-switch-save").click()
    expect(dialog).to_be_hidden(timeout=5000)
    expect(mock_page.locator("#memory-unsaved-status")).to_be_hidden()
    assert mock_page.evaluate("window.__memoryCloseAttempts") == 2


@pytest.mark.frontend
def test_memory_browser_unsaved_role_switch_dialog_guards_discard_and_save(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=["recent_测试猫娘.json", "recent_备用猫娘.json"],
        current_catgirl="测试猫娘",
    )
    save_requests: list[dict] = []
    mock_page.on(
        "request",
        lambda request: save_requests.append(json.loads(request.post_data or "{}"))
        if "/api/memory/recent_file/save" in request.url
        else None,
    )
    mock_page.goto(f"{running_server}/memory_browser")

    current = mock_page.locator("#memory-file-list .cat-btn", has_text="测试猫娘")
    target = mock_page.locator("#memory-file-list .cat-btn", has_text="备用猫娘")
    expect(current).to_have_attribute("aria-current", "true", timeout=10000)
    expect(target).to_have_attribute("aria-current", "false")

    mock_page.locator("#clear-memory-btn").click()
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(1, timeout=3000)
    target.click()

    dialog = mock_page.locator("#memory-unsaved-switch-dialog")
    expect(dialog).to_be_visible()
    expect(dialog).to_have_attribute("role", "alertdialog")
    expect(dialog).to_have_attribute("aria-modal", "true")
    expect(dialog.locator("#memory-unsaved-switch-title")).to_have_text("切换角色？")
    expect(dialog.locator("#memory-unsaved-switch-message")).to_have_text(
        "测试猫娘 的修改尚未保存"
    )
    expect(target).to_have_class(re.compile(r"\bis-switch-target\b"))
    expect(current).to_have_attribute("aria-current", "true")
    expect(target).to_have_attribute("aria-current", "false")
    expect(mock_page.locator("#memory-unsaved-switch-cancel")).to_be_focused()

    geometry = mock_page.evaluate(
        """
        () => {
            const target = Array.from(document.querySelectorAll('.cat-btn'))
                .find(button => button.textContent.includes('备用猫娘'));
            const dialog = document.getElementById('memory-unsaved-switch-dialog');
            const editor = document.querySelector('.editor');
            const targetRect = target.getBoundingClientRect();
            const dialogRect = dialog.getBoundingClientRect();
            const scrim = getComputedStyle(editor, '::after');
            return {
                anchoredRight: dialogRect.left >= targetRect.right + 8,
                nudgedTowardEditor: dialogRect.left >= targetRect.right + 40,
                alignedToTarget: Math.abs(dialogRect.top - (targetRect.top - 8)) <= 1,
                insideViewport: dialogRect.right <= innerWidth - 12
                    && dialogRect.bottom <= innerHeight - 12,
                editorDimmed: scrim.content !== 'none'
                    && scrim.backgroundColor !== 'rgba(0, 0, 0, 0)',
                bodyLocked: document.body.classList.contains('is-memory-switch-confirming'),
            };
        }
        """
    )
    assert geometry == {
        "anchoredRight": True,
        "nudgedTowardEditor": True,
        "alignedToTarget": True,
        "insideViewport": True,
        "editorDimmed": True,
        "bodyLocked": True,
    }

    mock_page.locator("#memory-unsaved-switch-blocker").dispatch_event("click")
    expect(dialog).to_be_visible()
    mock_page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(target).not_to_have_class(re.compile(r"\bis-switch-target\b"))
    expect(target).to_be_focused()
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    expect(current).to_have_attribute("aria-current", "true")

    target.click()
    expect(dialog).to_be_visible()
    mock_page.locator("#memory-unsaved-switch-discard").click()
    expect(dialog).to_be_hidden()
    expect(target).to_have_attribute("aria-current", "true", timeout=5000)
    expect(mock_page.locator("#memory-unsaved-status")).to_be_hidden()

    memo = mock_page.locator("#memory-chat-edit .memo-textarea").first
    expect(memo).to_be_visible(timeout=5000)
    memo.fill("备用猫娘的新备忘录")
    memo.press("Tab")
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    current.click()
    expect(dialog).to_be_visible()
    with mock_page.expect_response(
        lambda response: "/api/memory/recent_file/save" in response.url
        and response.status == 200
    ):
        mock_page.locator("#memory-unsaved-switch-save").dispatch_event("click")
    expect(dialog).to_be_hidden(timeout=5000)
    expect(current).to_have_attribute("aria-current", "true", timeout=5000)
    assert save_requests[-1]["filename"] == "recent_备用猫娘.json"


@pytest.mark.frontend
def test_memory_browser_unsaved_switch_dialog_keeps_compact_role_panel_open_on_cancel(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=["recent_测试猫娘.json", "recent_备用猫娘.json"],
        current_catgirl="测试猫娘",
    )
    mock_page.set_viewport_size({"width": 839, "height": 600})
    mock_page.goto(f"{running_server}/memory_browser")
    expect(
        mock_page.locator("#memory-file-list .cat-btn", has_text="测试猫娘")
    ).to_have_attribute("aria-current", "true", timeout=10000)

    trigger = mock_page.locator("#memory-role-panel-trigger")
    trigger.click()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(mock_page.locator("#memory-role-panel")).to_be_visible()
    mock_page.locator("#memory-chat-edit .memo-textarea").first.fill("尚未保存")
    mock_page.locator("#memory-file-list .cat-btn", has_text="备用猫娘").click()
    expect(mock_page.locator("#memory-unsaved-switch-dialog")).to_be_visible()

    mock_page.locator("#memory-unsaved-switch-cancel").click()
    expect(mock_page.locator("#memory-unsaved-switch-dialog")).to_be_hidden()
    expect(trigger).to_have_attribute("aria-expanded", "true")
    expect(mock_page.locator("#memory-role-panel")).to_be_visible()


@pytest.mark.frontend
def test_memory_browser_unsaved_switch_dialog_stays_open_when_save_fails(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=["recent_测试猫娘.json", "recent_备用猫娘.json"],
        current_catgirl="测试猫娘",
    )
    mock_page.unroute("**/api/memory/recent_file/save")
    mock_page.route(
        "**/api/memory/recent_file/save",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={"success": False, "error": "测试失败"},
        ),
    )
    mock_page.goto(f"{running_server}/memory_browser")
    current = mock_page.locator("#memory-file-list .cat-btn", has_text="测试猫娘")
    target = mock_page.locator("#memory-file-list .cat-btn", has_text="备用猫娘")
    expect(current).to_have_attribute("aria-current", "true", timeout=10000)

    memo = mock_page.locator("#memory-chat-edit .memo-textarea").first
    memo.fill("无法保存的修改")
    memo.press("Tab")
    target.click()
    dialog = mock_page.locator("#memory-unsaved-switch-dialog")
    expect(dialog).to_be_visible()
    mock_page.locator("#memory-unsaved-switch-save").dispatch_event("click")

    expect(dialog).to_be_visible()
    expect(dialog).to_have_attribute("aria-busy", "false")
    expect(dialog).to_have_class(re.compile(r"\bis-error\b"))
    expect(dialog.locator("#memory-unsaved-switch-message")).to_contain_text(
        "保存失败"
    )
    expect(dialog.locator("button:disabled")).to_have_count(0)
    expect(mock_page.locator("#memory-unsaved-switch-save")).to_be_focused()
    expect(current).to_have_attribute("aria-current", "true")
    expect(target).to_have_attribute("aria-current", "false")
    expect(mock_page.locator("#memory-unsaved-status")).to_be_visible()
    expect(mock_page.locator("#save-status")).to_have_attribute("role", "alert")


@pytest.mark.frontend
def test_memory_browser_unsaved_switch_dialog_uses_dark_theme_tokens(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=["recent_测试猫娘.json", "recent_备用猫娘.json"],
        current_catgirl="测试猫娘",
    )
    mock_page.add_init_script("localStorage.setItem('neko-dark-mode', 'true')")
    mock_page.goto(f"{running_server}/memory_browser")
    expect(
        mock_page.locator("#memory-file-list .cat-btn", has_text="测试猫娘")
    ).to_have_attribute("aria-current", "true", timeout=10000)

    mock_page.locator("#memory-chat-edit .delete-btn").first.click()
    mock_page.locator("#memory-file-list .cat-btn", has_text="备用猫娘").click()
    dialog = mock_page.locator("#memory-unsaved-switch-dialog")
    expect(dialog).to_be_visible()
    colors = mock_page.evaluate(
        """
        () => {
            const dialog = document.getElementById('memory-unsaved-switch-dialog');
            const message = document.getElementById('memory-unsaved-switch-message');
            const editor = document.querySelector('.editor');
            return {
                theme: document.documentElement.dataset.theme,
                dialogBackground: getComputedStyle(dialog).backgroundColor,
                dialogText: getComputedStyle(dialog).color,
                messageText: getComputedStyle(message).color,
                editorScrim: getComputedStyle(editor, '::after').backgroundColor,
            };
        }
        """
    )
    assert colors == {
        "theme": "dark",
        "dialogBackground": "rgb(31, 42, 54)",
        "dialogText": "rgb(230, 237, 243)",
        "messageText": "rgb(203, 213, 225)",
        "editorScrim": "rgba(7, 12, 18, 0.58)",
    }


@pytest.mark.frontend
def test_memory_browser_pagehide_commits_pending_row_exit_for_bfcache_restore(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.goto(f"{running_server}/memory_browser")
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(3, timeout=10000)

    state = mock_page.evaluate(
        """
        () => {
            document.getElementById('clear-memory-btn').click();
            window.dispatchEvent(new PageTransitionEvent('pagehide', { persisted: true }));
            window.dispatchEvent(new PageTransitionEvent('pageshow', { persisted: true }));
            const rows = Array.from(document.querySelectorAll('#memory-chat-edit .chat-item'));
            return {
                rowCount: rows.length,
                pendingRows: rows.filter(row => (
                    row.classList.contains('is-exit-ready')
                    || row.classList.contains('is-leaving')
                    || row.classList.contains('is-reflowing')
                )).length,
                pointerBlockedRows: rows.filter(
                    row => getComputedStyle(row).pointerEvents === 'none'
                ).length,
                clearDisabled: document.getElementById('clear-memory-btn').disabled,
            };
        }
        """
    )
    assert state == {
        "rowCount": 1,
        "pendingRows": 0,
        "pointerBlockedRows": 0,
        "clearDisabled": False,
    }


@pytest.mark.frontend
def test_memory_browser_delete_single_chat_item_uses_flip_reflow(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Per-row delete exits intact, then smoothly moves the surviving row into place."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .delete-btn", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(3)

    result = _run_and_observe_row_exit(mock_page, "#memory-chat-edit .delete-btn", 2)

    assert result["sawLeaving"] is True
    assert result["sawReflowing"] is True
    assert result["sawParticleCanvas"] is False
    assert result["elapsed"] <= 520
    assert result["scheduledDelays"]
    assert result["completionTransitions"] == 1
    assert any(option["duration"] == 160 for option in result["animationOptions"])
    assert any(option["duration"] == 240 for option in result["animationOptions"])
    assert len(result["followingRowTops"]) >= 4
    assert all(
        current <= previous + 0.5
        for previous, current in zip(
            result["followingRowTops"],
            result["followingRowTops"][1:],
        )
    )
    assert abs(result["followingRowTops"][-1] - result["followingRowTops"][-2]) <= 3
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(2)
    expect(mock_page.locator("#memory-chat-edit")).not_to_contain_text("你好，测试猫娘！")
    expect(mock_page.locator("#memory-chat-edit")).to_contain_text("你好主人！我是测试猫娘喵~")
    delete_image = mock_page.locator("#memory-chat-edit .delete-btn img")
    expect(delete_image).to_have_count(1)
    expect(delete_image).to_have_attribute("src", "/static/icons/delete.png")


@pytest.mark.frontend
def test_memory_browser_delete_respects_reduced_motion_synchronously(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Reduced motion skips every visual exit state and removes synchronously."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.emulate_media(reduced_motion="reduce")

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(3)

    result = mock_page.evaluate(
        """
        async () => {
            const edit = document.getElementById('memory-chat-edit');
            const nativeAdd = DOMTokenList.prototype.add;
            let sawLeaving = false;
            const startedAt = performance.now();
            DOMTokenList.prototype.add = function (...tokens) {
                if (tokens.includes('is-leaving')) sawLeaving = true;
                return nativeAdd.apply(this, tokens);
            };
            try {
                edit.querySelector('.delete-btn').click();
                await Promise.resolve();
                const remainingRow = edit.querySelector('.chat-item');
                const remainingDelete = edit.querySelector('.delete-btn');
                const rowStyle = getComputedStyle(remainingRow);
                const deleteStyle = getComputedStyle(remainingDelete);
                return {
                    elapsed: performance.now() - startedAt,
                    count: edit.querySelectorAll('.chat-item').length,
                    sawLeaving,
                    particle: !!document.getElementById('memory-particle-canvas'),
                    rowTransform: rowStyle.transform,
                    rowTransitionDuration: rowStyle.transitionDuration,
                    deleteTransform: deleteStyle.transform,
                    deleteTransitionDuration: deleteStyle.transitionDuration,
                };
            } finally {
                DOMTokenList.prototype.add = nativeAdd;
            }
        }
        """
    )

    assert result["count"] == 2
    assert result["elapsed"] < 50
    assert result["sawLeaving"] is False
    assert result["particle"] is False
    assert result["rowTransform"] == "none"
    assert set(result["rowTransitionDuration"].split(", ")) == {"0s"}
    assert result["deleteTransform"] == "none"
    assert set(result["deleteTransitionDuration"].split(", ")) == {"0s"}


@pytest.mark.frontend
def test_memory_browser_selected_role_owns_card_face_and_reclick_does_not_reselect(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Only the selected role owns art, and refresh does not replay selection state."""
    _install_ready_memory_browser_routes(
        mock_page,
        seed_memory_file,
        recent_files=["recent_测试猫娘.json", "recent_备用猫娘.json"],
        current_catgirl="测试猫娘",
    )
    one_pixel_png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        b"\x00\x00\x00\rIDAT\x08\xd7c\xf8\xcf\xc0\xf0\x1f\x00\x05"
        b"\x00\x01\xff\x89\x99=\x1d\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    mock_page.route(
        "**/api/characters/catgirl/*/card-face",
        lambda route: route.fulfill(status=200, content_type="image/png", body=one_pixel_png),
    )

    mock_page.goto(f"{running_server}/memory_browser")
    selected = mock_page.locator("#memory-file-list .cat-btn[aria-current='true']")
    unselected = mock_page.locator("#memory-file-list .cat-btn[aria-current='false']")
    expect(selected).to_have_count(1, timeout=10000)
    expect(unselected).to_have_count(1)
    artwork = selected.locator("img.memory-role-card-face")
    expect(artwork).to_have_count(1)
    expect(artwork).to_be_visible()
    assert artwork.get_attribute("src") == mock_page.evaluate(
        "name => `/api/characters/catgirl/${encodeURIComponent(name)}/card-face`",
        "测试猫娘",
    )
    expect(unselected.locator("img.memory-role-card-face")).to_have_count(0)

    mock_page.evaluate(
        """
        () => {
            window.__selectedRoleAttributeMutations = [];
            const selected = document.querySelector('#memory-file-list .cat-btn[aria-current="true"]');
            window.__selectedRoleObserver = new MutationObserver(records => {
                records.forEach(record => window.__selectedRoleAttributeMutations.push(record.oldValue));
            });
            window.__selectedRoleObserver.observe(selected, {
                attributes: true,
                attributeFilter: ['aria-current'],
                attributeOldValue: true,
            });
        }
        """
    )
    with mock_page.expect_request("**/api/memory/recent_file?**"):
        selected.click()
    mock_page.wait_for_timeout(25)
    mutations = mock_page.evaluate(
        """
        () => {
            window.__selectedRoleObserver.disconnect();
            return window.__selectedRoleAttributeMutations;
        }
        """
    )
    assert mutations == []
    expect(selected.locator("img.memory-role-card-face")).to_have_count(1)


def test_memory_browser_task4_static_visual_contract() -> None:
    project_root = Path(__file__).parents[2]
    css = (project_root / "static/css/memory_browser.css").read_text(encoding="utf-8")
    dark_css = (project_root / "static/css/dark-mode.css").read_text(encoding="utf-8")
    js = (project_root / "static/js/memory_browser.js").read_text(encoding="utf-8")
    template = (project_root / "templates/memory_browser.html").read_text(encoding="utf-8")

    required_tokens = {
        "--memory-canvas",
        "--memory-sidebar",
        "--memory-surface",
        "--memory-surface-subtle",
        "--memory-surface-hover",
        "--memory-text",
        "--memory-text-secondary",
        "--memory-text-tertiary",
        "--memory-scrollbar-thumb",
        "--memory-scrollbar-thumb-hover",
        "--memory-divider",
        "--memory-border",
        "--memory-accent",
        "--memory-accent-brand",
        "--memory-accent-hover",
        "--memory-on-accent",
        "--memory-on-brand",
        "--memory-accent-soft",
        "--memory-accent-soft-hover",
        "--memory-focus",
        "--memory-success",
        "--memory-warning",
        "--memory-danger",
        "--memory-danger-hover",
        "--memory-danger-soft",
        "--memory-motion-instant",
        "--memory-motion-fast",
        "--memory-motion-panel",
    }
    assert required_tokens <= set(re.findall(r"--[a-z0-9-]+(?=\s*:)", css))
    assert "transition: all" not in css
    assert "memory-particle-canvas" not in css
    assert "memory-particle-canvas" not in js
    assert "blur(" not in css
    assert "cubic-bezier(0.34, 1.56" not in css
    row_exit_duration = int(re.search(r"MEMORY_ROW_EXIT_MS\s*=\s*(\d+)", js).group(1))
    assert 140 <= row_exit_duration <= 180
    memory_dark_section = dark_css.split("Memory Browser ", 1)[1].split("当前角色", 1)[0]
    assert "body.memory-browser-page" in memory_dark_section
    assert re.search(r"#[0-9a-fA-F]{3,8}\b", memory_dark_section) is None

    ids = re.findall(r'\bid="([^"]+)"', template)
    assert len(ids) == len(set(ids))
    for asset in (
        "static/icons/delete.png",
        "static/icons/exclamation.png",
        "static/icons/close_button.png",
    ):
        assert (project_root / asset).is_file()


_BODY_SENTENCE = "博士正在和小猫娘一起挖铁矿，刚找到一批可以做铁镐。"
_OLDER_SENTENCE = "几天前两人养过一株窗台幼苗，并烤了草莓蛋糕。"


def _write_memo_seed(clean_user_data_dir, divider: str):
    """种子一个 recent.json，memo body + 指定形态的 `---` 分隔符 + older。

    `divider` 是 body 与 older 之间的完整分隔片段（包含前后换行），让用例
    覆盖 LLM 实际可能漂移的几种间距（漏空行 / 多空行 / 多个连字符）。"""
    app_root = Path(clean_user_data_dir) / "N.E.K.O"
    save_storage_policy(
        None,
        selected_root=app_root,
        anchor_root=app_root,
        selection_source="test",
    )

    memory_dir = app_root / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)
    catgirl_dir = memory_dir / "测试猫娘"
    catgirl_dir.mkdir(parents=True, exist_ok=True)

    memo_text = f"先前对话的备忘录: {_BODY_SENTENCE}{divider}{_OLDER_SENTENCE}"
    test_data = [
        {
            "type": "system",
            "data": {
                "content": memo_text,
                "additional_kwargs": {},
                "response_metadata": {},
                "type": "system",
                "name": None,
                "id": None,
                "example": False,
            },
        }
    ]

    memory_file = catgirl_dir / "recent.json"
    atomic_write_json(memory_file, test_data, ensure_ascii=False, indent=2)
    return memory_file


@pytest.fixture
def seed_memory_file_with_older_divider(clean_user_data_dir, running_server):
    """种子文件：memo 用规范 `\\n\\n---\\n\\n` 分界（LLM 严格遵守 prompt 的形态）。"""
    return _write_memo_seed(clean_user_data_dir, "\n\n---\n\n")


@pytest.mark.frontend
def test_memory_browser_renders_older_section_when_divider_present(
    mock_page: Page,
    running_server: str,
    seed_memory_file_with_older_divider,
):
    """memo 含 `\\n\\n---\\n\\n` 时，前端必须把"较久前"段拆成独立 textarea 渲染，
    并出现一个 `memo-older-label` 提示。

    这是 SUMMARY_STALE_HINT 硬分隔约定的落地点——LLM 输出端 + 前端识别端
    之间的契约就靠这条端到端测。
    """
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file_with_older_divider)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘").first.click()
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)

    # 主体 textarea：不包含 `---`，也不包含尾段文本
    body_ta = mock_page.locator(".memo-textarea:not(.memo-textarea--older)")
    expect(body_ta).to_have_count(1, timeout=5000)
    body_value = body_ta.input_value()
    assert "正在和小猫娘一起挖铁矿" in body_value
    assert "---" not in body_value, "主体段不应含分隔符——已被 splitter 切掉"
    assert "草莓蛋糕" not in body_value, "尾段文本应只出现在 older textarea"

    # 较久前 label + 独立 textarea
    older_label = mock_page.locator(".memo-older-label")
    expect(older_label).to_have_count(1, timeout=5000)
    older_ta = mock_page.locator(".memo-textarea--older")
    expect(older_ta).to_have_count(1, timeout=5000)
    older_value = older_ta.input_value()
    assert "草莓蛋糕" in older_value
    assert "正在和小猫娘一起挖铁矿" not in older_value


@pytest.mark.frontend
@pytest.mark.parametrize(
    "divider",
    [
        pytest.param("\n---\n", id="single_newline_each_side"),
        pytest.param("\n\n---\n", id="blank_before_only"),
        pytest.param("\n---\n\n", id="blank_after_only"),
        pytest.param("\n\n\n---\n\n", id="extra_blank_before"),
        pytest.param("\n\n----\n\n", id="four_dashes"),
        pytest.param("\n\n-----\n\n", id="five_dashes"),
    ],
)
def test_memory_browser_splits_non_canonical_divider_spacing(
    mock_page: Page,
    running_server: str,
    clean_user_data_dir,
    divider,
):
    """LLM 实际输出经常漂移：漏空行、多空行、多输连字符——splitter 都得切得开。

    Regression for codex review on PR #1358 catching that the original regex
    强制要求 `---` 前后各一行空行，少一行就识别不到，导致尾段还是塞回 body
    textarea。修后正则只要求 `---` 单独成行（前后至少各一个换行）。"""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    memory_file = _write_memo_seed(clean_user_data_dir, divider)
    _install_ready_memory_browser_routes(mock_page, memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘").first.click()
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)

    older_ta = mock_page.locator(".memo-textarea--older")
    expect(older_ta).to_have_count(1, timeout=5000)
    body_ta = mock_page.locator(".memo-textarea:not(.memo-textarea--older)")
    body_value = body_ta.input_value()
    assert _BODY_SENTENCE in body_value
    # 收紧到只查"≥3 连字符"的分隔符形态——单/双连字符在正文里可能合法
    # （日期、复合词），不该被这条断言误伤。
    assert "---" not in body_value, "body 不应残留分隔符"
    assert _OLDER_SENTENCE in older_ta.input_value()


@pytest.mark.frontend
def test_memory_browser_saves_memo_with_divider_roundtrip(
    mock_page: Page,
    running_server: str,
    seed_memory_file_with_older_divider,
):
    """编辑任一 textarea 后保存，发往后端的 payload 必须重新拼回 `\\n\\n---\\n\\n`
    规范形式——不能漏掉尾段、也不能改用别的分隔符。"""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))

    saved_payloads: list[dict] = []

    app_root = seed_memory_file_with_older_divider.parents[2]

    def handle_bootstrap(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "current_root": str(app_root),
                "recommended_root": str(app_root),
                "legacy_sources": [],
                "selection_required": False,
                "migration_pending": False,
                "recovery_required": False,
                "blocking_reason": "",
            },
        )

    def handle_recent_files(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"files": ["recent_测试猫娘.json"]},
        )

    def handle_current_catgirl(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"current_catgirl": "测试猫娘"},
        )

    def handle_recent_file(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"content": seed_memory_file_with_older_divider.read_text(encoding="utf-8")},
        )

    def handle_review_config(route):
        route.fulfill(status=200, content_type="application/json", json={"enabled": True})

    def handle_save(route):
        saved_payloads.append(_request_json(route))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"success": True, "need_refresh": False},
        )

    mock_page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    mock_page.route("**/api/memory/recent_files", handle_recent_files)
    mock_page.route("**/api/characters/current_catgirl", handle_current_catgirl)
    mock_page.route("**/api/memory/recent_file?**", handle_recent_file)
    mock_page.route("**/api/memory/review_config", handle_review_config)
    mock_page.route("**/api/memory/recent_file/save", handle_save)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘").first.click()
    mock_page.wait_for_selector(".memo-textarea--older", timeout=5000)

    # 改写尾段并 commit（textarea 的 `change` 事件靠 blur 触发）
    older_ta = mock_page.locator(".memo-textarea--older").first
    older_ta.fill("几天前的旧事件——已归档。")
    # 把焦点挪走触发 change
    mock_page.locator(".memo-textarea:not(.memo-textarea--older)").first.click()

    with mock_page.expect_response(
        lambda r: "/api/memory/recent_file/save" in r.url
        and r.request.method == "POST"
        and r.status == 200
    ):
        mock_page.locator("#save-memory-btn").click()

    assert len(saved_payloads) == 1
    chat = saved_payloads[0]["chat"]
    system_msgs = [m for m in chat if m.get("role") == "system"]
    assert len(system_msgs) == 1
    saved_text = system_msgs[0]["text"]

    # 1) 仍以本地化前缀打头
    assert saved_text.startswith("先前对话的备忘录: ")
    # 2) 主体段保留
    assert "正在和小猫娘一起挖铁矿" in saved_text
    # 3) 尾段被改写
    assert "几天前的旧事件——已归档。" in saved_text
    # 4) 分隔符是规范的 `\n\n---\n\n`
    assert "\n\n---\n\n" in saved_text
    # 5) 整段里 `---` 只出现一次
    assert saved_text.count("\n---\n") == 1


@pytest.mark.frontend
def test_memory_browser_preserves_leading_indent_in_older_section(
    mock_page: Page,
    running_server: str,
    seed_memory_file_with_older_divider,
):
    """Regression for codex review on PR #1358: composeMemo 不能把 older 段首字符
    的有意义缩进当 noise 削掉——只能削整行空白。比如用户在 older textarea 里
    手写一个嵌套列表（前导 2 空格 / tab），保存后必须 byte-for-byte 留住。"""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))

    saved_payloads: list[dict] = []
    app_root = seed_memory_file_with_older_divider.parents[2]

    def handle_bootstrap(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "current_root": str(app_root),
                "recommended_root": str(app_root),
                "legacy_sources": [],
                "selection_required": False,
                "migration_pending": False,
                "recovery_required": False,
                "blocking_reason": "",
            },
        )

    def handle_recent_files(route):
        route.fulfill(status=200, content_type="application/json", json={"files": ["recent_测试猫娘.json"]})

    def handle_current_catgirl(route):
        route.fulfill(status=200, content_type="application/json", json={"current_catgirl": "测试猫娘"})

    def handle_recent_file(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"content": seed_memory_file_with_older_divider.read_text(encoding="utf-8")},
        )

    def handle_review_config(route):
        route.fulfill(status=200, content_type="application/json", json={"enabled": True})

    def handle_save(route):
        saved_payloads.append(_request_json(route))
        route.fulfill(status=200, content_type="application/json", json={"success": True, "need_refresh": False})

    mock_page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    mock_page.route("**/api/memory/recent_files", handle_recent_files)
    mock_page.route("**/api/characters/current_catgirl", handle_current_catgirl)
    mock_page.route("**/api/memory/recent_file?**", handle_recent_file)
    mock_page.route("**/api/memory/review_config", handle_review_config)
    mock_page.route("**/api/memory/recent_file/save", handle_save)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#memory-file-list button.cat-btn", has_text="测试猫娘").first.click()
    mock_page.wait_for_selector(".memo-textarea--older", timeout=5000)

    # 写一段首字符就有 2 空格缩进 + 后续行 4 空格缩进的内容（模拟嵌套列表）
    indented_older = "  顶层条目一\n    子条目 a\n    子条目 b"
    older_ta = mock_page.locator(".memo-textarea--older").first
    older_ta.fill(indented_older)
    mock_page.locator(".memo-textarea:not(.memo-textarea--older)").first.click()

    with mock_page.expect_response(
        lambda r: "/api/memory/recent_file/save" in r.url
        and r.request.method == "POST"
        and r.status == 200
    ):
        mock_page.locator("#save-memory-btn").click()

    assert len(saved_payloads) == 1
    saved_text = next(
        m["text"] for m in saved_payloads[0]["chat"] if m.get("role") == "system"
    )

    # 关键断言：分隔符之后立刻是 `  顶层条目一`，前导 2 空格没有被吃掉
    assert "\n\n---\n\n  顶层条目一\n    子条目 a\n    子条目 b" in saved_text, (
        f"older 段前导缩进被吞掉了，实际保存：{saved_text!r}"
    )


@pytest.mark.frontend
def test_memory_browser_auto_review_toggle(mock_page: Page, running_server: str, seed_memory_file):
    """Test that the auto-review toggle works and persists."""
    mock_page.on("console", lambda msg: print(f"Browser Console: {msg.text}"))
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    
    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    
    # Wait for the page to fully initialize
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # The auto-review checkbox should be present
    checkbox = mock_page.locator("#review-toggle-checkbox")
    expect(checkbox).to_be_attached()
    
    # Default is enabled (checked), toggle it off
    initial_state = checkbox.is_checked()
    
    # Toggle the checkbox via its label (since checkbox is styled via label)
    label = mock_page.locator("label.auto-review-toggle-btn[for='review-toggle-checkbox']")
    
    # Intercept the POST to /api/memory/review_config
    with mock_page.expect_response(
        lambda r: "/api/memory/review_config" in r.url and r.request.method == "POST" and r.status == 200
    ):
        label.click()
    
    # Verify the checkbox state toggled
    new_state = checkbox.is_checked()
    assert new_state != initial_state, "Checkbox state should have toggled"
    
    # Reload and verify the state persisted
    mock_page.reload()
    mock_page.wait_for_selector("#review-toggle-checkbox", state="attached", timeout=10000)
    expect(mock_page.locator("#review-toggle-checkbox")).to_be_checked(checked=new_state, timeout=5000)


@pytest.mark.frontend
def test_memory_browser_storage_bootstrap_blocks_memory_apis(mock_page: Page, running_server: str):
    """Storage bootstrap must run before ordinary memory APIs in limited mode."""
    requested_paths = []

    def handle_bootstrap(route):
        requested_paths.append("/api/storage/location/bootstrap")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "current_root": "/tmp/current/N.E.K.O",
                "recommended_root": "/tmp/recommended/N.E.K.O",
                "legacy_sources": [],
                "selection_required": True,
                "migration_pending": False,
                "recovery_required": False,
                "blocking_reason": "selection_required",
            },
        )

    def handle_memory_api(route):
        requested_paths.append(route.request.url)
        route.fulfill(status=500, content_type="application/json", json={"error": "memory api should not be called"})

    mock_page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    mock_page.route("**/api/memory/recent_files", handle_memory_api)
    mock_page.route("**/api/memory/review_config", handle_memory_api)

    mock_page.goto(f"{running_server}/memory_browser")

    expect(mock_page.locator("#storage-location-status")).to_contain_text("存储位置", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .memory-limited-state")).to_be_visible()
    expect(mock_page.locator("#review-toggle-checkbox")).to_be_disabled()
    settings_trigger = mock_page.locator("#memory-settings-trigger")
    expect(settings_trigger).to_be_enabled()
    settings_trigger.click()
    expect(mock_page.locator("#memory-settings-panel")).to_be_visible()
    # Recoverable storage states keep the management entry visible and enabled so
    # the user can resolve selection/recovery while ordinary initialization stays blocked.
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_visible()
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_enabled()

    assert "/api/storage/location/bootstrap" in requested_paths
    assert not any("/api/memory/recent_files" in path for path in requested_paths)
    assert not any("/api/memory/review_config" in path for path in requested_paths)


@pytest.mark.frontend
def test_memory_browser_storage_combined_restart_reports_preflight_blocking(mock_page: Page, running_server: str, seed_memory_file):
    """The combined restart button should stop after preflight when the target is blocked."""
    requested_paths = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    def handle_preflight(route):
        requested_paths.append("/api/storage/location/preflight")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/stage2-target/N.E.K.O",
                "target_root": "/tmp/stage2-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": False,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "target_not_writable",
                "blocking_error_message": "目标路径当前不可写。",
            },
        )

    def handle_forbidden_storage_mutation(route):
        requested_paths.append(route.request.url)
        route.fulfill(status=500, content_type="application/json", json={"error": "mutation should not be called"})

    mock_page.route("**/api/storage/location/preflight", handle_preflight)
    mock_page.route("**/api/storage/location/select", handle_forbidden_storage_mutation)
    mock_page.route("**/api/storage/location/restart", handle_forbidden_storage_mutation)

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    mock_page.locator("#storage-location-manage-btn").click()
    expect(mock_page.locator("#storage-location-modal")).to_be_visible()
    storage_pick = mock_page.locator("#storage-location-pick-btn")
    expect(storage_pick).to_have_css("color", "rgb(64, 197, 241)")
    mock_page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    expect(storage_pick).to_have_css("color", "rgb(125, 211, 252)")
    mock_page.evaluate("document.documentElement.removeAttribute('data-theme')")
    mock_page.locator("#storage-target-root-input").fill("/tmp/stage2-target")
    expect(mock_page.locator("#storage-location-preflight-btn")).to_have_count(0)

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        mock_page.locator("#storage-location-restart-btn").click()

    expect(mock_page.locator("#storage-location-preflight-result")).to_contain_text("目标路径当前不可写", timeout=5000)
    expect(mock_page.locator("#storage-location-restart-btn")).to_be_enabled()
    assert requested_paths == ["/api/storage/location/preflight"]


@pytest.mark.frontend
def test_memory_browser_storage_picker_preflights_selected_directory(mock_page: Page, running_server: str, seed_memory_file):
    """Directory picker selection should flow into preflight without generic failure."""
    requests = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.add_init_script(
        """
        window.nekoHost = {
            pickDirectory: async (options) => {
                window.__storagePickOptions = options;
                return { cancelled: false, selected_root: '/tmp/picked-storage' };
            }
        };
        """
    )

    def handle_preflight(route):
        requests.append(_request_json(route))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/picked-storage/N.E.K.O",
                "target_root": "/tmp/picked-storage/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": False,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "target_not_writable",
                "blocking_error_message": "/tmp/picked-storage/N.E.K.O",
                "selection_source": "custom",
            },
        )

    mock_page.route("**/api/storage/location/preflight", handle_preflight)

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-location-pick-btn").click()

    expect(mock_page.locator("#storage-target-root-input")).to_have_value("/tmp/picked-storage/N.E.K.O", timeout=5000)
    pick_options = mock_page.evaluate("window.__storagePickOptions")
    start_path = pick_options["startPath"]
    app_root = str(seed_memory_file.parents[2])
    assert start_path, "startPath should not be empty"
    assert not start_path.endswith("N.E.K.O"), (
        f"picker should be opened at the parent of the current root, got {start_path!r}"
    )
    assert app_root.startswith(start_path), (
        f"startPath should be a parent of app_root; got start_path={start_path!r} app_root={app_root!r}"
    )

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        mock_page.locator("#storage-location-restart-btn").click()

    expect(mock_page.locator("#storage-location-preflight-result")).to_contain_text("/tmp/picked-storage/N.E.K.O", timeout=5000)
    assert requests == [{"selected_root": "/tmp/picked-storage/N.E.K.O", "selection_source": "custom"}]


@pytest.mark.frontend
def test_memory_browser_storage_picker_preserves_root_parent_directory(mock_page: Page, running_server: str, seed_memory_file):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.add_init_script(
        """
        window.nekoHost = {
            pickDirectory: async () => {
                return { cancelled: false, selected_root: '/' };
            }
        };
        """
    )

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-location-pick-btn").click()

    expect(mock_page.locator("#storage-target-root-input")).to_have_value("/N.E.K.O", timeout=5000)


@pytest.mark.frontend
def test_memory_browser_open_current_root_uses_host_bridge(mock_page: Page, running_server: str, seed_memory_file):
    """Opening current storage root should call the desktop host bridge when present."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.nekoHost = {
            openPath: async (payload) => {
                window.__openedStoragePath = payload.path;
                return { ok: true };
            }
        };
        """
    )

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-open-btn").click()

    opened_path = mock_page.wait_for_function("window.__openedStoragePath", timeout=5000).json_value()
    assert opened_path == str(seed_memory_file.parents[2])


@pytest.mark.frontend
def test_memory_browser_open_current_root_uses_backend_without_host_bridge(mock_page: Page, running_server: str, seed_memory_file):
    """Plain web usage should ask the backend to open the current storage root."""
    requested_paths = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    def handle_open_current(route):
        requested_paths.append("/api/storage/location/open-current")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"ok": True, "current_root": str(seed_memory_file.parents[2])},
        )

    mock_page.route("**/api/storage/location/open-current", handle_open_current)

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    with mock_page.expect_response(lambda r: "/api/storage/location/open-current" in r.url and r.status == 200):
        mock_page.locator("#storage-location-open-btn").click()

    assert requested_paths == ["/api/storage/location/open-current"]


@pytest.mark.frontend
def test_memory_browser_open_current_root_falls_back_when_host_bridge_fails(mock_page: Page, running_server: str, seed_memory_file):
    """Host bridge failures should not block the backend open-current fallback."""
    requested_paths = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.nekoHost = {
            openPath: async () => ({ ok: false, error: 'native open failed' })
        };
        """
    )

    def handle_open_current(route):
        requested_paths.append("/api/storage/location/open-current")
        route.fulfill(
            status=200,
            content_type="application/json",
            json={"ok": True, "current_root": str(seed_memory_file.parents[2])},
        )

    mock_page.route("**/api/storage/location/open-current", handle_open_current)

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    with mock_page.expect_response(lambda r: "/api/storage/location/open-current" in r.url and r.status == 200):
        mock_page.locator("#storage-location-open-btn").click()

    assert requested_paths == ["/api/storage/location/open-current"]


@pytest.mark.frontend
def test_memory_browser_storage_restart_requires_preflight_and_confirms_existing_target(mock_page: Page, running_server: str, seed_memory_file):
    """Stage 3 calls restart after preflight and carries existing-target confirmation."""
    requests = []
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.__storageRestartMessages = [];
        window.__storageRestartClosed = false;
        Object.defineProperty(window, 'opener', {
            configurable: true,
            value: {
                closed: false,
                postMessage(message, origin) {
                    window.__storageRestartMessages.push({ message, origin });
                }
            }
        });
        window.close = function () {
            window.__storageRestartClosed = true;
        };
        """
    )

    def handle_preflight(route):
        requests.append(("preflight", _request_json(route)))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/stage3-target/N.E.K.O",
                "target_root": "/tmp/stage3-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": True,
                "requires_existing_target_confirmation": True,
                "existing_target_confirmation_message": "目标路径已经包含现有数据。",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        )

    def handle_restart(route):
        requests.append(("restart", _request_json(route)))
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/stage3-target/N.E.K.O",
                "target_root": "/tmp/stage3-target/N.E.K.O",
            },
        )

    mock_page.route("**/api/storage/location/preflight", handle_preflight)
    mock_page.route("**/api/storage/location/restart", handle_restart)
    mock_page.on("dialog", lambda dialog: dialog.accept())

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-target-root-input").fill("/tmp/stage3-target")

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        with mock_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
            mock_page.locator("#storage-location-restart-btn").click()

    expect(mock_page.locator("#storage-location-preflight-result")).to_contain_text("重启", timeout=5000)
    expect(mock_page.locator("#storage-location-pick-btn")).to_be_disabled()
    expect(mock_page.locator("#storage-target-root-input")).to_be_disabled()
    expect(mock_page.locator("#storage-location-restart-btn")).to_be_hidden()
    mock_page.wait_for_function("window.__storageRestartMessages.length === 1", timeout=5000)
    assert mock_page.evaluate("window.location.pathname") == "/memory_browser"
    restart_message = mock_page.evaluate("window.__storageRestartMessages[0]")
    assert restart_message["origin"] == running_server
    assert restart_message["message"]["type"] == "storage_location_restart_initiated"
    assert restart_message["message"]["sender_id"]
    assert restart_message["message"]["payload"] == {
        "ok": True,
        "result": "restart_initiated",
        "restart_mode": "migrate_after_shutdown",
        "selected_root": "/tmp/stage3-target/N.E.K.O",
        "target_root": "/tmp/stage3-target/N.E.K.O",
    }
    assert requests[0][0] == "preflight"
    assert requests[0][1] == {
        "selected_root": "/tmp/stage3-target/N.E.K.O",
        "selection_source": "custom",
    }
    assert requests[1] == (
        "restart",
        {
            "selected_root": "/tmp/stage3-target/N.E.K.O",
            "selection_source": "custom",
            "confirm_existing_target_content": True,
        },
    )
    mock_page.wait_for_function("window.__storageRestartClosed === true", timeout=5000)


@pytest.mark.frontend
def test_memory_browser_desktop_storage_restart_uses_host_close_window(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Desktop host windows should close via the host bridge after restart is accepted."""
    storage_status_requests = {"count": 0}
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script(
        """
        window.__hostCloseWindowCalls = 0;
        window.nekoHost = {
            closeWindow: async () => {
                window.__hostCloseWindowCalls += 1;
                return { ok: true };
            }
        };
        """
    )

    mock_page.route(
        "**/api/storage/location/preflight",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/desktop-target/N.E.K.O",
                "target_root": "/tmp/desktop-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        ),
    )
    mock_page.route(
        "**/api/storage/location/restart",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/desktop-target/N.E.K.O",
                "target_root": "/tmp/desktop-target/N.E.K.O",
            },
        ),
    )

    def handle_storage_status(route):
        storage_status_requests["count"] += 1
        route.fulfill(status=500, content_type="application/json", json={"ok": False})

    mock_page.route("**/api/storage/location/status", handle_storage_status)

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-target-root-input").fill("/tmp/desktop-target")

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        with mock_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
            mock_page.locator("#storage-location-restart-btn").click()

    mock_page.wait_for_function("window.__hostCloseWindowCalls === 1", timeout=5000)
    expect(mock_page.locator("#storage-location-overlay")).to_have_count(0)
    assert storage_status_requests["count"] == 0


@pytest.mark.frontend
def test_memory_browser_storage_restart_standalone_reuses_storage_maintenance_overlay(mock_page: Page, running_server: str, seed_memory_file):
    """Standalone memory page should show the shared storage maintenance overlay after restart."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    def handle_preflight(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/standalone-target/N.E.K.O",
                "target_root": "/tmp/standalone-target/N.E.K.O",
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        )

    def handle_restart(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": "/tmp/standalone-target/N.E.K.O",
                "target_root": "/tmp/standalone-target/N.E.K.O",
                "migration": {
                    "status": "pending",
                    "target_root": "/tmp/standalone-target/N.E.K.O",
                },
            },
        )

    def handle_storage_status(route):
        route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "ready": False,
                "status": "maintenance",
                "lifecycle_state": "maintenance",
                "migration_stage": "pending",
                "maintenance_message": "正在关闭，数据会在关闭后迁移并自动重启。",
                "poll_interval_ms": 500,
                "effective_root": str(seed_memory_file.parents[2]),
                "blocking_reason": "migration_pending",
                "migration": {
                    "status": "pending",
                    "target_root": "/tmp/standalone-target/N.E.K.O",
                },
            },
        )

    mock_page.route("**/api/storage/location/preflight", handle_preflight)
    mock_page.route("**/api/storage/location/restart", handle_restart)
    mock_page.route("**/api/storage/location/status", handle_storage_status)

    mock_page.goto(f"{running_server}/memory_browser")
    _open_auxiliary_panel(mock_page, "settings")
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    mock_page.locator("#storage-location-manage-btn").click()
    mock_page.locator("#storage-target-root-input").fill("/tmp/standalone-target")

    with mock_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        with mock_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
            mock_page.locator("#storage-location-restart-btn").click()

    expect(mock_page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    expect(mock_page.locator("#storage-location-overlay")).to_be_visible(timeout=10_000)
    expect(mock_page.locator('[role="progressbar"]')).to_be_visible(timeout=10_000)
    assert mock_page.evaluate("typeof window.__nekoStorageLocationStartupBarrier") == "undefined"
    assert mock_page.evaluate("window.location.pathname") == "/memory_browser"


@pytest.mark.frontend
def test_memory_browser_web_popup_restart_drives_opener_maintenance_overlay(mock_page: Page, running_server: str, seed_memory_file):
    """A real web popup opened from the home page must hand off restart maintenance to its opener."""
    context = mock_page.context
    _install_ready_memory_browser_routes(context, seed_memory_file)

    target_root = "/tmp/web-popup-target/N.E.K.O"

    context.route(
        "**/api/system/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "status": "ready",
                "ready": True,
                "storage": {
                    "selection_required": False,
                    "migration_pending": False,
                    "recovery_required": False,
                    "blocking_reason": "",
                    "last_error_summary": "",
                    "stage": "web_popup_restart",
                },
            },
        ),
    )
    context.route(
        "**/api/storage/location/status",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "ready": False,
                "status": "maintenance",
                "lifecycle_state": "maintenance",
                "migration_stage": "pending",
                "maintenance_message": "正在关闭，数据会在关闭后迁移并自动重启。",
                "poll_interval_ms": 500,
                "effective_root": str(seed_memory_file.parents[2]),
                "blocking_reason": "migration_pending",
                "storage": {
                    "selection_required": False,
                    "migration_pending": True,
                    "recovery_required": False,
                    "blocking_reason": "migration_pending",
                    "stage": "web_popup_restart",
                },
                "migration": {
                    "status": "pending",
                    "target_root": target_root,
                },
            },
        ),
    )
    context.route(
        "**/api/storage/location/preflight",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_required",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": target_root,
                "target_root": target_root,
                "estimated_required_bytes": 1024,
                "target_free_bytes": 4096,
                "permission_ok": True,
                "warning_codes": [],
                "target_has_existing_content": False,
                "requires_existing_target_confirmation": False,
                "existing_target_confirmation_message": "",
                "blocking_error_code": "",
                "blocking_error_message": "",
                "selection_source": "custom",
            },
        ),
    )
    context.route(
        "**/api/storage/location/restart",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            json={
                "ok": True,
                "result": "restart_initiated",
                "restart_mode": "migrate_after_shutdown",
                "selected_root": target_root,
                "target_root": target_root,
                "migration": {
                    "status": "pending",
                    "target_root": target_root,
                },
            },
        ),
    )

    home_page = mock_page
    home_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    expect(home_page.locator("#storage-location-overlay")).to_be_hidden(timeout=10_000)

    with home_page.expect_popup() as popup_info:
        home_page.evaluate("() => window.open('/memory_browser', 'neko_memory')")
    memory_page = popup_info.value
    memory_page.on("console", lambda msg: print(f"Memory Popup Console: {msg.text}"))
    memory_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    assert memory_page.evaluate("window.opener !== null")

    _open_auxiliary_panel(memory_page, "settings")
    memory_page.locator("#storage-location-manage-btn").click()
    memory_page.locator("#storage-target-root-input").fill("/tmp/web-popup-target")
    with memory_page.expect_response(lambda r: "/api/storage/location/preflight" in r.url and r.status == 200):
        with memory_page.expect_response(lambda r: "/api/storage/location/restart" in r.url and r.status == 200):
            memory_page.locator("#storage-location-restart-btn").click()

    expect(home_page.get_by_role("heading", name="正在优化存储布局...")).to_be_visible(timeout=10_000)
    expect(home_page.locator("#storage-location-overlay")).to_be_visible(timeout=10_000)
    expect(home_page.locator('[role="progressbar"]')).to_be_visible(timeout=10_000)
    assert home_page.locator("body").evaluate(
        "node => node.classList.contains('storage-location-modal-open')"
    )
