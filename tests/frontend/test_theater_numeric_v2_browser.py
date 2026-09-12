"""用真实剧本选择页脚本验收迁移后的核心交互。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json

import pytest
from playwright.sync_api import Page, Route, expect


STORY = {
    "story_id": "numeric_browser_story",
    "title": "雨巷来信",
    "author": "N.E.K.O",
    "language": "zh-CN",
    "revision": 3,
    "display_intro": {
        "background": "多年后，你回到雨季小镇，一封没有寄出的信仍放在旧花店里。",
        "player_identity": "你是回乡整理旧屋的故人。",
        "catgirl_identity": "小葵是守着花店和旧信的店主。",
    },
}
CHARACTER_ID = "numeric-browser-character"


def _fulfill(route: Route, payload: dict, status: int = 200) -> None:
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload, ensure_ascii=False))


def _install_selector_routes(
    page: Page,
    *,
    session: dict | None = None,
    deleted: dict[str, bool] | None = None,
    start_calls: list[dict] | None = None,
    start_result: dict | None = None,
    resume_result: dict | None = None,
    end_calls: list[dict] | None = None,
    end_result: dict | None = None,
) -> None:
    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            stories = [] if deleted and deleted["value"] else [STORY]
            _fulfill(
                route,
                {"ok": True, "stories": stories, "character_id": CHARACTER_ID},
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            if session is None:
                _fulfill(route, {"ok": False, "reason": "numeric_session_not_found"}, 404)
            else:
                _fulfill(route, {"ok": True, "session": session, "archive_status": "written"})
            return
        if path.endswith("/api/theater-numeric/session/start"):
            if start_calls is not None:
                start_calls.append(json.loads(request.post_data or "{}"))
            if start_result is None:
                _fulfill(route, {"ok": False, "reason": "unexpected_start"}, 409)
            else:
                _fulfill(route, start_result)
            return
        if path.endswith("/api/theater-numeric/session/resume"):
            if resume_result is None:
                _fulfill(route, {"ok": False, "reason": "unexpected_resume"}, 409)
            else:
                _fulfill(route, resume_result)
            return
        if path.endswith("/api/theater-numeric/session/end"):
            if end_calls is not None:
                end_calls.append(json.loads(request.post_data or "{}"))
            if end_result is None:
                _fulfill(route, {"ok": False, "reason": "unexpected_end"}, 409)
            else:
                _fulfill(route, end_result)
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": True, "archives": []})
            return
        if path.endswith("/delete-preview"):
            _fulfill(route, {"ok": True, "active_catgirl_names": ["小葵"], "session_count": 1})
            return
        if request.method == "DELETE" and path.endswith("/packages/" + STORY["story_id"]):
            if deleted is not None:
                deleted["value"] = True
            _fulfill(route, {"ok": True, "deleted_session_count": 1})
            return
        route.fallback()

    page.route("**/api/theater-numeric/**", handler)


@pytest.mark.frontend
def test_selector_shows_story_summary_roles_and_new_session_actions(mock_page: Page, running_server: str):
    _install_selector_routes(mock_page)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    expect(mock_page.locator(".theater-story-card")).to_have_count(1)
    expect(mock_page.locator("#theater-empty-state")).to_be_hidden()
    expect(mock_page.locator("#theater-detail-placeholder")).to_be_hidden()
    expect(mock_page.locator("#theater-detail-title")).to_have_text("雨巷来信")
    expect(mock_page.locator("#theater-detail-background")).to_contain_text("没有寄出的信")
    expect(mock_page.locator("#theater-detail-player")).to_contain_text("回乡整理旧屋")
    expect(mock_page.locator("#theater-detail-catgirl")).to_contain_text("小葵")
    expect(mock_page.locator("#theater-start-btn")).to_be_enabled()
    # 预算已固定，选择页不再提供档位控件。
    expect(mock_page.locator("#theater-token-budget")).to_have_count(0)
    expect(mock_page.locator("#theater-start-btn")).to_have_text("开始")
    expect(mock_page.locator("#theater-start-btn")).to_have_attribute("data-i18n", "theater.start")
    expect(mock_page.locator("#theater-continue-btn")).to_be_disabled()
    expect(mock_page.locator("#theater-session-hint")).to_contain_text("点击“开始”")
    expect(mock_page.locator("#theater-restart-btn")).to_have_count(0)
    expect(mock_page.locator("#theater-delete-btn")).to_be_enabled()
    actions_box = mock_page.locator(".theater-detail-actions").bounding_box()
    intro_box = mock_page.locator(".theater-intro-background").bounding_box()
    assert actions_box is not None and intro_box is not None
    assert actions_box["y"] < intro_box["y"]


@pytest.mark.frontend
def test_selector_recovers_status_when_story_detail_request_loses_network(
    mock_page: Page,
    running_server: str,
):
    """切换剧本时网络中断必须退出读取态并显示可重试错误。"""  # noqa: DOCSTRING_CJK

    second_story = {
        **STORY,
        "story_id": "numeric_browser_story_network_failure",
        "title": "断线后的舞台",
    }

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {
                    "ok": True,
                    "stories": [STORY, second_story],
                    "character_id": CHARACTER_ID,
                },
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            if second_story["story_id"] in request.url:
                route.abort("failed")
            else:
                _fulfill(
                    route,
                    {"ok": False, "reason": "numeric_session_not_found"},
                    404,
                )
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": True, "archives": []})
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    expect(mock_page.locator("#theater-selector-status")).to_have_text("就绪")

    mock_page.locator(".theater-story-card").filter(has_text="断线后的舞台").click()

    expect(mock_page.locator("#theater-selector-status")).to_have_text("出错了")
    expect(mock_page.locator("#theater-inline-feedback")).to_contain_text(
        "演绎进度读取失败"
    )


@pytest.mark.frontend
def test_selector_reports_archive_list_failure_instead_of_empty_history(
    mock_page: Page,
    running_server: str,
):
    """归档接口失败必须进入可重试错误态，不能发布就绪和空记录。"""  # noqa: DOCSTRING_CJK

    def handler(route: Route) -> None:
        path = route.request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {"ok": True, "stories": [STORY], "character_id": CHARACTER_ID},
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            _fulfill(
                route,
                {"ok": False, "reason": "numeric_session_not_found"},
                404,
            )
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": False, "reason": "numeric_archive_read_failed"}, 500)
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    expect(mock_page.locator("#theater-selector-status")).to_have_text("出错了")
    expect(mock_page.locator("#theater-inline-feedback")).to_contain_text(
        "演绎进度读取失败"
    )


@pytest.mark.frontend
def test_selector_can_pin_and_forget_saved_theater_memory(mock_page: Page, running_server: str):
    """选剧页用最小记录列表提供收藏与显式忘记入口。"""  # noqa: DOCSTRING_CJK

    forgotten = {"value": False}
    pinned = {"value": False}

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {"ok": True, "stories": [STORY], "character_id": CHARACTER_ID},
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            _fulfill(route, {"ok": False, "reason": "numeric_session_not_found"}, 404)
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            archives = [] if forgotten["value"] else [{
                "story_id": STORY["story_id"],
                "session_id": "saved-session",
                "revision": 8,
                "episode_status": "completed",
                "ending_title": "雨停之后",
                "pinned": pinned["value"],
            }]
            _fulfill(route, {"ok": True, "archives": archives})
            return
        if path.endswith("/api/theater-numeric/memory/archive/pin"):
            pinned["value"] = True
            _fulfill(route, {"ok": True, "archive": {"session_id": "saved-session", "pinned": True}})
            return
        if path.endswith("/api/theater-numeric/memory/forget"):
            forgotten["value"] = True
            _fulfill(route, {"ok": True, "removed_archives": 1})
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    expect(mock_page.locator(".theater-memory-row")).to_have_count(1)
    expect(mock_page.locator(".theater-memory-row strong")).to_have_text("雨停之后")
    with mock_page.expect_request("**/api/theater-numeric/memory/archive/pin"):
        mock_page.locator(".theater-memory-pin").click()
    expect(mock_page.locator(".theater-memory-pin")).to_have_text("取消收藏")

    mock_page.locator("#theater-forget-memory-btn").click()
    expect(mock_page.locator("#theater-modal-title")).to_have_text("忘记该剧本？")
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("不会删除剧本或当前进度")
    with mock_page.expect_request("**/api/theater-numeric/memory/forget"):
        mock_page.locator("#theater-modal-confirm").click()
    expect(mock_page.locator(".theater-memory-row")).to_have_count(0)
    expect(mock_page.locator("#theater-inline-feedback")).to_contain_text("已忘记")


@pytest.mark.frontend
def test_selector_opens_identity_checked_performance_archive(
    mock_page: Page,
    running_server: str,
):
    """已保存记录应通过详情接口打开完整公开演绎。"""  # noqa: DOCSTRING_CJK

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {"ok": True, "stories": [STORY], "character_id": CHARACTER_ID},
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            _fulfill(
                route,
                {"ok": False, "reason": "numeric_session_not_found"},
                404,
            )
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": True, "archives": [{
                "story_id": STORY["story_id"],
                "story_title": STORY["title"],
                "session_id": "saved-session",
                "revision": 2,
                "episode_status": "completed",
                "ending_title": "雨停之后",
                "pinned": False,
            }]})
            return
        if path.endswith("/api/theater-numeric/memory/archive"):
            _fulfill(route, {"ok": True, "archive": {
                "story_id": STORY["story_id"],
                "story_title": STORY["title"],
                "session_id": "saved-session",
                "player_name": "你",
                "catgirl_name": "小葵",
                "opening": {"performance": "（推开花店的门）你回来了。"},
                "turns": [{
                    "revision": 1,
                    "player_input": "我来取那封信。",
                    "performance": "（把信递过来）一直替你留着。雨停后，两人走到街角。",
                    "parts": [
                        {"kind": "action", "phase": "source_response", "text": "（把信递过来）"},
                        {"kind": "dialogue", "phase": "source_response", "text": "一直替你留着。"},
                        {"kind": "scene_narration", "phase": "transition_bridge", "text": "雨停后，两人走到街角。"},
                    ],
                }],
                "ending": {"title": "雨停之后", "summary": "两人终于说开旧事。"},
            }})
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    with mock_page.expect_request("**/api/theater-numeric/memory/archive?**"):
        mock_page.locator(".theater-memory-view").click()

    expect(mock_page.locator("#theater-modal-title")).to_have_text(STORY["title"])
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("我来取那封信")
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("小葵：一直替你留着")
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("雨停后，两人走到街角")
    expect(mock_page.locator("#theater-modal-body")).not_to_contain_text(
        "小葵：雨停后，两人走到街角"
    )
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("雨停之后")
    expect(mock_page.locator("#theater-modal-cancel")).to_be_hidden()
    expect(mock_page.locator("#theater-modal-confirm")).to_have_text("关闭")
    mock_page.locator("#theater-modal-confirm").click()
    expect(mock_page.locator("#theater-modal")).to_be_hidden()


@pytest.mark.frontend
@pytest.mark.parametrize("status", ["active", "ended"])
def test_upgraded_save_offers_cleanup_without_continuation(mock_page: Page, running_server: str, status):
    _install_selector_routes(mock_page, session={
        "session_id": "old-package", "revision": 4, "status": status,
        "ended_reason": "user_exit" if status == "ended" else "",
        "continuation_allowed": False,
    })
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    expect(mock_page.locator("#theater-continue-btn")).to_be_disabled()
    expect(mock_page.locator("#theater-session-hint")).to_contain_text("剧本已更新")
    if status == "active":
        expect(mock_page.locator("#theater-end-btn")).to_be_enabled()
    else:
        expect(mock_page.locator("#theater-start-btn")).to_be_enabled()


@pytest.mark.frontend
@pytest.mark.parametrize("operation", ["start", "input"])
def test_transport_keeps_slow_valid_requests_alive(mock_page: Page, running_server: str, operation):
    _install_selector_routes(mock_page)
    mock_page.clock.install()
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    mock_page.evaluate("""operation => {
        window.__requestAborted = false;
        const originalFetch = window.fetch.bind(window);
        const target = '/api/theater-numeric/session/' + operation;
        window.fetch = (url, options) => url !== target ? originalFetch(url, options) : new Promise((resolve, reject) => {
            window.__finishRequest = () => resolve({ status: 200, json: async () => ({ ok: true }) });
            options.signal.addEventListener('abort', () => { window.__requestAborted = true; reject(new Error('aborted')); });
        });
        window.nekoTheaterTransport.requestJson('/api/theater-numeric/session/' + operation, { method: 'POST', body: {} })
            .then(result => { window.__requestResult = result; }).catch(() => {});
    }""", operation)
    mock_page.wait_for_function("typeof window.__finishRequest === 'function'")
    mock_page.clock.fast_forward(90000)
    assert mock_page.evaluate("window.__requestAborted") is False
    mock_page.evaluate("window.__finishRequest()")
    mock_page.wait_for_function("window.__requestResult && window.__requestResult.ok")


@pytest.mark.frontend
def test_active_story_only_enables_continue(mock_page: Page, running_server: str):
    _install_selector_routes(
        mock_page,
        session={"session_id": "active-session", "revision": 4, "status": "active", "actor_budget_profile": "quality"},
    )
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    expect(mock_page.locator("#theater-start-btn")).to_be_disabled()
    expect(mock_page.locator("#theater-start-btn")).to_have_text("重新开始")
    expect(mock_page.locator("#theater-start-btn")).to_have_attribute("data-i18n", "theater.restartSession")
    expect(mock_page.locator("#theater-continue-btn")).to_be_enabled()
    expect(mock_page.locator("#theater-end-btn")).to_be_enabled()
    expect(mock_page.locator("#theater-end-btn")).to_have_text("结束演绎")
    expect(mock_page.locator("#theater-session-hint")).to_contain_text("点击“继续”")
    expect(mock_page.locator("#theater-restart-btn")).to_have_count(0)
    # 旧 Session 的兼容字段不能重新显示预算选择器。
    expect(mock_page.locator("#theater-token-budget")).to_have_count(0)


@pytest.mark.frontend
def test_selector_can_end_active_story_when_capsule_button_is_unavailable(
    mock_page: Page,
    running_server: str,
):
    end_calls: list[dict] = []
    _install_selector_routes(
        mock_page,
        session={
            "session_id": "active-session",
            "story_package_id": STORY["story_id"],
            "revision": 4,
            "lifecycle_revision": 0,
            "status": "active",
        },
        end_calls=end_calls,
        end_result={
            "ok": True,
            "session": {
                "session_id": "active-session",
                "story_package_id": STORY["story_id"],
                "revision": 5,
                "lifecycle_revision": 1,
                "status": "ended",
                "ended_reason": "user_exit",
            },
            "end_receipt_id": "selector-end-receipt",
            "archive_request_id": "selector-archive-request",
            "archive_status": "pending",
        },
    )
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    mock_page.locator("#theater-end-btn").click()
    expect(mock_page.locator("#theater-modal-title")).to_have_text("结束演绎")
    mock_page.locator("#theater-modal-cancel").click()
    expect(mock_page.locator("#theater-end-btn")).to_be_visible()

    mock_page.locator("#theater-end-btn").click()
    with mock_page.expect_request("**/api/theater-numeric/session/end") as request_info:
        mock_page.locator("#theater-modal-confirm").click()
    assert json.loads(request_info.value.post_data or "{}") == {
        "story_id": STORY["story_id"],
        "session_id": "active-session",
        "base_revision": 4,
        "base_lifecycle_revision": 0,
    }
    expect(mock_page.locator("#theater-modal-title")).to_contain_text("记下本次演绎内容")
    mock_page.locator("#theater-modal-cancel").click()
    expect(mock_page.locator("#theater-end-btn")).to_be_hidden()
    expect(mock_page.locator("#theater-session-badge")).to_have_text("已退出")
    expect(mock_page.locator("#theater-session-hint")).to_contain_text("继续原进度")
    assert end_calls == [{
        "story_id": STORY["story_id"],
        "session_id": "active-session",
        "base_revision": 4,
        "base_lifecycle_revision": 0,
    }]


@pytest.mark.frontend
def test_user_exit_story_can_continue_same_session(mock_page: Page, running_server: str):
    _install_selector_routes(
        mock_page,
        session={
            "session_id": "paused-session",
            "revision": 4,
            "lifecycle_revision": 1,
            "status": "ended",
            "ended_reason": "user_exit",
            "actor_budget_profile": "economy",
        },
        resume_result={
            "ok": True,
            "resumed": True,
            "session": {
                "session_id": "paused-session",
                "story_package_id": STORY["story_id"],
                "revision": 4,
                "lifecycle_revision": 2,
                "status": "active",
                "ended_reason": None,
            },
        },
    )
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    expect(mock_page.locator("#theater-start-btn")).to_be_enabled()
    expect(mock_page.locator("#theater-start-btn")).to_have_text("重新开始")
    expect(mock_page.locator("#theater-continue-btn")).to_be_enabled()
    expect(mock_page.locator("#theater-session-badge")).to_have_text("已退出")
    expect(mock_page.locator("#theater-session-hint")).to_contain_text("继续原进度")
    expect(mock_page.locator("#theater-token-budget")).to_have_count(0)
    with mock_page.expect_request("**/api/theater-numeric/session/resume") as request_info:
        mock_page.locator("#theater-continue-btn").click()
    assert json.loads(request_info.value.post_data or "{}") == {
        "story_id": STORY["story_id"],
        "session_id": "paused-session",
        "base_revision": 4,
        "base_lifecycle_revision": 1,
    }


@pytest.mark.frontend
def test_ended_story_start_replaces_session_after_confirmation(mock_page: Page, running_server: str):
    _install_selector_routes(
        mock_page,
        session={"session_id": "ended-session", "revision": 4, "status": "ended"},
        start_result={
            "ok": True,
            "session": {
                "session_id": "replacement-session",
                "story_package_id": STORY["story_id"],
                "revision": 0,
                "status": "active",
            },
        },
    )
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    expect(mock_page.locator("#theater-start-btn")).to_be_enabled()
    expect(mock_page.locator("#theater-start-btn")).to_have_text("重新开始")
    expect(mock_page.locator("#theater-continue-btn")).to_be_disabled()
    expect(mock_page.locator("#theater-token-budget")).to_have_count(0)
    mock_page.locator("#theater-start-btn").click()

    expect(mock_page.locator("#theater-modal")).to_be_visible()
    expect(mock_page.locator("#theater-modal-title")).to_have_text("开始新的演绎？")
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("替换当前角色的已结束记录")
    expect(mock_page.locator(".theater-modal")).to_be_focused()
    assert mock_page.locator("#theater-modal-title").get_attribute("tabindex") is None
    expect(mock_page.locator("#theater-modal-cancel")).to_have_css("border-top-width", "0px")
    expect(mock_page.locator("#theater-modal-confirm")).to_have_css("color", "rgb(255, 255, 255)")
    mock_page.locator("#theater-modal-cancel").click()
    expect(mock_page.locator("#theater-start-btn")).to_be_focused()
    mock_page.locator("#theater-start-btn").click()
    with mock_page.expect_request("**/api/theater-numeric/session/start") as request_info:
        mock_page.locator("#theater-modal-confirm").click()

    start_payload = json.loads(request_info.value.post_data or "{}")
    assert start_payload["story_id"] == STORY["story_id"]
    assert start_payload["character_id"] == CHARACTER_ID
    assert start_payload["replace_existing"] is True
    assert "actor_budget_profile" not in start_payload
    assert start_payload["session_id"] != "ended-session"


@pytest.mark.frontend
def test_selector_queues_post_end_memory_prompt_behind_open_confirmation(
    mock_page: Page,
    running_server: str,
):
    """结束回执到达时不能覆盖用户尚未选择的重新开始确认框。"""  # noqa: DOCSTRING_CJK

    start_calls: list[dict] = []
    skip_calls: list[dict] = []
    archive_pending = {"value": False}
    ended_session = {
        "session_id": "ended-session",
        "revision": 4,
        "status": "ended",
    }

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {"ok": True, "stories": [STORY], "character_id": CHARACTER_ID},
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            payload = {"ok": True, "session": ended_session}
            if archive_pending["value"]:
                payload.update({
                    "end_receipt_id": "queued-memory-receipt",
                    "archive_request_id": "queued-memory-request",
                    "archive_status": "pending",
                })
            else:
                payload["archive_status"] = "written"
            _fulfill(route, payload)
            return
        if path.endswith("/api/theater-numeric/session/start"):
            start_calls.append(json.loads(request.post_data or "{}"))
            _fulfill(route, {"ok": False, "reason": "expected_test_stop"}, 409)
            return
        if path.endswith("/api/theater-numeric/session/archive/skip"):
            skip_calls.append(json.loads(request.post_data or "{}"))
            _fulfill(route, {"ok": True, "status": "skipped"})
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": True, "archives": []})
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-modal-title")).to_have_text("开始新的演绎？")

    archive_pending["value"] = True
    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1',
            action: 'theater:post-end',
            story_id: 'numeric_browser_story',
            session_id: 'ended-session',
            revision: 4,
            end_receipt_id: 'queued-memory-receipt'
        }, window.location.origin)"""
    )
    expect(mock_page.locator("#theater-modal-title")).to_have_text("开始新的演绎？")

    mock_page.locator("#theater-modal-confirm").click()
    mock_page.wait_for_function("() => document.querySelector('#theater-inline-feedback').textContent.includes('启动演出失败')")
    assert len(start_calls) == 1
    expect(mock_page.locator("#theater-modal-title")).to_contain_text("记下本次演绎内容")
    mock_page.locator("#theater-modal-cancel").click()
    expect(mock_page.locator("#theater-modal")).to_be_hidden()
    assert len(skip_calls) == 1


