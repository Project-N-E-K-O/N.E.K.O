"""验证 Numeric v2 演绎真正进入现有 React 胶囊与历史面板。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
import re

import pytest
from playwright.sync_api import Page, Route, expect


def _snapshot(
    *,
    revision: int,
    lifecycle_revision: int = 0,
    performance_history: list[dict] | None = None,
    story_id: str = "capsule-browser-story",
    session_id: str = "capsule-browser-session",
) -> dict:
    return {
        "ok": True,
        "story_title": "雨巷来信",
        "participants": {
            "player_name": "老谢",
            "catgirl_name": "小葵",
        },
        "story_intro": {
            "background": "雨夜的旧花店。",
            "player_identity": "你是归乡故人。",
            "catgirl_identity": "小葵是花店主人。",
        },
        "scene": {
            "id": "mainline_01",
            "chapter": "第一章",
            "summary": "旧信仍在桌上。",
            "terminal": False,
            "ending": None,
            "node_turn_count": revision,
            "min_turns": 1,
        },
        "suggested_inputs": ["把旧信递给她", "问她这些年过得好吗"],
        "session": {
            "session_id": session_id,
            "story_package_id": story_id,
            "revision": revision,
            "lifecycle_revision": lifecycle_revision,
            "status": "active",
            "opening_performance": {
                "scene_narration": "雨落在花店檐角。",
                "performance": "你终于回来了。",
                "suggested_inputs": [],
            },
            "performance_history": performance_history or [],
        },
    }


@pytest.mark.frontend
def test_theater_capsule_drops_ordinary_reply_preview_on_takeover(
    mock_page: Page,
    running_server: str,
):
    """小剧场接管胶囊时，不得继续展示此前普通聊天的猫娘回复。"""  # noqa: DOCSTRING_CJK

    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function("() => !!window.reactChatWindowHost")
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
        }"""
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isMounted && window.reactChatWindowHost.isMounted()"
    )
    mock_page.evaluate(
        """() => {
            window.reactChatWindowHost.setChatSurfaceMode('compact');
            window.reactChatWindowHost.setCompactChatState('default');
            window.reactChatWindowHost.setMessages([{
                id: 'ordinary-assistant-before-theater',
                role: 'assistant',
                author: 'Neko',
                blocks: [{ type: 'text', text: '进入小剧场前的旧回复' }],
                status: 'streaming'
            }]);
        }"""
    )
    mock_page.wait_for_function(
        """() => {
            const text = document.querySelector('.compact-chat-capsule-text')?.textContent || '';
            return text.length >= 2 && '进入小剧场前的旧回复'.startsWith(text);
        }"""
    )
    ordinary_preview = mock_page.locator(".compact-chat-capsule-text").text_content() or ""

    mock_page.evaluate(
        """() => window.reactChatWindowHost.setViewProps({
            chatSurfaceMode: 'compact',
            compactChatState: 'default',
            composerDisabled: true,
            theaterPresentation: {
                active: true,
                phase: 'loading',
                history: [],
                suggestedInputs: []
            }
        })"""
    )

    expect(mock_page.locator(".compact-chat-capsule-button")).to_be_visible()
    expect(mock_page.locator(".compact-chat-capsule-text")).not_to_contain_text(
        ordinary_preview
    )
    # 接管只改变当前展示职责，普通聊天消息仍留在宿主历史中供退出剧场后恢复。
    assert mock_page.evaluate(
        """() => window.reactChatWindowHost.getState().messages.some(
            (message) => message.id === 'ordinary-assistant-before-theater'
        )"""
    ) is True


@pytest.mark.frontend
def test_theater_capsule_reasserts_composer_visibility_on_active_render(
    mock_page: Page,
    running_server: str,
):
    """外部 goodbye 状态隐藏输入区后，下一次剧场渲染必须立即重新显示。"""  # noqa: DOCSTRING_CJK

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_snapshot(revision=0), ensure_ascii=False),
            )
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1',
                action: 'theater:launch-request',
                launch_id: 'composer-visibility-launch',
                launch_action: 'continue',
                story_id: 'capsule-browser-story',
                session_id: 'capsule-browser-session',
                revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )

    mock_page.evaluate(
        """() => {
            window.reactChatWindowHost.setGoodbyeComposerHidden(true, 'review-regression');
            window.dispatchEvent(new Event('localechange'));
        }"""
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.getState().goodbyeComposerHidden === false"
    )
    expect(mock_page.locator(".composer-input")).to_be_visible()


