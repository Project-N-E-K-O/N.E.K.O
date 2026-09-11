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
        route.continue_()

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
        route.continue_()

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
        route.continue_()

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
        route.continue_()

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
        route.continue_()

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
        route.continue_()

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
        route.continue_()

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
        route.continue_()

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