@pytest.mark.frontend
def test_selector_drops_restart_confirmation_after_story_switch(
    mock_page: Page,
    running_server: str,
):
    """A 剧本确认框等待期间切到 B 后，确认旧弹窗不能重新开始 B。"""  # noqa: DOCSTRING_CJK

    second_story = {
        **STORY,
        "story_id": "numeric_browser_story_b",
        "title": "雾港回声",
    }
    start_calls: list[dict] = []
    skip_calls: list[dict] = []

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {
                    "ok": True,
                    "stories": [STORY, second_story],
                    "character_id": CHARACTER_ID,
                },
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            is_second = "story_id=numeric_browser_story_b" in request.url
            session_id = "ended-session-b" if is_second else "ended-session-a"
            payload = {
                "ok": True,
                "session": {
                    "session_id": session_id,
                    "revision": 4,
                    "status": "ended",
                },
                "archive_status": "written",
            }
            if is_second:
                payload.update({
                    "end_receipt_id": "story-b-memory-receipt",
                    "archive_request_id": "story-b-memory-request",
                    "archive_status": "pending",
                })
            _fulfill(route, payload)
            return
        if path.endswith("/api/theater-numeric/session/start"):
            start_calls.append(json.loads(request.post_data or "{}"))
            _fulfill(route, {"ok": False, "reason": "unexpected_start"}, 409)
            return
        if path.endswith("/api/theater-numeric/session/archive/skip"):
            skip_calls.append(json.loads(request.post_data or "{}"))
            _fulfill(route, {"ok": True, "status": "skipped"})
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": True, "archives": []})
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    mock_page.locator("#theater-start-btn").click()
    expect(mock_page.locator("#theater-modal-title")).to_have_text("开始新的演绎？")

    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1',
            action: 'theater:post-end',
            story_id: 'numeric_browser_story_b',
            session_id: 'ended-session-b',
            revision: 4,
            end_receipt_id: 'story-b-memory-receipt'
        }, window.location.origin)"""
    )
    expect(mock_page.locator("#theater-detail-title")).to_have_text("雾港回声")
    expect(mock_page.locator("#theater-modal-title")).to_have_text("开始新的演绎？")

    mock_page.locator("#theater-modal-confirm").click()
    expect(mock_page.locator("#theater-modal-title")).to_contain_text("记下本次演绎内容")
    assert start_calls == []
    mock_page.locator("#theater-modal-cancel").click()
    expect(mock_page.locator("#theater-modal")).to_be_hidden()
    assert len(skip_calls) == 1


@pytest.mark.frontend
def test_delete_story_warns_about_active_character_and_removes_card(mock_page: Page, running_server: str):
    deleted = {"value": False}
    _install_selector_routes(mock_page, deleted=deleted)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")

    mock_page.locator("#theater-delete-btn").click()
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("小葵")
    expect(mock_page.locator("#theater-modal-body")).to_contain_text("还未结束")
    mock_page.locator("#theater-modal-confirm").click()

    expect(mock_page.locator(".theater-story-card")).to_have_count(0)
    expect(mock_page.locator("#theater-selector-status")).to_contain_text("剧本已删除")
    assert deleted["value"] is True


@pytest.mark.frontend
def test_post_end_receipt_prompts_memory_on_selector_and_archives_once(
    mock_page: Page,
    running_server: str,
):
    archive_calls: list[dict] = []
    archive_status = {"value": "written"}
    ended_session = {
        "session_id": "ended-session",
        "revision": 5,
        "status": "ended",
        "ended_reason": "user_exit",
    }

    def handler(route: Route) -> None:
        request = route.request
        path = request.url.split("?", 1)[0]
        if path.endswith("/api/theater-numeric/stories"):
            _fulfill(
                route,
                {"ok": True, "stories": [STORY], "character_id": CHARACTER_ID},
            )
            return
        if path.endswith("/api/theater-numeric/session/active"):
            _fulfill(route, {
                "ok": True,
                "session": ended_session,
                "end_receipt_id": "theater_end_browser_receipt",
                "archive_request_id": "theater_archive_browser_receipt",
                "archive_status": archive_status["value"],
            })
            return
        if path.endswith("/api/theater-numeric/memory/archives"):
            _fulfill(route, {"ok": True, "archives": []})
            return
        if path.endswith("/api/theater-numeric/session/archive"):
            archive_calls.append(json.loads(request.post_data or "{}"))
            _fulfill(route, {"ok": True, "status": "written"})
            return
        route.fallback()

    mock_page.route("**/api/theater-numeric/**", handler)
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    expect(mock_page.locator("#theater-detail-title")).to_have_text("雨巷来信")

    archive_status["value"] = "pending"
    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1',
            action: 'theater:post-end',
            story_id: 'numeric_browser_story',
            session_id: 'ended-session',
            revision: 5,
            end_receipt_id: 'theater_end_browser_receipt'
        }, window.location.origin)"""
    )

    expect(mock_page.locator("#theater-modal")).to_be_visible()
    expect(mock_page.locator("#theater-modal-title")).to_contain_text("记下本次演绎内容")
    expect(mock_page.locator(".theater-modal")).to_be_focused()
    expect(mock_page.locator("#theater-modal-cancel")).to_have_css("border-top-width", "0px")
    mock_page.locator("#theater-modal-confirm").click()

    expect(mock_page.locator("#theater-modal")).to_be_hidden()
    expect(mock_page.locator("#theater-inline-feedback")).to_contain_text("本次演绎已记下")
    assert len(archive_calls) == 1
    assert archive_calls[0]["story_id"] == STORY["story_id"]
    assert archive_calls[0]["session_id"] == "ended-session"
    assert archive_calls[0]["revision"] == 5
    assert archive_calls[0]["end_receipt_id"] == "theater_end_browser_receipt"
    assert archive_calls[0]["archive_request_id"].startswith("theater_archive_")

    archive_status["value"] = "written"
    mock_page.evaluate(
        """() => window.postMessage({
            schema: 'neko.theater.interpage.v1',
            action: 'theater:post-end',
            story_id: 'numeric_browser_story',
            session_id: 'ended-session',
            revision: 5,
            end_receipt_id: 'theater_end_browser_receipt'
        }, window.location.origin)"""
    )
    expect(mock_page.locator("#theater-modal")).to_be_hidden()
    assert len(archive_calls) == 1


