"""小剧场轻量页面的真实 Chromium smoke。"""  # noqa: DOCSTRING_CJK

import pytest
from playwright.sync_api import Page, expect


def _open_theater_page(page: Page, running_server: str) -> None:
    """打开真实页面并等待故事列表加载完成。"""  # noqa: DOCSTRING_CJK
    page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    page.wait_for_function("() => document.querySelectorAll('#theater-story-select option').length >= 1")


def _leave_active_session(page: Page) -> None:
    """通过正式离场协议清理上一条测试可能留下的活动演出。"""  # noqa: DOCSTRING_CJK
    # option 会先于异步 Session 恢复完成出现；先问服务端真值，再等待对应按钮追上该状态。
    active = page.evaluate(
        """async () => fetch('/api/theater/session/active').then(response => response.json())"""
    )
    if active.get("ok") and active.get("can_resume"):
        expect(page.locator("#theater-end-btn")).to_be_enabled(timeout=8000)
        page.locator("#theater-end-btn").click()
    # 离场请求的 finally 才会按已加载 story 重新开放开始按钮；下拉框本身不足以证明清理完成。
    expect(page.locator("#theater-start-btn")).to_be_enabled(timeout=8000)


@pytest.mark.frontend
def test_story_intro_exposes_concise_current_catgirl_background(mock_page: Page, running_server: str):
    """开演前真实页面只展示开场背景，并把占位符替换为当前猫娘名。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    mock_page.locator("#theater-story-select").select_option("date_list_last_item_story")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    intro_brief = mock_page.locator("#theater-story-intro-brief").inner_text()
    assert 350 <= len(intro_brief) < 500
    assert intro_brief.startswith("你与")
    assert "当前猫娘" not in intro_brief
    assert "{{lanlan_name}}" not in intro_brief
    assert "提着一个牛皮纸袋来到门廊" in intro_brief
    assert "两张淡蓝色的普通入场券" in intro_brief
    assert intro_brief.endswith("挂坠正一点点滑向玄关的铜质钥匙盘。")
    assert "你可以选择" not in intro_brief
    assert "你需要回应" not in intro_brief


@pytest.mark.frontend
def test_light_theater_supports_roleplay_and_static_choice(mock_page: Page, running_server: str):
    """自由输入先得到回应，静态 Choice 随后仍能推进剧情。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    # 删除旧内置故事后，页面必须直接预览唯一的新甜蜜约会剧本。
    expect(mock_page.locator("#theater-story-select option")).to_have_count(1)
    mock_page.locator("#theater-story-select").select_option("date_list_last_item_story")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    assert mock_page.evaluate("document.querySelector('.theater-stage > #theater-story-intro') !== null")
    # 真实浏览器必须加载可播放的银河视频，并保持静音循环，避免干扰剧情音频。
    assert mock_page.evaluate(
        """() => {
            const video = document.querySelector('#theater-galaxy-video');
            return Boolean(video && video.autoplay && video.muted && video.loop && video.playsInline);
        }"""
    )
    expect(mock_page.locator("#theater-story-intro-title")).to_contain_text("约会清单最后一项")
    expect(mock_page.locator("#theater-player-role")).to_contain_text("七项清单")
    expect(mock_page.locator("#theater-catgirl-role")).to_contain_text("朋友分寸")
    # 折叠后舞台应缩为紧凑栏并释放高度，再次展开时背景介绍必须原样恢复。
    scene_text_before_collapse = mock_page.locator("#theater-log .theater-turn.narration").first.inner_text()
    expect(mock_page.locator("#theater-scene-text")).to_have_count(0)
    expanded_console_height = mock_page.locator(".theater-console").evaluate("element => element.getBoundingClientRect().height")
    mock_page.locator("#theater-stage-toggle").click()
    expect(mock_page.locator("#theater-stage-toggle")).to_have_attribute("aria-expanded", "false")
    expect(mock_page.locator("#theater-stage-toggle-label")).to_have_text("展开舞台")
    expect(mock_page.locator("#theater-story-intro")).to_be_hidden()
    # Scene 只留在下方演绎日志，折叠舞台时不额外复制到紧凑栏。
    expect(mock_page.locator("#theater-log .theater-turn.narration").first).to_have_text(scene_text_before_collapse)
    mock_page.wait_for_timeout(220)
    collapsed_console_height = mock_page.locator(".theater-console").evaluate("element => element.getBoundingClientRect().height")
    assert collapsed_console_height > expanded_console_height + 150
    mock_page.locator("#theater-stage-toggle").click()
    expect(mock_page.locator("#theater-stage-toggle")).to_have_attribute("aria-expanded", "true")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    # 舞台底栏的 Scene 同时作为第一条旁白进入演绎日志，后续同一 Scene 不应每回合重复。
    active_scene_text = scene_text_before_collapse
    scene_narrations = mock_page.locator("#theater-log .theater-turn.narration").filter(has_text=active_scene_text)
    expect(scene_narrations).to_have_count(1)
    # 开演后身份卡继续常驻，玩家在长回合中仍可核对人设和目标。
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    expect(mock_page.locator("#theater-action-choices .theater-choice-button").first).to_be_visible()
    expect(mock_page.locator("#theater-dialogue-choices .theater-choice-button").first).to_be_visible()
    # 剧本面板有内容时只显示标题，玩家主动展开后才展示道具与线索。
    expect(mock_page.locator("#theater-scenario-board")).to_be_visible()
    expect(mock_page.locator("#theater-board-toggle")).to_have_attribute("aria-expanded", "false")
    expect(mock_page.locator(".theater-workspace")).to_have_attribute("data-board-expanded", "false")
    expect(mock_page.locator("#theater-board-groups")).to_be_hidden()
    mock_page.locator("#theater-board-toggle").click()
    expect(mock_page.locator("#theater-board-toggle")).to_have_attribute("aria-expanded", "true")
    expect(mock_page.locator(".theater-workspace")).to_have_attribute("data-board-expanded", "true")
    expect(mock_page.locator("#theater-board-groups")).to_be_visible()
    expect(mock_page.locator("#theater-board-available-props")).to_contain_text("七项约会清单")

    initial_choice_count = mock_page.locator(".theater-choice-button").count()
    pending_input_routes = []
    mock_page.route("**/api/theater/session/input", lambda route: pending_input_routes.append(route))
    mock_page.locator("#theater-input").fill("我想先听听你现在的心情")
    mock_page.locator("#theater-send-btn").click()
    # 请求悬而未决时展示加载旁白；放行响应后临时气泡必须移除。
    expect(mock_page.locator(".theater-generation-loading")).to_be_visible()
    expect(mock_page.locator(".theater-generation-loading")).to_contain_text("片刻之后")
    for _ in range(20):
        if pending_input_routes:
            break
        mock_page.wait_for_timeout(25)
    assert pending_input_routes
    pending_input_routes.pop().continue_()
    expect(mock_page.locator("#theater-trace-summary")).to_contain_text("当前场景", timeout=20000)
    expect(mock_page.locator(".theater-generation-loading")).to_have_count(0, timeout=20000)
    mock_page.unroute("**/api/theater/session/input")
    expect(scene_narrations).to_have_count(1)
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
    expect(mock_page.locator("#theater-trace-summary")).to_contain_text("推进", timeout=20000)
    # 同一 setup Scene 内推进只追加一次 callback，不能重复开场环境。
    expect(scene_narrations).to_have_count(1)

    departure_callback = "你拿起搭在椅背上的外套，走到门口接受同行邀请；不久后，你们一同抵达旧街牌楼。"
    festival_scene = "旧街牌楼挂满浅金色绢灯"
    mock_page.locator("#theater-dialogue-choices .theater-choice-button").first.click()
    expect(mock_page.locator("#theater-log")).to_contain_text(departure_callback, timeout=20000)
    expect(mock_page.locator("#theater-log")).to_contain_text(festival_scene, timeout=20000)
    # 实时跨 Scene 必须先完成玩家选择的离开/抵达动作，再展示新环境，最后才是猫娘对白。
    live_order = mock_page.evaluate(
        """([callbackText, sceneText]) => Array.from(document.querySelectorAll('#theater-log .theater-turn')).map(
            row => ({ text: row.textContent.trim(), classes: row.className })
        ).reduce((result, row, index) => {
            if (row.text === callbackText) result.callback = index;
            if (row.text.includes(sceneText)) result.scene = index;
            if (row.classes.includes('dialogue')) result.lastDialogue = index;
            return result;
        }, { callback: -1, scene: -1, lastDialogue: -1 })""",
        [departure_callback, festival_scene],
    )
    assert 0 <= live_order["callback"] < live_order["scene"] < live_order["lastDialogue"]
    _leave_active_session(mock_page)


