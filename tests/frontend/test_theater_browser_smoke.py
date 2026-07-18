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
def test_story_intro_separates_stable_background_from_initial_scene(mock_page: Page, running_server: str):
    """开演前把测试 Story 的稳定背景与正在发生的开场动作分开。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    public_backgrounds = mock_page.evaluate(
        """async () => {
            const payload = await fetch('/api/theater/stories').then(response => response.json());
            return Object.fromEntries(payload.stories.map(story => [story.id, story.background]));
        }"""
    )
    mock_page.locator("#theater-story-select").select_option("framework_contract_story")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    background = mock_page.locator("#theater-story-intro-background").inner_text()
    assert background == public_backgrounds["framework_contract_story"]
    assert "中性 Story Package" in background
    assert "桌面放着一枚带编号的测试牌" not in background
    expect(mock_page.locator("#theater-log")).to_contain_text(
        "桌面放着一枚带编号的测试牌"
    )
    # 长简介由介绍卡自身滚动承载，不能通过裁字或题材专属前端分支规避正文。
    assert mock_page.locator("#theater-story-intro").evaluate(
        "element => getComputedStyle(element).overflowY"
    ) in {"auto", "scroll"}


@pytest.mark.frontend
def test_light_theater_supports_roleplay_and_static_choice(mock_page: Page, running_server: str):
    """自由输入先得到回应，静态 Choice 随后仍能推进剧情。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    # 浏览器 smoke 只读取测试目录中的中性 Story，不能重新依赖已删除的正式剧本。
    expect(mock_page.locator("#theater-story-select option")).to_have_count(1)
    mock_page.locator("#theater-story-select").select_option("framework_contract_story")
    expect(mock_page.locator("#theater-story-intro")).to_be_visible()
    assert mock_page.evaluate("document.querySelector('.theater-stage > #theater-story-intro') !== null")
    # 真实浏览器必须加载可播放的银河视频，并保持静音循环，避免干扰剧情音频。
    assert mock_page.evaluate(
        """() => {
            const video = document.querySelector('#theater-galaxy-video');
            return Boolean(video && video.autoplay && video.muted && video.loop && video.playsInline);
        }"""
    )
    expect(mock_page.locator("#theater-story-intro-title")).to_contain_text(
        "小剧场框架合同夹具"
    )
    expect(mock_page.locator("#theater-player-role")).to_contain_text("框架合同验证")
    expect(mock_page.locator("#theater-catgirl-role")).to_contain_text("公开步骤")
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
    expect(mock_page.locator("#theater-board-available-props")).to_contain_text(
        "公开测试牌"
    )

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

    exchange_callback = "你把测试牌递给她，双方公开确认交换已经完成。"
    progress_scene = "测试牌仍在双方视线内，记录板等待写入公开完成结果。"
    mock_page.locator("#theater-action-choices .theater-choice-button").first.click()
    expect(mock_page.locator("#theater-log")).to_contain_text(
        exchange_callback, timeout=20000
    )
    expect(mock_page.locator("#theater-log")).to_contain_text(
        progress_scene, timeout=20000
    )
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
        [exchange_callback, progress_scene],
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
    expect(mock_page.locator("#theater-log .theater-turn.narration").first).to_contain_text(
        "桌面放着一枚带编号的测试牌"
    )
    expect(mock_page.locator(".theater-choice-button").first).to_be_visible()
    _leave_active_session(mock_page)


@pytest.mark.frontend
def test_dormant_session_restores_with_resumable_status(
    mock_page: Page, running_server: str
):
    """休眠快照恢复后应保留输入和 Choice，并显示可继续而不是已结束。"""  # noqa: DOCSTRING_CJK
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    session_id = mock_page.evaluate(
        "window.localStorage.getItem('neko.theater.activeSession.v1')"
    )
    payload = mock_page.evaluate(
        """async sessionId => fetch(
            '/api/theater/session/state?session_id=' + encodeURIComponent(sessionId)
        ).then(response => response.json())""",
        session_id,
    )
    payload["session_lifecycle"] = "dormant"

    mock_page.route(
        "**/api/theater/session/state**",
        lambda route: route.fulfill(status=200, json=payload),
    )
    mock_page.reload(wait_until="domcontentloaded")

    expect(mock_page.locator("#theater-status")).to_have_text(
        "演出已休眠，可以继续", timeout=8000
    )
    expect(mock_page.locator("#theater-input")).to_be_enabled()
    expect(mock_page.locator(".theater-choice-button").first).to_be_visible()
    assert (
        mock_page.evaluate(
            "window.localStorage.getItem('neko.theater.activeSession.v1')"
        )
        == session_id
    )

    mock_page.unroute("**/api/theater/session/state**")
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
def test_incompatible_save_requires_explicit_restart_and_keeps_old_pointer(mock_page: Page, running_server: str):
    """不兼容存档应保留旧指针并只允许提示卡发出显式重开请求。"""  # noqa: DOCSTRING_CJK
    old_session_id = "theater_preserved_incompatible_smoke"
    observed_requests = []
    start_payloads = []

    # 先通过正式离场协议清理前一测试可能留下的真实 active，避免 mock 提示遮住后端测试状态。
    mock_page.on("request", lambda request: observed_requests.append((request.method, request.url)))
    _open_theater_page(mock_page, running_server)
    _leave_active_session(mock_page)

    # 浏览器侧模拟服务端已保留但无法恢复的 Story revision 存档，不伪造任何剧本专属内容。
    mock_page.route(
        "**/api/theater/session/active",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"ok":false,"reason":"session_story_revision_mismatch",'
                f'"session_id":"{old_session_id}"}}'
            ),
        ),
    )

    def capture_start(route):
        """记录正式开场请求后交给真实后端，验证页面发送的是现有协议。"""  # noqa: DOCSTRING_CJK
        start_payloads.append(route.request.post_data_json)
        route.continue_()

    mock_page.route("**/api/theater/session/start", capture_start)
    mock_page.reload(wait_until="domcontentloaded")

    expect(mock_page.locator("#theater-compatibility-notice")).to_be_visible(timeout=8000)
    expect(mock_page.locator("#theater-compatibility-title")).to_have_text("旧演出无法继续")
    expect(mock_page.locator("#theater-start-btn")).to_be_disabled()
    expect(mock_page.locator("#theater-restart-btn")).to_be_enabled()
    assert mock_page.evaluate("window.localStorage.getItem('neko.theater.activeSession.v1')") == old_session_id

    mock_page.locator("#theater-restart-btn").click()
    expect(mock_page.locator("#theater-input")).to_be_enabled(timeout=8000)
    expect(mock_page.locator("#theater-compatibility-notice")).to_be_hidden()
    assert start_payloads and start_payloads[0]["replace_incompatible_session"] is True
    assert all(method != "DELETE" for method, _ in observed_requests)

    # 清理真实后端新建的 smoke Session，避免拦截的旧响应影响正式离场查询。
    mock_page.unroute("**/api/theater/session/active")
    mock_page.unroute("**/api/theater/session/start")
    _leave_active_session(mock_page)


@pytest.mark.frontend
@pytest.mark.parametrize("path", ["/chat", "/subtitle"])
def test_theater_assets_do_not_inject_other_pages(mock_page: Page, running_server: str, path: str):
    """小剧场资源保持独立，不污染聊天和字幕页面。"""  # noqa: DOCSTRING_CJK
    mock_page.goto(f"{running_server}{path}", wait_until="domcontentloaded")
    assert mock_page.locator("[data-theater-app]").count() == 0
    assert "/static/js/theater.js" not in mock_page.content()