@pytest.mark.frontend
def test_selector_end_without_receipt_allows_retrying_pending_forget(mock_page: Page, running_server: str):
    session = {"session_id": "forget-pending", "story_package_id": STORY["story_id"],
               "revision": 4, "lifecycle_revision": 0, "status": "active"}
    _install_selector_routes(mock_page, session=session, end_result={
        "ok": True, "session": {**session, "status": "ended", "ended_reason": "user_exit", "lifecycle_revision": 1},
    })
    mock_page.goto(f"{running_server}/theater", wait_until="domcontentloaded")
    mock_page.locator("#theater-end-btn").click()
    mock_page.locator("#theater-modal-confirm").click()
    expect(mock_page.locator("#theater-modal")).to_be_hidden()
    expect(mock_page.locator("#theater-inline-feedback")).to_contain_text("剧本记忆删除失败，请重试")
    expect(mock_page.locator("#theater-forget-memory-btn")).to_be_enabled()
    mock_page.locator("#theater-forget-memory-btn").click()
    expect(mock_page.locator("#theater-modal-title")).to_have_text("忘记该剧本？")
    mock_page.locator("#theater-modal-cancel").click()
    expect(mock_page.locator("#theater-modal")).to_be_hidden()


@pytest.fixture(autouse=True)
def empty_deleted_story_memories(mock_page: Page):
    mock_page.route('**/api/theater-numeric/memory/stories', lambda route: _fulfill(route, {
        'ok': True, 'stories': [], 'character_id': CHARACTER_ID, 'memory_available': True}))