@pytest.mark.frontend
def test_theater_capsule_stops_ordinary_voice_before_launch(
    mock_page: Page,
    running_server: str,
):
    """剧场接管前必须先停止准备中的普通语音，并在活跃期间拒绝重新开麦。"""  # noqa: DOCSTRING_CJK

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(_snapshot(revision=0), ensure_ascii=False),
            )
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
        " && window.appAudioCapture && window.appWebSocket && window.appState"
    )
    mock_page.evaluate(
        """() => {
            window.__theaterVoiceStopEvents = [];
            window.appState.voiceStartPending = true;
            window.appState.voiceSessionStartEpoch = 7;
            window.isMicStarting = true;
            const originalCancelPendingSessionStart = window.cancelPendingSessionStart;
            window.cancelPendingSessionStart = (reason) => {
                window.__theaterVoiceStopEvents.push('cancel-pending');
                originalCancelPendingSessionStart(reason);
            };
            window.appAudioCapture.stopMicCapture = async () => {
                window.__theaterVoiceStopEvents.push('stop-capture');
            };
            const originalSend = window.appWebSocket.send.bind(window.appWebSocket);
            window.appWebSocket.send = (payload) => {
                if (payload && payload.action === 'pause_session') {
                    window.__theaterVoiceStopEvents.push('pause-session');
                    return;
                }
                return originalSend(payload);
            };
            window.postMessage({
                schema: 'neko.theater.interpage.v1',
                action: 'theater:launch-request',
                launch_id: 'voice-stop-launch',
                launch_action: 'continue',
                story_id: 'capsule-browser-story',
                session_id: 'capsule-browser-session',
                revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )

    assert mock_page.evaluate("() => window.__theaterVoiceStopEvents") == [
        "cancel-pending",
        "stop-capture",
        "pause-session",
    ]
    assert mock_page.evaluate("() => window.appState.voiceSessionStartEpoch") == 8
    assert mock_page.evaluate("() => window.appState.voiceStartPending") is False
    assert mock_page.evaluate("() => window.isMicStarting") is False
    assert mock_page.evaluate("() => window.startMicCapture()") is False


@pytest.mark.frontend
def test_theater_capsule_restores_committed_turn_after_end_failure(
    mock_page: Page,
    running_server: str,
):
    """逐字播放期间结束失败后，必须恢复完整正文和推荐输入。"""  # noqa: DOCSTRING_CJK

    performance = "（她把旧信压在桌角）这封信我一直没有拆开，因为我想等你回来亲手确认最后一页。"
    turn = {
        "revision": 1,
        "input_text": "把旧信递给她",
        "performance": performance,
        "suggested_inputs": ["请她一起拆开", "先问这些年发生了什么"],
    }

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            fulfill(route, _snapshot(revision=0))
            return
        if path.endswith("/api/theater-numeric/session/input"):
            payload = _snapshot(revision=1, performance_history=[turn])
            payload["suggested_inputs"] = turn["suggested_inputs"]
            payload["performance"] = turn
            fulfill(route, payload)
            return
        if path.endswith("/api/theater-numeric/session/end"):
            fulfill(route, {"ok": False, "reason": "numeric_end_failed"})
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.openOrFocusWindow = () => null;
            window.showConfirm = (_message, _title, options) => {
                if (typeof options.onResolve === 'function') options.onResolve(true);
                return Promise.resolve(true);
            };
            window.postMessage({
                schema: 'neko.theater.interpage.v1',
                action: 'theater:launch-request',
                launch_id: 'end-failure-restore-launch',
                launch_action: 'continue',
                story_id: 'capsule-browser-story',
                session_id: 'capsule-browser-session',
                revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    mock_page.evaluate(
        "() => window.nekoTheaterRuntime.handleComposerSubmit('把旧信递给她')"
    )
    mock_page.wait_for_function(
        """() => window.nekoTheaterRuntime.getState().history.some((entry) =>
            entry.status === 'streaming' && entry.text.length > 0
        )"""
    )
    mock_page.evaluate(
        "() => { window.__failedEndResult = null; window.nekoTheaterRuntime.requestEnd().then((value) => { window.__failedEndResult = value; }); }"
    )
    mock_page.wait_for_function(
        "() => window.__failedEndResult === false"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )

    state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert state["currentBlock"] is None
    assert all(entry.get("status") != "streaming" for entry in state["history"])
    assert state["history"][-1]["text"] == performance
    assert state["suggestedInputs"] == turn["suggested_inputs"]
    expect(mock_page.locator(".composer-galgame-option")).to_have_count(2)
    expect(mock_page.locator(".compact-theater-history-error")).to_contain_text(
        "当前演绎状态无法结束"
    )


@pytest.mark.frontend
def test_theater_capsule_ignores_late_turn_after_launching_another_session(
    mock_page: Page,
    running_server: str,
):
    """A 回合等待期间切到 B 后，A 的迟到响应只能留在服务端。"""  # noqa: DOCSTRING_CJK

    pending_input: dict[str, Route] = {}

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/session-a"):
            fulfill(route, _snapshot(revision=0, story_id="story-a", session_id="session-a"))
            return
        if path.endswith("/api/theater-numeric/session/session-b"):
            fulfill(route, _snapshot(revision=0, story_id="story-b", session_id="session-b"))
            return
        if path.endswith("/api/theater-numeric/session/input"):
            pending_input["route"] = route
            return
        if path.endswith("/api/theater-numeric/session/speak-block"):
            fulfill(route, {"ok": True, "audio_queued": False})
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
                launch_id: 'launch-a', launch_action: 'continue',
                story_id: 'story-a', session_id: 'session-a', revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().sessionId === 'session-a'"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    mock_page.evaluate(
        "() => window.nekoTheaterRuntime.handleComposerSubmit('A 的待处理输入')"
    )
    for _ in range(50):
        if "route" in pending_input:
            break
        mock_page.wait_for_timeout(20)
    assert "route" in pending_input

    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
            launch_id: 'launch-b', launch_action: 'continue',
            story_id: 'story-b', session_id: 'session-b', revision: 0
        }, window.location.origin)"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().sessionId === 'session-b'"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    late_turn = {
        "revision": 1,
        "input_text": "A 的待处理输入",
        "performance": "这是不应显示在 B 中的 A 回复。",
        "suggested_inputs": [],
    }
    late_payload = _snapshot(
        revision=1,
        performance_history=[late_turn],
        story_id="story-a",
        session_id="session-a",
    )
    late_payload["performance"] = late_turn
    fulfill(pending_input["route"], late_payload)
    mock_page.wait_for_timeout(200)

    state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert state["storyId"] == "story-b"
    assert state["sessionId"] == "session-b"
    assert state["revision"] == 0
    assert all("不应显示" not in item.get("text", "") for item in state["history"])


@pytest.mark.frontend
def test_theater_capsule_ignores_late_launch_snapshot(
    mock_page: Page,
    running_server: str,
):
    """先发起的启动快照迟到时，不能覆盖后启动的 Session。"""  # noqa: DOCSTRING_CJK

    pending_launch: dict[str, Route] = {}

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/session-a"):
            pending_launch["route"] = route
            return
        if path.endswith("/api/theater-numeric/session/session-b"):
            fulfill(route, _snapshot(revision=0, story_id="story-b", session_id="session-b"))
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
                launch_id: 'late-launch-a', launch_action: 'continue',
                story_id: 'story-a', session_id: 'session-a', revision: 0
            }, window.location.origin);
        }"""
    )
    for _ in range(50):
        if "route" in pending_launch:
            break
        mock_page.wait_for_timeout(20)
    assert "route" in pending_launch

    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
            launch_id: 'latest-launch-b', launch_action: 'continue',
            story_id: 'story-b', session_id: 'session-b', revision: 0
        }, window.location.origin)"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().sessionId === 'session-b'"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )

    fulfill(
        pending_launch["route"],
        _snapshot(revision=0, story_id="story-a", session_id="session-a"),
    )
    mock_page.wait_for_timeout(200)

    state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert state["active"] is True
    assert state["storyId"] == "story-b"
    assert state["sessionId"] == "session-b"
    assert state["phase"] == "awaiting_player"