@pytest.mark.frontend
def test_light_theater_restores_after_reload(mock_page: Page, running_server: str):
    """刷新后只读取服务端公开快照，不重新创建 Session。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    session_id = mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')")
    assert str(session_id).startswith("theater_")

    mock_page.reload(wait_until="domcontentloaded")
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    assert mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')") == session_id
    # 恢复没有完整历史可供重放，因此必须先用当前 Scene 建立环境，再展示快照回合。
    expect(mock_page.locator("#theater-log .theater-turn.narration").first).to_contain_text("午后的客厅")
    expect(mock_page.locator(".theater-choice-button").first).to_be_visible()
    _leave_active_session(mock_page)


@pytest.mark.frontend
def test_stale_input_without_active_session_reopens_start_controls(mock_page: Page, running_server: str):
    """输入时 Session 失效且无可恢复演出时，页面无需刷新即可重新开场。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    session_id = mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')")

    # 只拦截本轮输入与恢复查询，模拟另一个窗口已经替换并清理活动 Session。
    mock_page.route(
        "**/api/theater/session/input",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok":false,"reason":"stale_session","skipped":true}',
        ),
    )
    mock_page.route(
        "**/api/theater/session/active",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"ok":false,"reason":"active_session_not_found"}',
        ),
    )
    mock_page.locator("#theater-input").fill("这句不会提交到旧演出")
    mock_page.locator("#theater-send-btn").click()

    expect(mock_page.locator("#theater-start-btn")).to_be_enabled(timeout=8000)
    expect(mock_page.locator("#theater-story-select")).to_be_enabled()
    assert mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')") is None
    expect(mock_page.locator("#theater-log")).not_to_contain_text("这句不会提交到旧演出")
    expect(mock_page.locator("#theater-input")).to_have_value("这句不会提交到旧演出")

    # 解除拦截并刷新，恢复真实后端中的测试 Session，再按正式离场协议清理测试状态。
    mock_page.unroute("**/api/theater/session/input")
    mock_page.unroute("**/api/theater/session/active")
    mock_page.reload(wait_until="domcontentloaded")
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    assert mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')") == session_id
    _leave_active_session(mock_page)


@pytest.mark.frontend
@pytest.mark.parametrize("path", ["/chat", "/subtitle"])
def test_theater_assets_do_not_inject_other_pages(mock_page: Page, running_server: str, path: str):
    """小剧场资源保持独立，不污染聊天和字幕页面。"""  # noqa: DOCSTRING_CJK
    mock_page.goto(f"{running_server}{path}", wait_until="domcontentloaded")
    assert mock_page.locator("[data-theater-app]").count() == 0
    assert "/static/js/theater.js" not in mock_page.content()