@pytest.mark.frontend
def test_post_end_received_while_pin_is_busy_is_processed(mock_page: Page, running_server: str):
    ended = {'value': False}
    pending = {}
    skips = []

    def handler(route: Route):
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/session/active'):
            payload = {'ok': True, 'session': {'session_id': 'busy-session', 'revision': 2,
                'status': 'ended' if ended['value'] else 'active'}}
            if ended['value']:
                payload.update(end_receipt_id='busy-receipt', archive_status='pending')
            _fulfill(route, payload)
        elif path.endswith('/memory/archives'):
            _fulfill(route, {'ok': True, 'archives': [{'session_id': 'archive', 'revision': 1}]})
        elif path.endswith('/memory/archive/pin'):
            pending['pin'] = route
        elif path.endswith('/session/archive/skip'):
            skips.append(route.request.post_data)
            _fulfill(route, {'ok': True})
        else:
            route.fallback()

    _install_selector_routes(mock_page)
    mock_page.route('**/api/theater-numeric/**', handler)
    mock_page.goto(f'{running_server}/theater', wait_until='domcontentloaded')
    with mock_page.expect_request('**/memory/archive/pin'):
        mock_page.locator('[data-theater-pin-session]').click()
    ended['value'] = True
    mock_page.evaluate("""() => window.postMessage({schema:'neko.theater.interpage.v1',action:'theater:post-end',
        story_id:'numeric_browser_story',session_id:'busy-session',revision:2,end_receipt_id:'busy-receipt'},location.origin)""")
    expect(mock_page.locator('#theater-import-btn')).to_be_disabled()
    pending['pin'].fulfill(status=200, content_type='application/json', body='{"ok":true}')
    expect(mock_page.locator('#theater-modal-title')).to_contain_text('记下本次演绎内容')
    mock_page.locator('#theater-modal-cancel').click()
    expect(mock_page.locator('#theater-modal')).to_be_hidden()
    assert len(skips) == 1