@pytest.mark.frontend
def test_theater_capsule_rejects_stale_launch_without_replacing_active_runtime(
    mock_page: Page,
    running_server: str,
):
    """同 Session 的旧 revision 启动不能清空健康演绎或中断当前音频。"""  # noqa: DOCSTRING_CJK

    turn = {
        "revision": 1,
        "input_text": "把旧信递给她",
        "performance": "（她接过旧信）这一页，我们一起看。",
        "suggested_inputs": ["请她拆开信封"],
    }
    session_requests = []

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/session-a"):
            session_requests.append(path)
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _snapshot(
                        revision=1,
                        performance_history=[turn],
                        story_id="story-a",
                        session_id="session-a",
                    ),
                    ensure_ascii=False,
                ),
            )
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
                launch_id: 'healthy-launch', launch_action: 'continue',
                story_id: 'story-a', session_id: 'session-a', revision: 1
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().revision === 1"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    before = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")

    mock_page.evaluate(
        """() => {
            window.__theaterAudioClears = 0;
            window.appAudioPlayback = {
                clearAudioQueueWithoutDecoderReset: () => { window.__theaterAudioClears += 1; }
            };
            window.postMessage({
                schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
                launch_id: 'stale-launch', launch_action: 'continue',
                story_id: 'story-a', session_id: 'session-a', revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_timeout(200)

    after = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert after["active"] is True
    assert after["storyId"] == "story-a"
    assert after["sessionId"] == "session-a"
    assert after["revision"] == 1
    assert after["phase"] == "awaiting_player"
    assert after["history"] == before["history"]
    assert len(session_requests) == 2
    assert mock_page.evaluate("() => window.__theaterAudioClears") == 0


@pytest.mark.frontend
def test_theater_capsule_ignores_pointer_restore_superseded_by_launch(
    mock_page: Page,
    running_server: str,
):
    """启动指针的迟到快照不能覆盖选剧页随后启动的新 Session。"""  # noqa: DOCSTRING_CJK

    pending_pointer: dict[str, Route] = {}

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/session-a"):
            pending_pointer["route"] = route
            return
        if path.endswith("/api/theater-numeric/session/session-b"):
            fulfill(
                route,
                _snapshot(revision=0, story_id="story-b", session_id="session-b"),
            )
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script(
        """window.sessionStorage.setItem(
            'neko.theater.numeric.v2.capsule-pointer.v1',
            JSON.stringify({story_id: 'story-a', session_id: 'session-a'})
        );
        window.localStorage.setItem('neko_tutorial_settings', 'seen');"""
    )
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    for _ in range(50):
        if "route" in pending_pointer:
            break
        mock_page.wait_for_timeout(20)
    assert "route" in pending_pointer

    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
            launch_id: 'pointer-superseding-launch', launch_action: 'continue',
            story_id: 'story-b', session_id: 'session-b', revision: 0
        }, window.location.origin)"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().sessionId === 'session-b'"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    fulfill(
        pending_pointer["route"],
        _snapshot(revision=0, story_id="story-a", session_id="session-a"),
    )
    mock_page.wait_for_timeout(200)

    state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    pointer = mock_page.evaluate(
        "() => JSON.parse(window.sessionStorage.getItem('neko.theater.numeric.v2.capsule-pointer.v1'))"
    )
    assert state["storyId"] == "story-b"
    assert state["sessionId"] == "session-b"
    assert state["phase"] == "awaiting_player"
    assert pointer == {"story_id": "story-b", "session_id": "session-b"}


@pytest.mark.frontend
def test_theater_capsule_does_not_restore_persisted_pointer_after_app_restart(
    mock_page: Page,
    running_server: str,
):
    """旧版长期指针不能让完整退出后的新程序自动进入小剧场。"""  # noqa: DOCSTRING_CJK

    session_requests: list[str] = []

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if "/api/theater-numeric/session/" in path:
            session_requests.append(path)
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script(
        """window.localStorage.setItem(
            'neko.theater.numeric.v2.capsule-pointer.v1',
            JSON.stringify({story_id: 'old-story', session_id: 'old-session'})
        );
        window.localStorage.setItem('neko_tutorial_settings', 'seen');"""
    )
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.wait_for_timeout(200)

    state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert state["active"] is False
    assert state["phase"] == "inactive"
    assert session_requests == []


@pytest.mark.frontend
def test_desktop_pet_relays_restart_to_the_visible_compact_chat_runtime(
    mock_page: Page,
    running_server: str,
):
    """桌面本体收到重新开始后，应由独立胶囊窗口唯一播放并显示开场。"""  # noqa: DOCSTRING_CJK

    speak_blocks: list[int] = []

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            fulfill(route, _snapshot(revision=0))
            return
        if path.endswith("/api/theater-numeric/session/speak-block"):
            body = json.loads(request.post_data or "{}")
            speak_blocks.append(body["block_index"])
            fulfill(route, {"ok": True, "audio_queued": False})
            return
        route.continue_()

    context = mock_page.context
    context.route("**/api/theater-numeric/**", handler)
    context.add_init_script(
        """(() => {
            const originalUserAgent = window.navigator.userAgent;
            Object.defineProperty(window.navigator, 'userAgent', {
                configurable: true,
                get: () => originalUserAgent + ' Electron'
            });
            window.localStorage.setItem('neko_tutorial_settings', 'seen');
        })();"""
    )
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    chat_page = context.new_page()
    try:
        chat_page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
        mock_page.wait_for_function("() => window.nekoTheaterRuntime")
        chat_page.wait_for_function(
            "() => window.nekoTheaterRuntime && window.reactChatWindowHost"
        )
        chat_page.evaluate("() => window.reactChatWindowHost.openWindow()")

        mock_page.evaluate(
            """() => window.postMessage({
                schema: 'neko.theater.interpage.v1',
                action: 'theater:launch-request',
                launch_id: 'desktop-restart-relay',
                launch_action: 'restart',
                story_id: 'capsule-browser-story',
                session_id: 'capsule-browser-session',
                revision: 0
            }, window.location.origin)"""
        )

        chat_page.wait_for_function(
            "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
            timeout=10000,
        )
        history = chat_page.locator(".compact-export-history-anchor")
        expect(history).to_contain_text("雨落在花店檐角。")
        expect(history).to_contain_text("你终于回来了。")
        assert mock_page.evaluate(
            "() => window.nekoTheaterRuntime.getState().active"
        ) is False
        assert speak_blocks == [1]
    finally:
        chat_page.close()


@pytest.mark.frontend
def test_theater_capsule_ignores_end_confirmation_after_session_switch(
    mock_page: Page,
    running_server: str,
):
    """旧 Session 的确认框迟到后，不能结束确认期间切入的新 Session。"""  # noqa: DOCSTRING_CJK

    end_requests: list[dict] = []

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/session-a"):
            fulfill(route, _snapshot(revision=0, story_id="story-a", session_id="session-a"))
            return
        if path.endswith("/api/theater-numeric/session/session-b"):
            fulfill(route, _snapshot(revision=0, story_id="story-b", session_id="session-b"))
            return
        if path.endswith("/api/theater-numeric/session/end"):
            end_requests.append(json.loads(request.post_data or "{}"))
            fulfill(route, {"ok": False, "reason": "unexpected_end_request"})
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
                launch_id: 'end-confirm-a', launch_action: 'continue',
                story_id: 'story-a', session_id: 'session-a', revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().sessionId === 'session-a'"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    mock_page.evaluate(
        """() => {
            window.__endSelectorOpenCalls = 0;
            window.openOrFocusWindow = () => {
                window.__endSelectorOpenCalls += 1;
                return null;
            };
            window.showConfirm = (_message, _title, options) => new Promise((resolve) => {
                window.__resolveOldEnd = () => {
                    if (typeof options.onResolve === 'function') options.onResolve(true);
                    resolve(true);
                };
            });
            window.__oldEndResult = null;
            window.nekoTheaterRuntime.requestEnd().then((value) => {
                window.__oldEndResult = value;
            });
        }"""
    )
    mock_page.wait_for_function("() => typeof window.__resolveOldEnd === 'function'")

    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1', action: 'theater:launch-request',
            launch_id: 'end-confirm-b', launch_action: 'continue',
            story_id: 'story-b', session_id: 'session-b', revision: 0
        }, window.location.origin)"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().sessionId === 'session-b'"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'"
    )
    mock_page.evaluate("() => window.__resolveOldEnd()")
    mock_page.wait_for_function("() => window.__oldEndResult === false")

    state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert state["storyId"] == "story-b"
    assert state["sessionId"] == "session-b"
    assert state["phase"] == "awaiting_player"
    assert end_requests == []
    assert mock_page.evaluate("() => window.__endSelectorOpenCalls") == 0


@pytest.mark.frontend
def test_theater_capsule_rebuilds_committed_history_after_idempotent_retry(
    mock_page: Page,
    running_server: str,
):
    """提交成功但响应丢失时，重试快照必须补回已提交的猫娘回复。"""  # noqa: DOCSTRING_CJK

    input_ids: list[str] = []
    first_response_lost = {"value": False}
    turn = {
        "revision": 1,
        "input_text": "检查那封旧信",
        "performance": "（按住信角）纸张没有受潮，封口也还完整。",
        "suggested_inputs": ["问她信是谁留下的"],
    }

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(payload, ensure_ascii=False),
        )

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            fulfill(route, _snapshot(revision=0))
            return
        if path.endswith("/api/theater-numeric/session/speak-block"):
            fulfill(route, {"ok": True, "audio_queued": False})
            return
        if path.endswith("/api/theater-numeric/session/input"):
            body = json.loads(request.post_data or "{}")
            input_ids.append(body["client_turn_id"])
            if not first_response_lost["value"]:
                first_response_lost["value"] = True
                route.abort("connectionreset")
                return
            payload = _snapshot(revision=1, performance_history=[turn])
            payload["idempotent_replay"] = True
            fulfill(route, payload)
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
        " && typeof window.sendTextPayload === 'function'"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1',
                action: 'theater:launch-request',
                launch_id: 'capsule-idempotent-launch',
                launch_action: 'continue',
                story_id: 'capsule-browser-story',
                session_id: 'capsule-browser-session',
                revision: 0
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )

    # Startup translation hydration can briefly return an object; errors still
    # have to cross the React presentation contract as a string.
    mock_page.evaluate("""() => {
        const translate = window.t;
        window.t = (key, ...args) => key === 'theater.inputFailed' ? {} : translate(key, ...args);
    }""")
    mock_page.evaluate(
        "() => window.nekoTheaterRuntime.handleComposerSubmit('检查那封旧信')"
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )
    mock_page.evaluate(
        "() => window.nekoTheaterRuntime.handleComposerSubmit('检查那封旧信')"
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().revision === 1"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )

    history = mock_page.locator(".compact-export-history-anchor")
    expect(history.get_by_text("检查那封旧信", exact=True)).to_have_count(1)
    expect(history).to_contain_text("纸张没有受潮，封口也还完整。")
    assert len(input_ids) == 2
    assert input_ids[0] == input_ids[1]


@pytest.mark.frontend
@pytest.mark.parametrize("page_path", ["/", "/chat"])
def test_theater_capsule_keeps_chat_draft_and_speaks_dialogue_only(
    mock_page: Page,
    running_server: str,
    page_path: str,
):
    speak_blocks: list[int] = []
    speak_groups: list[list[int]] = []
    input_messages: list[str] = []
    end_messages: list[dict] = []
    end_fails = {"value": False}

    # 本例从正常聊天开始；完成首次引导，避免教程或人格选择遮挡剧场交互。
    mock_page.route("**/api/seven-day-tutorial/state", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({
            "success": True, "initialized": True, "revision": 1,
            "state": {"completedRounds": list(range(1, 8))},
        }),
    ))
    mock_page.route("**/api/characters/persona-onboarding-state", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps({
            "success": True, "state": {"status": "completed"},
        }),
    ))

    def fulfill(route: Route, payload: dict) -> None:
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            fulfill(route, _snapshot(revision=0))
            return
        if path.endswith("/api/theater-numeric/session/speak-block"):
            body = json.loads(request.post_data or "{}")
            speak_blocks.append(body["block_index"])
            speak_groups.append(body["dialogue_block_indexes"])
            fulfill(route, {"ok": True, "audio_queued": False, "block_index": body["block_index"]})
            return
        if path.endswith("/api/theater-numeric/session/input"):
            body = json.loads(request.post_data or "{}")
            input_messages.append(body["message"])
            turn = {
                "revision": 1,
                "input_text": body["message"],
                "performance": (
                    "（她接过信，指尖停了一瞬）我一直替你收着。"
                    "这封信，我一次也没有拆开过。"
                    "（轻轻压住信角）还有一件事，我也想亲口告诉你。"
                ),
                "suggested_inputs": ["在窗边坐下"],
            }
            payload = _snapshot(revision=1, performance_history=[turn])
            payload["performance"] = turn
            fulfill(route, payload)
            return
        if path.endswith("/api/theater-numeric/session/end"):
            body = json.loads(request.post_data or "{}")
            end_messages.append(body)
            if end_fails["value"]:
                fulfill(route, {"ok": False, "reason": "numeric_end_failed"})
                return
            payload = _snapshot(revision=2)
            payload["session"]["status"] = "ended"
            payload["session"]["ended_reason"] = "user_exit"
            payload["end_receipt_id"] = "theater_end_capsule_browser"
            payload["archive_status"] = "pending"
            fulfill(route, payload)
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}{page_path}", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
        " && window.appButtons && window.appChat && window.appState"
        " && typeof window.sendTextPayload === 'function'"
    )
    # 共享前端夹具的外部资源探测可能返回 502，并残留与本测试无关的模型管理启动闸门；
    # 这里先释放闸门，再打开真实 React 胶囊宿主来验证小剧场桥接。
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
        }"""
    )
    mock_page.evaluate("() => window.reactChatWindowHost.openWindow()")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isMounted && window.reactChatWindowHost.isMounted()"
        " && !!document.querySelector('#react-chat-window-root .app-shell')"
    )
    if mock_page.locator(".composer-input").count() == 0:
        mock_page.locator(".compact-chat-capsule-button").click()
    expect(mock_page.locator(".composer-input")).to_be_visible()
    mock_page.locator(".composer-input").fill("普通聊天草稿")
    mock_page.evaluate(
        """() => window.reactChatWindowHost.setComposerAttachments([{
            id: 'ordinary-chat-image',
            url: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=',
            alt: '普通聊天图片'
        }])"""
    )
    expect(mock_page.locator(".composer-attachment-card")).to_have_count(1)

    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1',
            action: 'theater:launch-request',
            launch_id: 'capsule-browser-launch',
            launch_action: 'start',
            story_id: 'capsule-browser-story',
            session_id: 'capsule-browser-session',
            revision: 0
        }, window.location.origin)"""
    )

    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )
    assert mock_page.evaluate(
        "() => window.nekoTheaterRuntime.getState().ordinaryDraftRestore.text"
    ) == "普通聊天草稿"
    expect(mock_page.locator(".app-shell")).to_have_attribute("data-theater-active", "true")
    expect(mock_page.locator(".compact-export-history-anchor")).to_have_class(
        re.compile(r"\bis-theater-history\b")
    )
    history = mock_page.locator(".compact-export-history-anchor")
    assistant_messages = history.locator(".compact-export-history-message.is-assistant")
    system_messages = history.locator(".compact-export-history-message.is-system")
    expect(assistant_messages).to_have_count(1)
    expect(system_messages).to_have_count(1)
    expect(history).to_contain_text("雨落在花店檐角")
    expect(system_messages.first).to_contain_text("雨落在花店檐角。")
    expect(assistant_messages.first).to_contain_text("你终于回来了。")
    expect(assistant_messages.first.locator(".compact-export-history-author")).to_have_text("小葵")
    expect(system_messages.first).not_to_contain_text("（雨落在花店檐角。）")
    expect(history).not_to_contain_text("普通聊天草稿")
    expect(mock_page.locator(".composer-galgame-option")).to_have_count(2)
    expect(mock_page.locator(".composer-attachment-card")).to_have_count(0)
    expect(mock_page.locator(".composer-input")).to_have_value("")

    mock_page.evaluate(
        """() => {
            const host = window.reactChatWindowHost;
            const original = host.setViewProps.bind(host);
            window.__theaterHistoryStreamingSamples = [];
            host.setViewProps = (props) => {
                const history = props && props.theaterPresentation && props.theaterPresentation.history;
                const entry = Array.isArray(history) ? history[history.length - 1] : null;
                if (entry && entry.status === 'streaming' && entry.type === 'dialogue') {
                    window.__theaterHistoryStreamingSamples.push(entry.text);
                }
                return original(props);
            };
        }"""
    )
    # 直接派发点击事件，避免 Playwright 的可操作性等待吞掉短暂的逐字输出中间态。
    mock_page.locator(".composer-galgame-option").first.dispatch_event("click")
    expect(mock_page.locator(".composer-input")).to_have_value("")
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().revision === 1"
        " && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )

    expect(history).to_contain_text("把旧信递给她")
    expect(history.get_by_text("把旧信递给她", exact=True)).to_have_count(1)
    expect(
        history.locator(".compact-export-history-message.is-user .compact-export-history-author").last
    ).to_have_text("老谢")
    expect(assistant_messages).to_have_count(2)
    expect(assistant_messages.nth(1).locator(".compact-export-history-author")).to_have_text("小葵")
    expect(assistant_messages.nth(1)).to_contain_text("（她接过信，指尖停了一瞬）")
    expect(assistant_messages.nth(1)).to_contain_text("我一直替你收着。")
    expect(assistant_messages.nth(1)).to_contain_text("这封信，我一次也没有拆开过。")
    expect(assistant_messages.nth(1)).to_contain_text("（轻轻压住信角）")
    expect(assistant_messages.nth(1)).to_contain_text("还有一件事，我也想亲口告诉你。")
    expect(system_messages).to_have_count(1)
    streaming_samples = mock_page.evaluate("() => window.__theaterHistoryStreamingSamples")
    visible_samples = [sample for sample in streaming_samples if sample]
    assert len(visible_samples) > 2
    assert len(visible_samples[0]) < len(visible_samples[-1])
    assert all(current.startswith(previous) for previous, current in zip(visible_samples, visible_samples[1:]))
    completed_state = mock_page.evaluate("() => window.nekoTheaterRuntime.getState()")
    assert completed_state["currentBlock"] is None
    assert completed_state["history"][-1]["status"] == "sent"
    assert input_messages == ["把旧信递给她"]
    assert speak_blocks == [1, 1]
    assert speak_groups == [[1], [1, 3]]

    mock_page.evaluate(
        """() => {
            window.__theaterEndConfirmCalls = [];
            window.__theaterSelectorOpenCalls = [];
            window.__theaterSelectorFocusCalls = 0;
            window.__theaterSelectorRestoreCalls = 0;
            window.__theaterSelectorOpenShouldFail = true;
            window.showConfirm = (message, title, options) => {
                window.__theaterEndConfirmCalls.push({ message, title, options });
                return Promise.resolve(false);
            };
            window.openOrFocusWindow = (url, name) => {
                window.__theaterOpenedSelector = { url, name };
                window.__theaterSelectorOpenCalls.push({ url, name });
                return window.__theaterSelectorOpenShouldFail ? null : {
                    closed: false,
                    focus: () => { window.__theaterSelectorFocusCalls += 1; },
                    postMessage: () => {}
                };
            };
            window.requestOpenedWindowRestore = () => {
                window.__theaterSelectorRestoreCalls += 1;
            };
        }"""
    )
    end_button = mock_page.locator(".compact-theater-history-header button")
    expect(end_button).to_have_attribute("data-compact-hit-region-id", "history:theater-end")
    expect(end_button).to_have_css("pointer-events", "auto")
    button_box = end_button.bounding_box()
    viewport = mock_page.viewport_size
    assert button_box is not None and viewport is not None
    assert 0 <= button_box["x"] < viewport["width"], (button_box, viewport)
    assert 0 <= button_box["y"] < viewport["height"], (button_box, viewport)

    # 取消确认必须保留当前 Session，不能清空历史或调用结束接口。
    end_button.click()
    mock_page.wait_for_function("() => window.__theaterEndConfirmCalls.length === 1")
    assert mock_page.evaluate("() => window.nekoTheaterRuntime.getState().active") is True
    assert end_messages == []
    expect(mock_page.locator(".app-shell")).to_have_attribute("data-theater-active", "true")

    end_fails["value"] = True
    mock_page.evaluate(
        """() => {
            window.showConfirm = (message, title, options) => {
                window.__theaterEndConfirmCalls.push({ message, title, options });
                if (typeof options.onResolve === 'function') options.onResolve(true);
                return Promise.resolve(true);
            };
        }"""
    )
    end_button.click()
    expect(history.locator(".compact-theater-history-error")).to_contain_text(
        "当前演绎状态无法结束"
    )
    assert mock_page.evaluate("() => window.nekoTheaterRuntime.getState().active") is True
    expect(mock_page.locator(".app-shell")).to_have_attribute("data-theater-active", "true")
    assert len(mock_page.evaluate("() => window.__theaterSelectorOpenCalls")) == 1

    # 结束请求恢复后再次点击；若剧本页被拦截，保留只读历史和“返回剧本页”重试入口。
    end_fails["value"] = False
    end_button.click()
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().phase === 'ended'")
    expect(history.locator(".compact-theater-history-error")).to_contain_text("剧本页面打开失败")
    expect(end_button).to_contain_text("返回剧本页")
    assert mock_page.evaluate("() => window.nekoTheaterRuntime.getState().active") is True

    mock_page.evaluate("() => { window.__theaterSelectorOpenShouldFail = false; }")
    end_button.click()
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().active === false")
    # 首页恢复 full 宿主时可能移除临时属性，独立聊天宿主则保留 false；
    # 两种 DOM 形态都只需保证剧场样式不再处于 true。
    assert mock_page.locator(".app-shell").get_attribute("data-theater-active") != "true"
    expect(mock_page.locator(".composer-input")).to_have_value("普通聊天草稿")
    expect(mock_page.locator(".composer-attachment-card")).to_have_count(1)
    assert end_messages == [{
        "story_id": "capsule-browser-story",
        "session_id": "capsule-browser-session",
        "base_revision": 1,
        "base_lifecycle_revision": 0,
    }, {
        "story_id": "capsule-browser-story",
        "session_id": "capsule-browser-session",
        "base_revision": 1,
        "base_lifecycle_revision": 0,
    }]
    confirm_calls = mock_page.evaluate("() => window.__theaterEndConfirmCalls")
    assert len(confirm_calls) == 3
    assert confirm_calls[0]["title"] == "结束演绎"
    assert confirm_calls[0]["options"]["cancelText"] == "取消"
    assert confirm_calls[1]["options"]["danger"] is True
    assert confirm_calls[2]["options"]["danger"] is True
    assert mock_page.evaluate("() => window.__theaterOpenedSelector") == {
        "url": "/theater?story_id=capsule-browser-story",
        "name": "neko_theater",
    }
    assert mock_page.evaluate("() => window.__theaterSelectorRestoreCalls") == 1
    assert mock_page.evaluate("() => window.__theaterSelectorFocusCalls") == 1


@pytest.mark.frontend
def test_theater_end_confirm_is_transparent_and_clickable(
    mock_page: Page,
    running_server: str,
):
    """结束确认框必须进入透明桌面窗口的原生命中区域。"""  # noqa: DOCSTRING_CJK

    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && typeof window.showConfirm === 'function'"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
        }"""
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isMounted && window.reactChatWindowHost.isMounted()"
        " && !!document.querySelector('#react-chat-window-root .app-shell')"
    )

    mock_page.evaluate(
        """() => {
            window.__theaterConfirmResult = 'pending';
            window.showConfirm('确定结束当前演绎吗？', '结束演绎', {
                okText: '确认',
                cancelText: '取消',
                danger: true,
                skin: 'theater'
            }).then((value) => {
                window.__theaterConfirmResult = value;
            });
        }"""
    )

    overlay = mock_page.locator(".modal-overlay-theater")
    dialog = mock_page.locator(".modal-dialog-theater")
    expect(overlay).to_be_visible()
    expect(dialog).to_be_visible()
    expect(overlay).to_have_css("background-color", "rgba(0, 0, 0, 0)")
    expect(dialog).to_have_css("min-width", "320px")
    expect(dialog).to_be_focused()
    expect(overlay.locator(".modal-btn-secondary")).to_have_css("border-top-width", "0px")
    dialog_style = dialog.evaluate(
        "element => ({ backgroundColor: getComputedStyle(element).backgroundColor, borderColor: getComputedStyle(element).borderColor, color: getComputedStyle(element).color })"
    )
    assert dialog_style["backgroundColor"] == "rgb(255, 255, 255)"
    assert dialog_style["borderColor"] == "rgba(23, 167, 255, 0.22)"
    assert dialog_style["color"] == "rgb(36, 68, 90)"
    expect(overlay.locator(".modal-btn-danger")).to_have_css("color", "rgb(255, 255, 255)")
    mock_page.evaluate("() => { document.documentElement.dataset.theme = 'dark'; }")
    expect(dialog).to_have_css("background-color", "rgb(255, 255, 255)")
    expect(dialog).to_have_css("color", "rgb(36, 68, 90)")
    expect(overlay.locator(".modal-btn-danger")).to_have_css("background-image", re.compile("linear-gradient"))

    overlay.locator(".modal-btn-secondary").click()
    mock_page.wait_for_function("() => window.__theaterConfirmResult === false")
    expect(overlay).to_have_count(0)


