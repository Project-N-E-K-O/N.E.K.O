"""小剧场轻量页面的真实 Chromium smoke。"""

import pytest
from playwright.sync_api import Page, expect


def _open_theater_page(page: Page, running_server: str) -> None:
    """打开真实页面并等待故事列表加载完成。"""
    page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelectorAll('#theater-story-select option').length >= 1")


def _leave_active_session(page: Page) -> None:
    """通过正式离场协议清理上一条测试可能留下的活动演出。"""
    if page.locator("#theater-end-btn").is_enabled():
        page.locator("#theater-end-btn").click()
        expect(page.locator("#theater-story-select")).to_be_enabled(timeout=8000)


@pytest.mark.frontend
def test_light_theater_supports_roleplay_and_static_choice(mock_page: Page, running_server: str):
    """自由输入先得到回应，静态 Choice 随后仍能推进剧情。"""
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    # 先切换短篇再切回长篇，验证未开演时顶部身份卡会跟随剧本实时更新。
    mock_page.locator("#theater-story-select").select_option("tape_for_tomorrow_story")
    expect(mock_page.locator("#theater-story-intro-title")).to_contain_text("留给明天的那盘磁带")
    mock_page.locator("#theater-story-select").select_option("always_like_you_story")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    assert mock_page.evaluate("document.querySelector('.theater-stage > #theater-story-intro') !== null")
    # 真实浏览器必须加载可播放的银河视频，并保持静音循环，避免干扰剧情音频。
    assert mock_page.evaluate(
        """() => {
            const video = document.querySelector('#theater-galaxy-video');
            return Boolean(video && video.autoplay && video.muted && video.loop && video.playsInline);
        }"""
    )
    expect(mock_page.locator("#theater-story-intro-title")).to_contain_text("在晚风重逢以前")
    expect(mock_page.locator("#theater-player-role")).to_contain_text("声音档案计划负责人")
    expect(mock_page.locator("#theater-catgirl-role")).to_contain_text("声音修复师")
    # 折叠后舞台应缩为紧凑栏并释放高度，再次展开时背景介绍必须原样恢复。
    expanded_console_height = mock_page.locator(".theater-console").evaluate("element => element.getBoundingClientRect().height")
    mock_page.locator("#theater-stage-toggle").click()
    expect(mock_page.locator("#theater-stage-toggle")).to_have_attribute("aria-expanded", "false")
    expect(mock_page.locator("#theater-stage-toggle-label")).to_have_text("展开舞台")
    expect(mock_page.locator("#theater-story-intro")).to_be_hidden()
    mock_page.wait_for_timeout(220)
    collapsed_console_height = mock_page.locator(".theater-console").evaluate("element => element.getBoundingClientRect().height")
    assert collapsed_console_height > expanded_console_height + 150
    mock_page.locator("#theater-stage-toggle").click()
    expect(mock_page.locator("#theater-stage-toggle")).to_have_attribute("aria-expanded", "true")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    # 开演后身份卡继续常驻，玩家在长回合中仍可核对人设和目标。
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    expect(mock_page.locator("#theater-action-choices .theater-choice-button").first).to_be_visible()
    expect(mock_page.locator("#theater-dialogue-choices .theater-choice-button").first).to_be_visible()

    initial_choice_count = mock_page.locator(".theater-choice-button").count()
    mock_page.locator("#theater-input").fill("我想先听听你现在的心情")
    mock_page.locator("#theater-send-btn").click()
    expect(mock_page.locator("#theater-trace-summary")).to_contain_text("当前场景", timeout=8000)
    # 玩家自由发言必须贴右、自适应短文本宽度，并与旁白和猫娘对白使用不同底色。
    player_turn_style = mock_page.evaluate(
        """() => {
            const log = document.querySelector('#theater-log').getBoundingClientRect();
            const player = Array.from(document.querySelectorAll('.theater-turn.user')).at(-1);
            const narration = Array.from(document.querySelectorAll('.theater-turn.narration')).at(-1);
            const dialogue = Array.from(document.querySelectorAll('.theater-turn.dialogue')).at(-1);
            const playerRect = player.getBoundingClientRect();
            return {
                rightGap: log.right - playerRect.right,
                playerWidth: playerRect.width,
                logWidth: log.width,
                textAlign: getComputedStyle(player).textAlign,
                playerBackground: getComputedStyle(player).backgroundColor,
                narrationBackground: getComputedStyle(narration).backgroundColor,
                dialogueBackground: getComputedStyle(dialogue).backgroundColor,
            };
        }"""
    )
    assert player_turn_style["rightGap"] < 24
    assert player_turn_style["playerWidth"] < player_turn_style["logWidth"] * 0.6
    assert player_turn_style["textAlign"] == "right"
    assert len({
        player_turn_style["playerBackground"],
        player_turn_style["narrationBackground"],
        player_turn_style["dialogueBackground"],
    }) == 3
    # 日志与本轮行动必须形成独立上下区域，回合摘要不得覆盖演绎内容。
    layout = mock_page.evaluate(
        """() => {
            const log = document.querySelector('#theater-log').getBoundingClientRect();
            const trace = document.querySelector('#theater-trace-panel').getBoundingClientRect();
            return { logBottom: log.bottom, traceTop: trace.top };
        }"""
    )
    assert layout["traceTop"] >= layout["logBottom"] + 8
    expect(mock_page.locator(".theater-choice-button")).to_have_count(initial_choice_count)

    mock_page.locator("#theater-action-choices .theater-choice-button").first.click()
    expect(mock_page.locator("#theater-trace-summary")).to_contain_text("推进", timeout=8000)
    _leave_active_session(mock_page)


@pytest.mark.frontend
def test_light_theater_restores_after_reload(mock_page: Page, running_server: str):
    """刷新后只读取服务端公开快照，不重新创建 Session。"""
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    session_id = mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')")
    assert str(session_id).startswith("theater_")

    mock_page.reload(wait_until="domcontentloaded")
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    assert mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')") == session_id
    expect(mock_page.locator(".theater-choice-button").first).to_be_visible()
    _leave_active_session(mock_page)


@pytest.mark.frontend
@pytest.mark.parametrize("path", ["/chat", "/subtitle"])
def test_theater_assets_do_not_inject_other_pages(mock_page: Page, running_server: str, path: str):
    """小剧场资源保持独立，不污染聊天和字幕页面。"""
    mock_page.goto(f"{running_server}{path}", wait_until="domcontentloaded")
    assert mock_page.locator("[data-theater-app]").count() == 0
    assert "/static/js/theater.js" not in mock_page.content()