@pytest.mark.frontend
def test_deleted_story_retains_summary_and_forget_retry(mock_page: Page, running_server: str):
    forgotten = {'value': False}
    memory = {**STORY, 'memory_only': True, 'forget_pending': True,
              'memory_summaries': ['我们在星火之后重逢。<script>不得执行</script>']}
    requests = []

    def handler(route: Route):
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/memory/stories'):
            _fulfill(route, {'ok': True, 'character_id': CHARACTER_ID, 'memory_available': True,
                'stories': [] if forgotten['value'] else [memory]})
        elif path.endswith('/memory/forget'):
            requests.append(json.loads(route.request.post_data or '{}'))
            forgotten['value'] = True
            _fulfill(route, {'ok': True})
        else:
            route.fallback()

    _install_selector_routes(mock_page, deleted={'value': True})
    mock_page.route('**/api/theater-numeric/**', handler)
    mock_page.goto(f'{running_server}/theater', wait_until='domcontentloaded')
    expect(mock_page.locator('#theater-session-badge')).to_have_text('已删除 · 记忆管理')
    expect(mock_page.locator('#theater-memory-list')).to_contain_text(memory['memory_summaries'][0])
    expect(mock_page.locator('#theater-start-btn')).to_be_disabled()
    expect(mock_page.locator('#theater-delete-btn')).to_be_disabled()
    expect(mock_page.locator('#theater-forget-memory-btn')).to_be_enabled()
    mock_page.locator('#theater-forget-memory-btn').click()
    mock_page.locator('#theater-modal-confirm').click()
    expect(mock_page.locator('#theater-empty-state')).to_be_visible()
    assert requests == [{'story_id': STORY['story_id'], 'character_id': CHARACTER_ID}]


