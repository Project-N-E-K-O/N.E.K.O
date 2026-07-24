import pytest
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


def _install_ready_memory_browser_routes(page: Page | BrowserContext, memory_file: Path) -> None:
    """Mock storage + memory APIs so the page (or whole context) is tested in ready mode."""
    app_root = memory_file.parents[2]
    review_state = {"enabled": True}

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
            json={"content": memory_file.read_text(encoding="utf-8")},
        )

    def handle_review_config(route):
        if route.request.method == "POST":
            payload = _request_json(route)
            review_state["enabled"] = bool(payload.get("enabled"))
            route.fulfill(status=200, content_type="application/json", json={"success": True, "enabled": review_state["enabled"]})
            return
        route.fulfill(status=200, content_type="application/json", json={"enabled": review_state["enabled"]})

    def handle_save(route):
        route.fulfill(status=200, content_type="application/json", json={"success": True, "need_refresh": False})

    page.route("**/api/storage/location/bootstrap", handle_bootstrap)
    page.route("**/api/memory/recent_files", handle_recent_files)
    page.route("**/api/characters/current_catgirl", handle_current_catgirl)
    page.route("**/api/memory/recent_file?**", handle_recent_file)
    page.route("**/api/memory/review_config", handle_review_config)
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
    expect(mock_page.locator(".storage-location-section")).to_be_visible()
    expect(mock_page.locator("#storage-location-manage-btn")).to_be_enabled()
    expect(mock_page.locator("#storage-recommended-root")).to_have_count(0)
    expect(mock_page.locator("#storage-current-root")).not_to_have_text("加载中...", timeout=5000)
    expect(mock_page.locator("#storage-location-overlay")).to_have_count(0)
    expect(mock_page.locator("#tutorial-reset-select option[value='current_personality']")).to_have_count(1)
    expect(mock_page.locator("#home-tutorial-reset-controls")).to_have_count(0)
    expect(mock_page.locator(".tutorial-day-reset-menu")).to_have_count(0)
    expect(mock_page.locator(".tutorial-cascader-day-column [data-tutorial-day]")).to_have_count(7)
    expect(mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-popup")).to_be_hidden()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_disabled()
    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()
    expect(mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-popup")).to_be_visible()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    expect(mock_page.locator(".tutorial-cascader-day-column")).to_be_visible()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_disabled()
    expect(mock_page.locator(".tutorial-cascader-option[data-tutorial-home-all='true']")).to_have_count(1)
    mock_page.locator(".tutorial-cascader-option[data-tutorial-day='1']").click()
    expect(mock_page.locator(".tutorial-reset-value")).to_have_text("主页 / 第 1 天")
    expect(mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-popup")).to_be_hidden()
    expect(mock_page.locator("#tutorial-reset-btn")).to_be_enabled()
    assert mock_page.evaluate("typeof window.AvatarFloatingGuideReset") == "object"
    assert mock_page.evaluate("typeof window.appStorageLocation") == "object"
    assert mock_page.evaluate("typeof window.waitForStorageLocationStartupBarrier") == "undefined"
    assert mock_page.evaluate("typeof window.__nekoStorageLocationStartupBarrier") == "undefined"


@pytest.mark.frontend
def test_memory_browser_tutorial_cascader_dark_mode_uses_readable_text(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.add_init_script("window.localStorage.setItem('neko-dark-mode', 'true')")

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)
    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()

    colors = mock_page.evaluate(
        """
        () => {
            const trigger = document.querySelector('.tutorial-cascader-trigger');
            const option = document.querySelector('.tutorial-cascader-option[data-tutorial-page="home"]');
            return {
                theme: document.documentElement.getAttribute('data-theme'),
                triggerColor: getComputedStyle(trigger).color,
                optionColor: getComputedStyle(option).color,
                optionBackground: getComputedStyle(option).backgroundColor,
            };
        }
        """
    )

    assert colors == {
        "theme": "dark",
        "triggerColor": "rgb(15, 95, 122)",
        "optionColor": "rgb(15, 95, 122)",
        "optionBackground": "rgba(0, 0, 0, 0)",
    }


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
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)
    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-page='current_personality']").click()
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
            const first = window.showTutorialResetNotice('First message');
            const second = window.showTutorialResetNotice('Second message');
            const firstResult = await Promise.race([
                first,
                new Promise((resolve) => window.setTimeout(() => resolve('timeout'), 250)),
            ]);
            const visibleNotices = document.querySelectorAll('.tutorial-reset-notice-backdrop').length;
            const message = document.querySelector('.tutorial-reset-notice-message')?.textContent || '';
            document.querySelector('.tutorial-reset-notice-ok')?.click();
            const secondResult = await Promise.race([
                second,
                new Promise((resolve) => window.setTimeout(() => resolve('timeout'), 500)),
            ]);
            return {
                firstResult,
                secondResult,
                visibleNotices,
                message,
                remainingNotices: document.querySelectorAll('.tutorial-reset-notice-backdrop').length,
            };
        }
        """
    )

    assert result == {
        "firstResult": False,
        "secondResult": True,
        "visibleNotices": 1,
        "message": "Second message",
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
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)
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

    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-page='all']").click()
    expect(mock_page.locator(".tutorial-reset-value")).to_have_text("全部页面")
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
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)
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

    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-home-all='true']").click()
    expect(mock_page.locator(".tutorial-reset-value")).to_have_text("主页 / 全部重置")
    expect(mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-popup")).to_be_hidden()
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
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)

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
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)
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

    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-day='1']").click()
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

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector(".tutorial-cascader-trigger", timeout=10000)
    mock_page.locator("#tutorial-reset-cascader .tutorial-cascader-trigger").click()
    expect(mock_page.locator(".tutorial-cascader-option[data-tutorial-page='home']")).to_have_text("Home")
    expect(mock_page.locator(".tutorial-cascader-option[data-tutorial-day='1']")).to_have_text("Day 1")
    mock_page.locator(".tutorial-cascader-option[data-tutorial-page='home']").click()
    mock_page.locator(".tutorial-cascader-option[data-tutorial-day='1']").click()
    expect(mock_page.locator(".tutorial-reset-value")).to_have_text("Home / Day 1")


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
    
    # Verify the save row is now visible
    expect(mock_page.locator("#save-row")).to_be_visible()


@pytest.mark.frontend
def test_memory_browser_clear_all_dissolves_chat_items_before_removing(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """The Clear button should animate human/AI rows away and keep the system memo."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(3)

    mock_page.locator("#clear-memory-btn").click()

    expect(mock_page.locator("#memory-chat-edit .chat-item.is-dissolving")).to_have_count(2)
    expect(mock_page.locator("#memory-particle-canvas")).to_have_count(1)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(1, timeout=3000)
    expect(mock_page.locator("#memory-chat-edit .memo-textarea").first).to_have_value("这是测试备忘录内容。")
    expect(mock_page.locator("#save-status")).to_contain_text("已清空对话记录，备忘录已保留")