@pytest.mark.frontend
@pytest.mark.parametrize('legacy_actions', [False, True])
def test_theater_capsule_restores_action_and_scene_narration_without_mixing(
    mock_page: Page,
    running_server: str,
    legacy_actions: bool,
):
    """恢复历史时也必须按换场 phase 区分括号动作和独立场景旁白。"""  # noqa: DOCSTRING_CJK

    transition = {
        "revision": 1,
        "input_text": "和她一起去车站",
        "segments": [
            {
                "phase": "source_response",
                "content": [
                    {"type": "narration", "text": "（她收好旧信。）"},
                    {"type": "dialogue", "speaker_id": "active_catgirl", "text": "那就走吧。"},
                ],
            },
            {
                "phase": "transition_bridge",
                "content": [
                    {"type": "narration", "text": "雨停后，两人来到车站。"},
                ],
            },
            {
                "phase": "target_opening",
                "content": [
                    {"type": "narration", "text": "末班车的灯照亮空荡站台。"},
                    {"type": "dialogue", "speaker_id": "active_catgirl", "text": "车票上的日期不对。"},
                ],
            },
        ],
        "suggested_inputs": [],
        "transition_delivered": True,
        "visible_node_id": "mainline_02",
    }
    if legacy_actions:
        transition['segments'][0]['content'][0]['type'] = 'action'
        # A legacy action-only bridge remains an action bubble, even though
        # scene narration in the same phase normally uses a system bubble.
        transition['segments'][1]['content'] = [{'type': 'action', 'text': '她跨过站台的积水。'}]

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _snapshot(revision=1, performance_history=[transition]),
                    ensure_ascii=False,
                ),
            )
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
        " && window.appButtons && window.appChat && window.appState"
        " && typeof window.sendTextPayload === 'function'"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
        }"""
    )
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost.isMounted && window.reactChatWindowHost.isMounted()"
        " && !!document.querySelector('#react-chat-window-root .app-shell')"
    )
    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1',
            action: 'theater:launch-request',
            launch_id: 'capsule-browser-transition-launch',
            launch_action: 'continue',
            story_id: 'capsule-browser-story',
            session_id: 'capsule-browser-session',
            revision: 1
        }, window.location.origin)"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )

    expect(mock_page.locator(".app-shell")).to_have_attribute("data-theater-active", "true")
    history = mock_page.locator(".compact-export-history-anchor")
    expect(history).to_have_class(re.compile(r"\bis-theater-history\b"))
    assistant_messages = history.locator(".compact-export-history-message.is-assistant")
    system_messages = history.locator(".compact-export-history-message.is-system")
    expect(assistant_messages).to_have_count(3)
    expect(system_messages).to_have_count(2 if legacy_actions else 3)
    # 已有括号不能被重复包装，换场桥和目标开场也不能误显示成微动作。
    source_response_message = assistant_messages.nth(1)
    expect(source_response_message).to_contain_text("（她收好旧信。）")
    expect(source_response_message).not_to_contain_text("（（她收好旧信。））")
    if legacy_actions:
        expect(source_response_message).to_contain_text('（她跨过站台的积水。）')
    else:
        expect(system_messages.nth(1)).to_contain_text("雨停后，两人来到车站。")
        expect(system_messages.nth(1)).not_to_contain_text("（雨停后，两人来到车站。）")
    expect(system_messages.last).to_contain_text("末班车的灯照亮空荡站台。")
    expect(system_messages.last).not_to_contain_text("（末班车的灯照亮空荡站台。）")


@pytest.mark.frontend
@pytest.mark.parametrize(
    "bridge_narration",
    ["", "时间向前流转，现场随之转换。"],
    ids=["empty", "legacy-placeholder"],
)
def test_theater_capsule_skips_empty_deduplicated_transition_bridge(
    mock_page: Page,
    running_server: str,
    bridge_narration: str,
):
    """空桥段和旧占位记录都直接进入目标开场，不生成额外系统气泡。"""  # noqa: DOCSTRING_CJK

    transition = {
        "revision": 1,
        "input_text": "“喜欢的话，以后经常做给你吃。”",
        "segments": [
            {
                "phase": "source_response",
                "performance": "（放下餐盘）真的吗……不许骗人家哦。",
            },
            {
                "phase": "transition_bridge",
                "scene_narration": bridge_narration,
            },
            {
                "phase": "target_opening",
                "scene_narration": "周末的客厅里，纸箱占据了大半空间。",
                "performance": "（从纸箱里探头）老谢，这些都是给人家的吗？",
            },
        ],
        "suggested_inputs": [],
        "transition_delivered": True,
        "visible_node_id": "mainline_02",
    }

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/session/capsule-browser-session"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    _snapshot(revision=1, performance_history=[transition]),
                    ensure_ascii=False,
                ),
            )
            return
        route.continue_()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.goto(f"{running_server}/", wait_until="domcontentloaded")
    mock_page.wait_for_function(
        "() => window.reactChatWindowHost && window.nekoTheaterRuntime"
        " && typeof window.sendTextPayload === 'function'"
    )
    mock_page.evaluate(
        """() => {
            window.isMainUIHiddenByModelManager = () => false;
            document.body.classList.remove('neko-main-ui-hidden-by-model-manager');
            window.reactChatWindowHost.openWindow();
            window.postMessage({
                schema: 'neko.theater.interpage.v1',
                action: 'theater:launch-request',
                launch_id: 'capsule-empty-bridge-launch',
                launch_action: 'continue',
                story_id: 'capsule-browser-story',
                session_id: 'capsule-browser-session',
                revision: 1
            }, window.location.origin);
        }"""
    )
    mock_page.wait_for_function(
        "() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'",
        timeout=10000,
    )

    history = mock_page.locator(".compact-export-history-anchor")
    system_messages = history.locator(".compact-export-history-message.is-system")
    expect(system_messages).to_have_count(2)
    expect(history).to_contain_text("周末的客厅里，纸箱占据了大半空间。")
    expect(history).not_to_contain_text("时间向前流转")
    expect(history).not_to_contain_text("现场随之转换")


@pytest.mark.frontend
def test_valid_replacement_launch_retires_previous_end_receipt(mock_page: Page, running_server: str):
    previous = _snapshot(revision=4, story_id="story-a", session_id="session-a")
    previous["session"].update(status="ended", ended_reason="user_exit")
    previous["end_receipt_id"] = "old-receipt"

    def handler(route: Route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/session/session-a"):
            payload = previous
        elif path.endswith("/session/session-b"):
            payload = _snapshot(revision=0, story_id="story-b", session_id="session-b")
        elif path.endswith("/session/missing"):
            route.fulfill(status=404, content_type="application/json", body=json.dumps(
                {"ok": False, "reason": "numeric_session_not_found"},
            ))
            return
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.sessionStorage.setItem('neko.theater.numeric.v2.capsule-pointer.v1', JSON.stringify({story_id:'story-a',session_id:'session-a'}))")
    mock_page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
    mock_page.wait_for_function("() => window.nekoTheaterRuntime?.getState().phase === 'ended'")
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().pendingEnd.end_receipt_id") == "old-receipt"
    with mock_page.expect_response("**/session/missing?*") as missing_response:
        mock_page.evaluate("""() => window.postMessage({schema:'neko.theater.interpage.v1', action:'theater:launch-request',
            launch_id:'missing-launch', story_id:'missing', session_id:'missing', revision:0}, location.origin)""")
    assert missing_response.value.status == 404
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().pendingEnd.end_receipt_id") == "old-receipt"
    mock_page.evaluate("""() => window.postMessage({schema:'neko.theater.interpage.v1', action:'theater:launch-request',
        launch_id:'new-launch', launch_action:'continue', story_id:'story-b', session_id:'session-b', revision:0}, location.origin)""")
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().sessionId === 'session-b' && window.nekoTheaterRuntime.getState().phase === 'awaiting_player'")
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().pendingEnd") is None
    mock_page.evaluate("""() => {
        window.__staleReceipts = [];
        window.addEventListener('message', event => { if(event.data?.action === 'theater:post-end') window.__staleReceipts.push(event.data); });
        window.postMessage({schema:'neko.theater.interpage.v1',action:'theater:selector-ready'},location.origin);
    }""")
    assert mock_page.evaluate("window.__staleReceipts") == []


@pytest.mark.frontend
@pytest.mark.parametrize("completion", ["end", "unavailable", "cancel", "timeout"])
def test_long_dialogue_waits_for_speech_completion(mock_page: Page, running_server: str, completion):
    snapshot = _snapshot(revision=0)
    snapshot["session"]["opening_performance"]["performance"] = "我还有一段很长的话要告诉你。" * 12

    def handler(route: Route):
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/session/capsule-browser-session"):
            payload = snapshot
        elif path.endswith("/session/speak-block"):
            payload = {"ok": True, "speech_id": "long-speech", "audio_queued": True}
        else:
            route.continue_()
            return
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.add_init_script("window.localStorage.setItem('neko_tutorial_settings', 'seen')")
    mock_page.route('**/api/seven-day-tutorial/state', lambda route: route.fulfill(
        json={'success': True, 'initialized': True, 'revision': 1, 'state': {'completedRounds': [1, 2, 3, 4, 5, 6, 7]}}))
    mock_page.route('**/api/characters/persona-onboarding-state', lambda route: route.fulfill(
        json={'success': True, 'state': {'status': 'completed'}}))
    mock_page.goto(f"{running_server}/chat", wait_until="domcontentloaded")
    mock_page.wait_for_function("() => window.nekoTheaterRuntime && window.reactChatWindowHost")
    mock_page.clock.install()
    with mock_page.expect_response("**/session/speak-block"):
        mock_page.evaluate("""() => window.postMessage({
            schema:'neko.theater.interpage.v1', action:'theater:launch-request',
            launch_id:'long-speech-launch', launch_action:'start',
            story_id:'capsule-browser-story', session_id:'capsule-browser-session', revision:0
        }, location.origin)""")
    mock_page.clock.run_for(16000)
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().phase") == "performing"
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().suggestedInputs") == []
    mock_page.evaluate("""() => window.dispatchEvent(new CustomEvent(
        'neko-assistant-speech-end', {detail:{turnId:'unrelated-speech'}}))""")
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().phase") == "performing"
    for kind in ('end', 'cancel', 'unavailable'):
        mock_page.evaluate("""kind => window.dispatchEvent(new CustomEvent(
            'neko-assistant-speech-' + kind, {detail:{reason:'ordinary-session-stop'}}))""", kind)
        mock_page.clock.run_for(100)
        assert mock_page.evaluate("window.nekoTheaterRuntime.getState().phase") == "performing"
    if completion == "timeout":
        # 完成事件丢失时仍有有限兜底，不永久锁住玩家输入。
        mock_page.clock.run_for(100000)
    else:
        mock_page.evaluate("""kind => window.dispatchEvent(new CustomEvent(
            'neko-assistant-speech-' + kind, {detail:{turnId:'long-speech'}}))""", completion)
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player'")
    assert mock_page.evaluate("window.nekoTheaterRuntime.getState().suggestedInputs") == snapshot["suggested_inputs"]


@pytest.mark.frontend
@pytest.mark.parametrize('end_ok', [True, False])
def test_end_response_cannot_close_resumed_same_revision(mock_page: Page, running_server: str, end_ok):
    snapshot = _snapshot(revision=0)
    snapshot['session']['lifecycle_revision'] = 0
    pending = {}

    def handler(route: Route):
        if '/session/end' in route.request.url:
            pending['end'] = route
        elif '/session/capsule-browser-session' in route.request.url:
            route.fulfill(status=200, content_type='application/json', body=json.dumps(snapshot))
        else:
            route.continue_()

    mock_page.route('**/api/theater-numeric/**', handler)
    mock_page.add_init_script("window.sessionStorage.setItem('neko.theater.numeric.v2.capsule-pointer.v1', JSON.stringify({story_id:'capsule-browser-story',session_id:'capsule-browser-session'}))")
    mock_page.goto(f'{running_server}/chat', wait_until='domcontentloaded')
    mock_page.wait_for_function("() => window.nekoTheaterRuntime?.getState().phase === 'awaiting_player'")
    with mock_page.expect_request('**/session/end'):
        mock_page.evaluate("""() => {
            window.openOrFocusWindow = () => null;
            window.showConfirm = () => Promise.resolve(true);
            window.__endResult = null;
            window.nekoTheaterRuntime.requestEnd().then(value => window.__endResult = value);
        }""")
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().phase === 'ending'")
    snapshot['session']['lifecycle_revision'] = 2
    mock_page.evaluate("""() => window.postMessage({schema:'neko.theater.interpage.v1',action:'theater:launch-request',
        launch_id:'resumed-same-session',launch_action:'continue', story_id:'capsule-browser-story',
        session_id:'capsule-browser-session',revision:0},location.origin)""")
    mock_page.wait_for_function("() => window.nekoTheaterRuntime.getState().phase === 'awaiting_player' && window.nekoTheaterRuntime.getState().lifecycleRevision === 2")
    pending['end'].fulfill(status=200, content_type='application/json', body=json.dumps({
        'ok': end_ok, 'session': {**snapshot['session'], 'status': 'ended', 'lifecycle_revision': 1},
        'end_receipt_id': 'stale-receipt', 'archive_status': 'pending'}))
    mock_page.wait_for_function('() => window.__endResult === false')
    state = mock_page.evaluate('window.nekoTheaterRuntime.getState()')
    assert state['active'] and state['phase'] == 'awaiting_player'
    assert state['lifecycleRevision'] == 2 and state['pendingEnd'] is None