@pytest.mark.frontend
@pytest.mark.parametrize('scenario', ['url', 'delete', 'manual_selection'])
def test_memory_target_survives_async_loading_unless_player_selects(mock_page: Page, running_server: str, scenario):
    deleted = {'value': scenario != 'delete'}
    memory = {**STORY, 'memory_only': True, 'memory_summaries': ['保留下来的公开摘要。']}
    other = {**STORY, 'story_id': 'other_story', 'title': '另一份剧本'}
    third = {**STORY, 'story_id': 'third_story', 'title': '第三份剧本'}
    pending = {}

    def handler(route: Route):
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/theater-numeric/stories'):
            _fulfill(route, {'ok': True, 'character_id': CHARACTER_ID,
                'stories': [other, third] if deleted['value'] else [STORY, other, third]})
        elif path.endswith('/memory/stories'):
            if deleted['value']:
                pending['memory'] = route
            else:
                _fulfill(route, {'ok': True, 'character_id': CHARACTER_ID, 'stories': []})
        else:
            route.fallback()

    _install_selector_routes(mock_page, deleted=deleted)
    mock_page.route('**/api/theater-numeric/**', handler)
    with mock_page.expect_request('**/memory/stories'):
        mock_page.goto(f'{running_server}/theater?story_id={STORY["story_id"]}', wait_until='domcontentloaded')
    if scenario == 'delete':
        mock_page.locator('#theater-delete-btn').click()
        with mock_page.expect_request('**/memory/stories'):
            mock_page.locator('#theater-modal-confirm').click()
    expect(mock_page.locator('#theater-detail-title')).to_have_text(other['title'])
    if scenario == 'manual_selection':
        # A -> B -> A still means the player has made an explicit selection.
        mock_page.locator('[data-story-id="third_story"]').click()
        expect(mock_page.locator('#theater-detail-title')).to_have_text(third['title'])
        mock_page.locator('[data-story-id="other_story"]').click()
        expect(mock_page.locator('#theater-detail-title')).to_have_text(other['title'])
    _fulfill(pending['memory'], {'ok': True, 'character_id': CHARACTER_ID,
        'memory_available': True, 'stories': [memory]})
    expect(mock_page.locator('.theater-story-card')).to_have_count(3)
    if scenario == 'manual_selection':
        expect(mock_page.locator('#theater-detail-title')).to_have_text(other['title'])
        expect(mock_page.locator('#theater-start-btn')).to_be_enabled()
    else:
        expect(mock_page.locator('#theater-session-badge')).to_have_text('已删除 · 记忆管理')
        expect(mock_page.locator('#theater-memory-list')).to_contain_text(memory['memory_summaries'][0])
        expect(mock_page.locator('#theater-start-btn')).to_be_disabled()
        expect(mock_page).to_have_url(f'{running_server}/theater?story_id={STORY["story_id"]}')