@pytest.mark.frontend
def test_memory_browser_delete_single_chat_item_dissolves_before_removing(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Per-row delete should use the same particle dissolve instead of vanishing instantly."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .delete-btn", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(3)

    mock_page.locator("#memory-chat-edit .delete-btn").first.click()

    expect(mock_page.locator("#memory-chat-edit .chat-item.is-dissolving")).to_have_count(1)
    expect(mock_page.locator("#memory-particle-canvas")).to_have_count(1)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(2, timeout=3000)
    expect(mock_page.locator("#memory-chat-edit")).not_to_contain_text("你好，测试猫娘！")
    expect(mock_page.locator("#memory-chat-edit")).to_contain_text("你好主人！我是测试猫娘喵~")


@pytest.mark.frontend
def test_memory_browser_clear_respects_reduced_motion(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """Reduced-motion users should not wait for the particle canvas animation."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)
    mock_page.emulate_media(reduced_motion="reduce")

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .chat-item", timeout=5000)
    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(3)

    mock_page.locator("#clear-memory-btn").click()

    expect(mock_page.locator("#memory-chat-edit .chat-item")).to_have_count(1)
    expect(mock_page.locator("#memory-particle-canvas")).to_have_count(0)
    expect(mock_page.locator("#save-status")).to_contain_text("已清空对话记录，备忘录已保留")


@pytest.mark.frontend
def test_memory_browser_particle_canvas_is_removed_on_pagehide(
    mock_page: Page,
    running_server: str,
    seed_memory_file,
):
    """The temporary particle canvas should not linger when the page unloads."""
    _install_ready_memory_browser_routes(mock_page, seed_memory_file)

    mock_page.goto(f"{running_server}/memory_browser")
    mock_page.wait_for_selector("#memory-chat-edit .delete-btn", timeout=5000)
    mock_page.locator("#memory-chat-edit .delete-btn").first.click()
    expect(mock_page.locator("#memory-particle-canvas")).to_have_count(1)

    mock_page.evaluate("window.dispatchEvent(new Event('pagehide'))")

    expect(mock_page.locator("#memory-particle-canvas")).to_have_count(0)


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
    
    # Wait for the page to fully initialize
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)
    
    # The auto-review checkbox should be present
    checkbox = mock_page.locator("#review-toggle-checkbox")
    expect(checkbox).to_be_attached()
    
    # Default is enabled (checked), toggle it off
    initial_state = checkbox.is_checked()
    
    # Toggle the checkbox via its label (since checkbox is styled via label)
    label = mock_page.locator("label[for='review-toggle-checkbox']")
    
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
    # Recoverable storage states keep the management entry enabled so the user
    # can resolve selection/recovery without leaving limited mode.
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
    mock_page.wait_for_selector("#memory-file-list button.cat-btn", state="attached", timeout=10000)

    mock_page.locator("#storage-location-manage-btn").click()
    expect(mock_page.locator("#storage-location-modal")).to_be_visible()
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