@pytest.mark.frontend
@pytest.mark.parametrize('delayed_stage', ['active', 'archives'])
@pytest.mark.parametrize('failed_response', [False, True])
def test_returning_to_story_ignores_first_selection_response(mock_page: Page, running_server: str, delayed_stage, failed_response):
    other = {**STORY, 'story_id': 'other_story', 'title': '另一份剧本'}
    counts = {'active': 0, 'archives': 0}
    pending = {}
    fresh = {'ok': True, 'session': {'session_id': 'fresh-session', 'revision': 3, 'status': 'active'}}

    def handler(route: Route):
        path = route.request.url.split('?', 1)[0]
        if path.endswith('/theater-numeric/stories'):
            _fulfill(route, {'ok': True, 'character_id': CHARACTER_ID, 'stories': [STORY, other]})
        elif STORY['story_id'] in route.request.url and (
            path.endswith('/session/active') or path.endswith('/memory/archives')
        ):
            stage = 'active' if path.endswith('/session/active') else 'archives'
            counts[stage] += 1
            if stage == delayed_stage and counts[stage] == 1:
                pending['route'] = route
            elif stage == 'active':
                _fulfill(route, fresh)
            else:
                _fulfill(route, {'ok': True, 'archives': [{'session_id': 'fresh-archive', 'revision': 3}]})
        else:
            route.fallback()

    _install_selector_routes(mock_page)
    mock_page.route('**/api/theater-numeric/**', handler)
    endpoint = 'session/active' if delayed_stage == 'active' else 'memory/archives'
    with mock_page.expect_request(f'**/{endpoint}?story_id={STORY["story_id"]}'):
        mock_page.goto(f'{running_server}/theater', wait_until='domcontentloaded')
    mock_page.locator('[data-story-id="other_story"]').click()
    expect(mock_page.locator('#theater-detail-title')).to_have_text(other['title'])
    mock_page.locator(f'[data-story-id="{STORY["story_id"]}"]').click()
    expect(mock_page.locator('[data-theater-view-session="fresh-archive"]')).to_be_visible()
    delayed_request = pending['route'].request
    with mock_page.expect_event(
        'requestfailed' if failed_response else 'requestfinished',
        predicate=lambda request: request == delayed_request,
    ):
        if failed_response:
            pending['route'].abort('failed')
        elif delayed_stage == 'active':
            _fulfill(pending['route'], {'ok': True, 'session': {'session_id': 'stale-session', 'revision': 1,
                'status': 'ended'}, 'end_receipt_id': 'stale-receipt', 'archive_status': 'pending'})
        else:
            _fulfill(pending['route'], {'ok': True, 'archives': [{'session_id': 'stale-archive', 'revision': 1}]})
    # Wait for the exact delayed request to finish before draining UI work.
    mock_page.evaluate('() => new Promise(resolve => requestAnimationFrame(resolve))')
    expect(mock_page.locator('[data-theater-view-session="fresh-archive"]')).to_be_visible()
    expect(mock_page.locator('#theater-modal')).to_be_hidden()
    expect(mock_page.locator('#theater-inline-feedback')).not_to_contain_text('读取失败')
    expect(mock_page.locator('#theater-continue-btn')).to_be_enabled()
