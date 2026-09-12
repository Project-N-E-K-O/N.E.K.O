"""验证 Numeric v2 HTTP 纵向链路和失败不提交边界。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import gc
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from main_routers import numeric_theater_router
from memory.recent import CompressedRecentHistoryManager
from services.theater.numeric_v2_actor import NumericV2ActorError
from services.theater.numeric_v2_archive import (
    NumericV2ArchiveError,
    NumericV2ArchiveStore,
    build_numeric_v2_memory_messages,
    build_numeric_v2_public_archive,
)
from services.theater.numeric_v2_evaluator import (
    NumericV2EvaluationResult,
    NumericV2EvaluatorError,
    NumericV2TransitionOfferReview,
)
from services.theater.numeric_v2_registry import NumericV2PackageError
from services.theater.numeric_v2_runtime import MetricChangeV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_1_story, numeric_v2_story
from utils.cloudsave_runtime import MaintenanceModeError
from utils.llm_client import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    THEATER_MEMORY_SOURCE,
    convert_to_messages,
    messages_to_dict,
)
from utils.llm_client.history import SQLChatMessageHistory
from utils.llm_client.messages import _normalize_messages


def test_numeric_v2_router_idle_request_locks_are_reclaimed():
    """请求锁只覆盖并发执行窗口，完成后不能随幂等请求 ID 无限增长。"""  # noqa: DOCSTRING_CJK

    request_id = "router_lock_reclaim_test"
    lock = numeric_theater_router._request_lock(
        numeric_theater_router._speak_request_locks,
        request_id,
    )
    assert numeric_theater_router._request_lock(
        numeric_theater_router._speak_request_locks,
        request_id,
    ) is lock

    # 调用方不再持有锁时，弱引用表应自行删除空闲请求条目。
    del lock
    gc.collect()
    assert request_id not in numeric_theater_router._speak_request_locks


def test_numeric_v2_public_performance_hides_route_identifiers():
    """公开演绎只保留展示内容，内部节点和路线标识不能送到浏览器。"""  # noqa: DOCSTRING_CJK

    projected = numeric_theater_router._public_performance({
        "from_node_id": "start",
        "to_node_id": "branch_secret",
        "visible_node_id": "branch_secret",
        "performance": "（收好地图）我们继续走。",
        "suggested_inputs": ["继续前进"],
        "suggestion_candidates": [{
            "text": "继续前进",
            "purpose": "advance",
            "goal_id": "secret_goal",
        }],
    })

    assert projected == {
        "performance": "（收好地图）我们继续走。",
        "suggested_inputs": ["继续前进"],
    }


def test_llm_role_dict_normalization_strips_internal_metadata():
    """角色字典发送到模型供应商前必须剥离 N.E.K.O 内部元数据。"""  # noqa: DOCSTRING_CJK
    normalized = _normalize_messages([{
        "role": "user",
        "content": "继续。",
        "metadata": {"source": THEATER_MEMORY_SOURCE},
    }])

    assert normalized == [{"role": "user", "content": "继续。"}]


@pytest.mark.asyncio
async def test_numeric_v2_router_uses_cloudsave_write_fence(tmp_path, monkeypatch):
    """剧场写操作必须在云存档维护态命中共享写栅栏。"""  # noqa: DOCSTRING_CJK
    calls = []

    def _blocked(_config_manager, *, operation: str, target: str):
        calls.append((operation, target))
        raise MaintenanceModeError(
            "applying_snapshot",
            operation=operation,
            target=target,
        )

    monkeypatch.setattr(numeric_theater_router, "assert_cloudsave_writable", _blocked)

    with pytest.raises(MaintenanceModeError):
        await numeric_theater_router._assert_numeric_writable(
            _ConfigManager(tmp_path),
            "sessions",
        )

    assert calls == [("save", "theater/numeric_v2/sessions")]


def test_numeric_v2_story_import_holds_story_lifecycle_lock(tmp_path, monkeypatch):
    """独占写入剧本包时必须与同 ID 删除事务串行。"""  # noqa: DOCSTRING_CJK

    story_guard_depth = {"value": 0}
    import_lock_states = []
    original_guard = numeric_theater_router.numeric_v2_story_session_guard
    original_import = numeric_theater_router.NumericV2PackageRegistry.import_package

    @asynccontextmanager
    async def tracked_story_guard(theater_root, story_id):
        async with original_guard(theater_root, story_id):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    def tracked_import(registry, payload):
        if payload["meta"]["story_id"] == "numeric_import_lock_guard":
            import_lock_states.append(story_guard_depth["value"] > 0)
        return original_import(registry, payload)

    monkeypatch.setattr(
        numeric_theater_router,
        "numeric_v2_story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2PackageRegistry,
        "import_package",
        tracked_import,
    )
    story = numeric_v2_story()
    story["meta"]["story_id"] = "numeric_import_lock_guard"
    client = _client(tmp_path, monkeypatch)

    with client:
        imported = client.post(
            "/api/theater-numeric/packages/import",
            json=story,
        )

    assert imported.status_code == 200
    assert import_lock_states == [True]


def test_numeric_v2_story_import_requires_v22_upgrade(tmp_path, monkeypatch):
    """导入只接受 v2.2；旧包不会再通过查询参数或原合同绕过门禁。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    legacy = numeric_v2_story()
    legacy["meta"].update({"story_id": "legacy_requires_upgrade", "contract_version": "v2.1"})
    current = numeric_v2_story()
    current["meta"]["story_id"] = "v22_import_ok"

    with client:
        rejected = client.post(
            "/api/theater-numeric/packages/import",
            json=legacy,
        )
        accepted = client.post(
            "/api/theater-numeric/packages/import",
            json=current,
        )

    assert rejected.status_code == 422
    assert rejected.json()["reason"] == "numeric_v2_upgrade_required"
    assert accepted.status_code == 200
    assert accepted.json()["package"]["contract_version"] == "v2.2"


def test_numeric_v2_story_list_reuses_compiled_summary_intro():
    """列表投影只能消费注册表已经编译的摘要，不能为显示简介再次加载整包。"""  # noqa: DOCSTRING_CJK

    class _SummaryOnlyRegistry:
        def list_packages(self):
            # 故意不提供 load_engine；若列表实现退回二次加载，本测试会直接失败。
            return [{
                "story_id": "summary_only_story",
                "title": "摘要剧本",
                "intro": {
                    "background": "林舟在门口遇见小岚。",
                    "player_identity": "林舟，刚到这里的男主。",
                    "catgirl_identity": "小岚，守在门口的猫娘。",
                },
            }]

    stories = numeric_theater_router._list_story_summaries(
        _SummaryOnlyRegistry(),
        {"catgirl_name": "测试猫娘", "player_address": "哥哥"},
    )

    assert stories[0]["display_intro"]["background"] == "你在门口遇见测试猫娘。"


class _ConfigManager:
    def __init__(self, root: Path):
        self.app_docs_dir = root
        self.config_dir = root / "config"
        self.local_state_dir = root.parent / (root.name + "-local-state")

    def ensure_local_state_directory(self):
        self.local_state_dir.mkdir(parents=True, exist_ok=True)
        return True

    def load_characters(self, *, require_authoritative=False) -> dict:
        return {
            "当前猫娘": "测试猫娘",
            "猫娘": {"测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。")},
            "主人": {"昵称": "哥哥"},
        }

    def load_root_state(self) -> dict:
        # 路由测试默认处于可写态；维护态行为由云存档栅栏测试单独覆盖。
        return {"mode": "normal"}


def _catgirl_profile(name: str, personality: str) -> dict:
    token = "2" if name == "新猫娘" else "1"
    return {
        "昵称": name,
        "人格": personality,
        "_reserved": {
            "character_id": f"character_{token * 32}",
        },
    }


def _performance(text: str, *, opening: bool = False) -> dict:
    if opening:
        return {
            "scene_narration": "风铃轻轻响了一声。",
            "performance": text,
            "suggested_inputs": ["继续听她说"],
        }
    return {
        "performance": f"（风铃轻轻响了一声）{text}",
        "suggested_inputs": ["继续听她说"],
    }


def _client(
    tmp_path: Path,
    monkeypatch,
    config_manager=None,
    *,
    opening_text="你回来了。",
    player_address_known=True,
) -> TestClient:
    packages = tmp_path / "theater" / "numeric_v2" / "packages"
    packages.mkdir(parents=True)
    (packages / "numeric_v2_contract.json").write_text(
        json.dumps(
            numeric_v2_story(player_address_known=player_address_known),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manager = config_manager or _ConfigManager(tmp_path)
    monkeypatch.setattr(numeric_theater_router, "get_config_manager", lambda: manager)
    monkeypatch.setattr(numeric_theater_router, "_validate_local_mutation_request", lambda *args, **kwargs: None)

    async def opening(*args, **kwargs):
        return _performance(opening_text, opening=True)

    async def turn(*args, **kwargs):
        return _performance("我在听。")

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(metric_changes=(), scene_complete=False)

    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_opening", opening)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    app = FastAPI()
    app.include_router(numeric_theater_router.router)

    class _NumericV2TestClient(TestClient):
        def post(self, url, *args, **kwargs):
            payload = kwargs.get("json")
            if (
                url == "/api/theater-numeric/session/start"
                and isinstance(payload, dict)
                and "character_id" not in payload
            ):
                # 路由回归默认模拟新版选剧页；需要测试旧/错角色令牌时由用例显式传入。
                payload = dict(payload)
                payload["character_id"] = numeric_theater_router._current_catgirl_binding(
                    manager
                )["character_id"]
                kwargs["json"] = payload
            return super().post(url, *args, **kwargs)

    return _NumericV2TestClient(app)


def test_numeric_v2_router_projects_unknown_player_as_second_person(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch, player_address_known=False)
    with client:
        listed = client.get("/api/theater-numeric/stories")
        assert listed.status_code == 200
        assert listed.json()["stories"][0]["display_intro"]["player_identity"].startswith("你，")

        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "unknown_address"},
        )
        body = started.json()
        assert started.status_code == 200
        assert started.json()["session"]["lifecycle_revision"] == 0
        assert body["story_intro"]["player_identity"].startswith("你，")
        assert body["participants"]["player_name"] == "你"


def test_numeric_v2_interaction_intent_reaches_actor_but_not_persisted(
    tmp_path,
    monkeypatch,
):
    """Interaction intent serves only the current Actor call and must not enter Session or Ledger state."""

    captured: dict[str, str] = {}
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="chat",
        )

    async def turn(*args, **kwargs):
        captured["interaction_intent"] = str(kwargs.get("interaction_intent"))
        captured["input_source"] = str(kwargs.get("input_source"))
        return {
            "performance": "（轻轻点头）我也有一点紧张。",
            "suggested_inputs": ["你最担心什么？", "我们先聊点别的。"],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "interaction_intent"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "interaction_intent",
                "client_turn_id": "interaction_intent_1",
                "base_revision": 0,
                "message": "你现在是不是有点害怕？",
            },
        )

    assert started.status_code == 200
    assert submitted.status_code == 200
    assert captured["interaction_intent"] == "chat"
    assert captured["input_source"] == "freeform"
    session_path = (
        tmp_path
        / "theater"
        / "numeric_v2"
        / "sessions"
        / "interaction_intent.json"
    )
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    assert "interaction_intent" not in persisted["session"]
    assert "interaction_intent" not in persisted["ledger_events"][0]
    assert persisted["ledger_events"][0]["input_text"] == "你现在是不是有点害怕？"


def test_numeric_v2_suggested_input_bypasses_chat_pacing_only_when_current(
    tmp_path,
    monkeypatch,
):
    """Suggestion clicks follow public choices; forged or stale suggestions cannot change Actor pacing through the source field."""

    captured: dict[str, str] = {}
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="chat",
        )

    async def turn(*args, **kwargs):
        captured["interaction_intent"] = str(kwargs.get("interaction_intent"))
        captured["input_source"] = str(kwargs.get("input_source"))
        return {
            "performance": "（轻轻点头）我知道了，那就照这个选择继续。",
            "suggested_inputs": [
                "（向前一步）我想继续看看。",
                "（停在原地）我想先缓一缓。",
            ],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "suggested_source"},
        )
        suggestion = started.json()["suggested_inputs"][0]
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "suggested_source",
                "client_turn_id": "suggested_source_1",
                "base_revision": 0,
                "message": suggestion,
                "input_source": "suggestion",
            },
        )
        invalid = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "suggested_source",
                "client_turn_id": "suggested_source_invalid",
                "base_revision": 1,
                "message": "（闭上眼睛）这不是当前推荐。",
                "input_source": "suggestion",
            },
        )

    assert started.status_code == 200
    assert submitted.status_code == 200
    assert captured == {
        "interaction_intent": "mixed_or_unclear",
        "input_source": "suggestion",
    }
    assert invalid.status_code == 409
    assert invalid.json()["reason"] == "numeric_suggested_input_not_current"


def test_numeric_v2_scene_complete_does_not_trigger_second_actor_call(
    tmp_path,
    monkeypatch,
):
    """Natural closure enters the base Actor prompt without generating a proposal afterward."""

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=True,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": "（看着你盖好毯子）安静一点，别吵到我。",
            "suggested_inputs": ["（闭上眼睛）晚安。", "我再坐一会儿。"],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        offered = kwargs["actor_performance"].get("transition_offered") is True
        return NumericV2TransitionOfferReview(
            offer_present=offered,
            valid=offered,
            body_violations=(),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "natural_closure"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "natural_closure",
                "client_turn_id": "natural_closure_1",
                "base_revision": 0,
                "message": "（盖好毯子闭上眼睛）今晚就先休息吧。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 1
    body = submitted.json()
    assert body["resolved_turn"]["route_changed"] is False
    assert body["performance"]["transition_offered"] is False
    assert body["performance"]["performance"] == "（看着你盖好毯子）安静一点，别吵到我。"
    persisted = json.loads(
        (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "sessions"
            / "natural_closure.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["session"]["current_node_id"] == "start"
    assert persisted["session"]["transition_offered"] is False


@pytest.mark.parametrize("failure_reason", ["", "当前只有观察结果，还没有新的离幕提议。"])
def test_numeric_v2_overdue_turns_do_not_trigger_second_actor_call(
    tmp_path,
    monkeypatch,
    failure_reason,
):
    """Exceeding recommended turns changes only base Actor pacing, without a later focus-generation call."""

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": "（把旧信放回桌面）眼前的事已经处理好了。",
            "suggested_inputs": [
                "（看向店门）接下来呢？",
                "（留在原地）我再想想。",
            ],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        candidate = kwargs["actor_performance"]
        offered = (
            candidate.get("transition_offered") is True
            and "一起沿长街离开花店吗" in str(candidate.get("performance") or "")
        )
        return NumericV2TransitionOfferReview(
            offer_present=offered,
            valid=offered,
            body_violations=(),
            unsafe_suggestion_indexes=(),
            failure_reason=failure_reason,
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "overdue_closure"},
        )
        submitted = None
        for revision in range(5):
            submitted = client.post(
                "/api/theater-numeric/session/input",
                json={
                    "story_id": "numeric_v2_contract",
                    "session_id": "overdue_closure",
                    "client_turn_id": f"overdue_closure_{revision + 1}",
                    "base_revision": revision,
                    "message": f"（整理桌面）继续处理眼前的事，第 {revision + 1} 次。",
                },
            )
            assert submitted.status_code == 200

    assert submitted is not None
    assert actor_calls == 5
    assert submitted.json()["performance"]["transition_offered"] is False
    assert submitted.json()["suggested_inputs"][0] == "（看向店门）接下来呢？"
    assert submitted.json()["resolved_turn"]["route_changed"] is False
    persisted = json.loads(
        (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "sessions"
            / "overdue_closure.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["session"]["current_node_id"] == "start"
    assert persisted["session"]["transition_offered"] is False


def test_numeric_v2_drops_reviewed_invalid_transition_suggestions(
    tmp_path,
    monkeypatch,
):
    """Suggestions with confirmed conflicts must not remain clickable dead ends even if not registered as offers."""

    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        return {
            "performance": "（收好终端）现有线索只能确认到这里。",
            "suggested_inputs": [
                "（冲向未知出口）我们直接离开这里。",
                "（放弃当前证据）先去别的地方。",
            ],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="推荐离开当前地点，与下一阶段继续现场备份明确冲突。",
            body_violations=(),
            unsafe_suggestion_indexes=(0, 1),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "invalid_suggestions"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "invalid_suggestions",
                "client_turn_id": "invalid_suggestions_1",
                "base_revision": 0,
                "message": "我们先确认现有线索。",
            },
        )

    assert submitted.status_code == 200
    assert submitted.json()["performance"]["performance"] == "（收好终端）现有线索只能确认到这里。"
    assert submitted.json()["suggested_inputs"] == []


def test_numeric_v2_filters_unsafe_future_suggestions_without_body_rereview(
    tmp_path,
    monkeypatch,
):
    """Suggestion violations remove only the affected buttons, without reviewing the body again or refilling after review."""

    actor_calls = 0
    review_calls = 0
    refill_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": "（看向玩家）当前结果已经清楚了。",
            "suggested_inputs": ["（继续当前动作）我来处理。"],
            "transition_offered": False,
        }

    async def refill(*args, **kwargs):
        nonlocal refill_calls
        refill_calls += 1
        return ["（留在原地）我再确认一下。", "（摇头）先不处理。"]

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        candidate = kwargs["actor_performance"]
        original_suggestion = "（继续当前动作）我来处理。" in (
            candidate.get("suggested_inputs") or []
        )
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            unsafe_suggestion_indexes=(0,) if original_suggestion else (),
            failure_reason="推荐是未来候选，不是已播放场景。" if original_suggestion else "",
            body_violations=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_current_scene_suggestions",
        refill,
        raising=False,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "suggestion_scene_false_positive"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "suggestion_scene_false_positive",
                "client_turn_id": "suggestion_scene_false_positive_1",
                "base_revision": 0,
                "message": "继续当前互动。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 1
    assert review_calls == 1
    assert refill_calls == 0
    assert submitted.json()["performance"]["performance"] == "（看向玩家）当前结果已经清楚了。"
    assert submitted.json()["suggested_inputs"] == []


def test_numeric_v2_removes_only_reported_unsafe_suggestion(
    tmp_path,
    monkeypatch,
):
    """Structured indices remove unsafe choices without dropping safe siblings or rewriting the body."""

    actor_calls = 0
    review_calls = 0
    bad_suggestion = "（使用未持有的设备）我来检测。"
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": "（看向桌面）目前只能确认这些可见痕迹。",
            "suggested_inputs": [
                "（凑近桌面）我再看看边缘。",
                bad_suggestion,
                "（退后一步）先记录现有结果。",
            ],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        suggestions = kwargs["actor_performance"].get("suggested_inputs") or []
        unsafe = (
            (suggestions.index(bad_suggestion),)
            if bad_suggestion in suggestions
            else ()
        )
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            unsafe_suggestion_indexes=unsafe,
            failure_reason="第二条推荐使用了玩家未持有的设备。" if unsafe else "",
            body_violations=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "indexed_suggestion"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "indexed_suggestion",
                "client_turn_id": "indexed_suggestion_1",
                "base_revision": 0,
                "message": "继续确认当前痕迹。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 1
    assert review_calls == 1
    assert submitted.json()["suggested_inputs"] == [
        "（凑近桌面）我再看看边缘。",
        "（退后一步）先记录现有结果。",
    ]


def test_numeric_v2_reviews_and_filters_target_opening_suggestions(
    tmp_path,
    monkeypatch,
):
    """After formal transition, filter unsafe target-opening suggestions without rewriting the source response or author bridge."""

    bad_suggestion = "（使用未持有的设备）我来检查新场景。"
    client = _client(tmp_path, monkeypatch)
    package_path = (
        tmp_path / "theater" / "numeric_v2" / "packages" / "numeric_v2_contract.json"
    )
    package = json.loads(package_path.read_text(encoding="utf-8"))
    for route in package["nodes"][0]["route_gates"]:
        route["transition_contract"]["bridge_scene_narration"] = "两人走进雨后的长街。"
    package_path.write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            transition_intent=("accept" if kwargs["message"] == "好，我们现在出发。" else "unclear"),
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        outcome = kwargs["outcome"]
        route_changed = (
            outcome.ledger_event["from_node_id"]
            != outcome.ledger_event["to_node_id"]
        )
        if not route_changed:
            return {
                "performance": "（看向门外）要和我一起离开这里吗？",
                "suggested_inputs": [
                    "（点头）好，我们现在出发。",
                    "（摇头）我还想留一会儿。",
                ],
                "transition_offered": True,
            }
        return {
            "segments": [
                {
                    "phase": "source_response",
                    "performance": "（点头）那就走吧。",
                },
                {
                    "phase": "transition_bridge",
                    "scene_narration": "两人走进雨后的长街。",
                },
                {
                    "phase": "target_opening",
                    "scene_narration": "雨停后的长街恢复了安静。",
                    "performance": "（停下脚步）已经到了。",
                },
            ],
            "suggested_inputs": [
                "（观察四周）先看看眼前环境。",
                bad_suggestion,
                "（留在原地）先听她说明情况。",
            ],
            "transition_delivered": True,
            "visible_node_id": "ending_leave",
        }

    async def review(*args, **kwargs):
        candidate = kwargs["actor_performance"]
        suggestions = candidate.get("suggested_inputs") or []
        unsafe = (
            (suggestions.index(bad_suggestion),)
            if bad_suggestion in suggestions
            else ()
        )
        offered = candidate.get("transition_offered") is True
        return NumericV2TransitionOfferReview(
            offer_present=offered,
            valid=offered,
            unsafe_suggestion_indexes=unsafe,
            failure_reason="目标开场推荐使用了未持有物品。" if unsafe else "",
            body_violations=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "route_suggestion_review"},
        )
        offered = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "route_suggestion_review",
                "client_turn_id": "route_suggestion_review_1",
                "base_revision": 0,
                "message": "我们可以离开了吗？",
            },
        )
        advanced = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "route_suggestion_review",
                "client_turn_id": "route_suggestion_review_2",
                "base_revision": 1,
                "message": "好，我们现在出发。",
            },
        )

    assert offered.status_code == 200
    assert advanced.status_code == 200, advanced.text
    assert advanced.json()["resolved_turn"]["route_changed"] is True
    assert advanced.json()["suggested_inputs"] == [
        "（观察四周）先看看眼前环境。",
        "（留在原地）先听她说明情况。",
    ]
    assert advanced.json()["performance"]["segments"][0]["performance"] == "（点头）那就走吧。"


def test_numeric_v2_preserves_safe_body_when_flagged_retry_only_has_invalid_suggestions(
    tmp_path,
    monkeypatch,
):
    """If only invalid suggestions remain after the sole rewrite, retain safe prose instead of failing the entire input turn."""

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": (
                "眼前的工作还没完成，现在就去下一地点吧。"
                if actor_calls == 1
                else "（确认当前结果）这一阶段的变化已经清楚了。"
            ),
            "suggested_inputs": ["（开始不相干的下一步）现在就做。"],
            "transition_offered": True,
        }

    async def review(*args, **kwargs):
        candidate = kwargs["actor_performance"]
        has_suggestions = bool(candidate.get("suggested_inputs"))
        return NumericV2TransitionOfferReview(
            offer_present="现在就去下一地点吧" in candidate["performance"],
            valid=False,
            failure_reason="推荐与下一互动阶段冲突。" if has_suggestions else "",
            body_violations=(),
            unsafe_suggestion_indexes=((0,) if has_suggestions else ()),
        )

    async def refill(*args, **kwargs):
        pytest.fail("Guard 之后不能再调用补推荐")

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_current_scene_suggestions",
        refill,
        raising=False,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "flagged_invalid_suggestions"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "flagged_invalid_suggestions",
                "client_turn_id": "flagged_invalid_suggestions_1",
                "base_revision": 0,
                "message": "继续当前互动。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    assert submitted.json()["performance"]["performance"] == "（确认当前结果）这一阶段的变化已经清楚了。"
    assert submitted.json()["performance"]["transition_offered"] is False
    assert submitted.json()["suggested_inputs"] == []


def test_numeric_v2_confirmed_body_violation_cannot_be_erased_by_later_review_drift(
    tmp_path,
    monkeypatch,
):
    """A body violation identified by the current review requires rewriting; a later random safe judgment must not erase it."""

    actor_calls = 0
    unsafe_body_only_reviews = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 1:
            return {
                "performance": "（查看传感器）已经确认外面有三架侦察机。",
                "suggested_inputs": [
                    "（指向屏幕）它们的具体位置在哪里？",
                    "（留在原地）先确认目前已知信号。",
                ],
                "transition_offered": False,
            }
        return {
            "performance": "（收回视线）目前只能确认外面仍有不明嗡鸣。",
            "suggested_inputs": [
                "（侧耳倾听）我再确认一下声音。",
                "（压低声音）先说说已经知道的情况。",
            ],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        nonlocal unsafe_body_only_reviews
        candidate = kwargs["actor_performance"]
        text = str(candidate.get("performance") or "")
        unsafe_body = "三架侦察机" in text
        suggestions = candidate.get("suggested_inputs") or []
        if unsafe_body and not suggestions:
            # 模拟真实 Qwen 在下一次纯正文复核中漂移成安全；新协议不应再调用到这里。
            unsafe_body_only_reviews += 1
            return NumericV2TransitionOfferReview(
                offer_present=False,
                valid=False,
                unsafe_suggestion_indexes=(),
                body_violations=(),
            )
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="正文提前确认了侦察机数量。" if unsafe_body else "",
            unsafe_suggestion_indexes=(0,) if unsafe_body else (),
            body_violations=("author_boundary",) if unsafe_body else (),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "body_violation_sticky"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "body_violation_sticky",
                "client_turn_id": "body_violation_sticky_1",
                "base_revision": 0,
                "message": "外面的威胁已经确认了吗？",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    assert unsafe_body_only_reviews == 0
    assert submitted.json()["performance"]["performance"] == (
        "（收回视线）目前只能确认外面仍有不明嗡鸣。"
    )


def test_numeric_v2_unflagged_played_result_is_rewritten_once(
    tmp_path,
    monkeypatch,
):
    """Missing Actor offer flags must not let prose that already reaches the next location pass review."""

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 1:
            return {
                "performance": "（推开门）我们已经抵达下一处大厅。",
                "suggested_inputs": ["（查看大厅）观察周围。"],
                "transition_offered": False,
            }
        return {
            "performance": "（扶住门）门外情况还不明确，我们先停在这里。",
            "suggested_inputs": ["（留在门边）先观察。", "（退后一步）暂不出去。"],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        played = "已经抵达" in str(kwargs["actor_performance"].get("performance") or "")
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="正文已经抵达下一地点。" if played else "",
            unsafe_suggestion_indexes=(),
            body_violations=("scene_boundary",) if played else (),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )
    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "unflagged_played_result"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "unflagged_played_result",
                "client_turn_id": "unflagged_played_result_1",
                "base_revision": 0,
                "message": "看看门外。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    assert "已经抵达" not in submitted.json()["performance"]["performance"]


def test_numeric_v2_played_transition_is_rewritten_once(
    tmp_path,
    monkeypatch,
):
    """Judge 确认已经抵达时须走唯一一次 Actor 改写。"""  # noqa: DOCSTRING_CJK

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=True,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 1:
            return {
                "performance": "（走到树下）我们已经到了。",
                "scene_narration": "两人离开教室并抵达树下。",
                "suggested_inputs": ["开始现场核对。"],
                "transition_offered": True,
            }
        return {
            "performance": "（背起书包）那我们现在出发，好吗？",
            "suggested_inputs": ["好，现在出发。"],
            "transition_offered": True,
        }

    async def review(*args, **kwargs):
        candidate = kwargs["actor_performance"]
        if "已经到了" not in str(candidate.get("performance") or ""):
            return NumericV2TransitionOfferReview(
                offer_present=True,
                valid=True,
                unsafe_suggestion_indexes=(),
                body_violations=(),
            )
        return NumericV2TransitionOfferReview(
            offer_present=True,
            valid=False,
            failure_reason="正文已经离开教室并抵达下一场景。",
            unsafe_suggestion_indexes=(),
            body_violations=("scene_boundary", "author_boundary"),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )
    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "played_offer_arbitration"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "played_offer_arbitration",
                "client_turn_id": "played_offer_arbitration_1",
                "base_revision": 0,
                "message": "我们现在出发吧。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    assert "已经到了" not in submitted.json()["performance"]["performance"]


@pytest.mark.parametrize("failure_reason", ["", "正文同时提出了两个互不相容的转场方向。"])
def test_numeric_v2_mixed_route_offer_is_rewritten_once(
    tmp_path,
    monkeypatch,
    failure_reason,
):
    """Judge 拒绝多方向提议后应改写一次，不能锁存错误选择。"""  # noqa: DOCSTRING_CJK

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=True,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 1:
            return {
                "performance": "是去树下，还是先去档案室？",
                "suggested_inputs": ["去档案室。", "去树下。"],
                "transition_offered": True,
            }
        return {
            "performance": "那我们下一步一起去树下，好吗？",
            "suggested_inputs": ["好，一起去树下。", "先等一下。"],
            "transition_offered": True,
        }

    async def review(*args, **kwargs):
        candidate = kwargs["actor_performance"]
        if "档案室" not in str(candidate.get("performance") or ""):
            return NumericV2TransitionOfferReview(
                offer_present=True,
                valid=True,
                unsafe_suggestion_indexes=(),
                body_violations=(),
            )
        return NumericV2TransitionOfferReview(
            offer_present=True,
            valid=False,
            failure_reason=failure_reason,
            unsafe_suggestion_indexes=(),
            body_violations=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )
    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "mixed_offer_arbitration"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "mixed_offer_arbitration",
                "client_turn_id": "mixed_offer_arbitration_1",
                "base_revision": 0,
                "message": "下一步做什么？",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    assert "档案室" not in submitted.json()["performance"]["performance"]
    assert submitted.json()["performance"]["transition_offered"] is True


def test_numeric_v2_filters_only_unsafe_suggestions_after_body_retry(
    tmp_path,
    monkeypatch,
):
    """If only suggestion violations remain after a boundary rewrite, filter by index without resampling or reviewing safe prose again."""

    actor_calls = 0
    review_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 1:
            return {
                "performance": "第一版正文越过作者事实边界。",
                "suggested_inputs": ["（留在原地）继续确认。"],
                "transition_offered": False,
            }
        return {
            "performance": "（收好照片）目前只能确认这是一处模糊痕迹。",
            "suggested_inputs": ["（前往下一场景）现在就去。"],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        candidate = kwargs["actor_performance"]
        text = str(candidate.get("performance") or "")
        suggestions = candidate.get("suggested_inputs") or []
        first_draft = "越过作者事实边界" in text
        retry_suggestion = "（前往下一场景）现在就去。" in suggestions
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="第一版正文包含未授权事实。" if first_draft else "把未来推荐误作已播放场景。" if retry_suggestion else "",
            body_violations=(("author_boundary",) if first_draft else ()),
            unsafe_suggestion_indexes=((0,) if retry_suggestion else ()),
        )

    async def refill(*args, **kwargs):
        pytest.fail("Guard 之后不能再调用补推荐")

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_current_scene_suggestions",
        refill,
        raising=False,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "retry_suggestion_scope"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "retry_suggestion_scope",
                "client_turn_id": "retry_suggestion_scope_1",
                "base_revision": 0,
                "message": "继续查看当前照片。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    # 首稿快速与争议复查各一次，改写稿仅快速复核一次。
    assert review_calls == 3
    assert submitted.json()["performance"]["performance"] == "（收好照片）目前只能确认这是一处模糊痕迹。"
    assert submitted.json()["performance"]["suggested_inputs"] == []


def test_numeric_v2_body_violation_with_unsafe_suggestion_requires_rewrite(
    tmp_path,
    monkeypatch,
):
    """When the same main review finds both body and suggestion violations, rewrite instead of removing buttons and judging again."""

    actor_calls = 0
    review_calls = 0
    unsafe_body_only_reviews = 0
    client = _client(tmp_path, monkeypatch)
    unsafe_performance = "（看向屏幕）你已经按下快门，新照片已经生成。"
    safe_performance = "（站稳在窗边）这个角度可以，我保持这个姿势。"

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 1:
            return {
                "performance": unsafe_performance,
                "suggested_inputs": ["（查看照片）告诉我尚未公开的信息。", "（放下相机）先休息。"],
                "transition_offered": False,
            }
        assert "正文替玩家按下快门并生成照片" in str(kwargs.get("retry_hint") or "")
        return {
            "performance": safe_performance,
            "suggested_inputs": ["（按下快门）拍好了。", "（放下相机）先休息。"],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        nonlocal review_calls, unsafe_body_only_reviews
        review_calls += 1
        candidate = kwargs["actor_performance"]
        unsafe_body = candidate["performance"] == unsafe_performance
        if review_calls == 1:
            # 正文证据与推荐索引分开报告；删除推荐不能解除正文的修稿要求。
            return NumericV2TransitionOfferReview(
                offer_present=False,
                valid=False,
                failure_reason="正文替玩家按下快门并生成照片。第一条推荐索要尚未公开的信息。",
                unsafe_suggestion_indexes=(0,),
                body_violations=("player_action", "author_boundary"),
            )
        if unsafe_body and not candidate.get("suggested_inputs"):
            # 模拟再次只看正文时随机改判安全；该调用不应发生。
            unsafe_body_only_reviews += 1
            unsafe_body = False
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="正文替玩家按下快门并生成照片。" if unsafe_body else "",
            unsafe_suggestion_indexes=(),
            body_violations=("player_action", "author_boundary") if unsafe_body else (),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "post_drop_body_violation"},
        )
        assert started.status_code == 200
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "post_drop_body_violation",
                "client_turn_id": "post_drop_body_violation_1",
                "base_revision": 0,
                "message": "（举起相机对焦）这个角度可以吗？",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    # 首稿快速与争议复查各一次，改写稿仅快速复核一次。
    assert review_calls == 3
    assert unsafe_body_only_reviews == 0
    assert submitted.json()["performance"]["performance"] == safe_performance


@pytest.mark.parametrize("remaining_violation", [None, "scene_boundary", "invalid_offer"])
def test_numeric_v2_scene_update_and_offer_errors_share_one_body_rewrite(
    tmp_path,
    monkeypatch,
    remaining_violation,
):
    """Do not delete narration and judge again; adopt the final draft if the rewrite still has problems, without extra correction attempts."""

    actor_calls = 0
    review_calls = 0
    unsafe_body_only_reviews = 0
    client = _client(tmp_path, monkeypatch)
    unsafe_performance = "（听见快门声）这张照片应该拍好了。"
    unsafe_update = "两人已经带着新照片抵达另一条街。"
    safe_performance = "（站稳在窗边）这个角度可以，我保持这个姿势。"
    original_reason = "scene_update 提前播放了抵达下一地点。"

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        candidate = {
            "performance": unsafe_performance,
            "scene_narration": unsafe_update,
            "suggested_inputs": ["（按下快门）拍好了。", "（放下相机）先休息。"],
            "transition_offered": False,
        }
        assert actor_calls <= 2
        if actor_calls == 2:
            hint = str(kwargs.get("retry_hint") or "")
            assert unsafe_performance not in hint
            assert unsafe_update not in hint
            if remaining_violation is None:
                assert original_reason in hint
                candidate["performance"] = safe_performance
                candidate.pop("scene_narration")
            elif remaining_violation == "invalid_offer":
                candidate["performance"] = "去尚未确认的另一个地点吧。"
                candidate.pop("scene_narration")
                candidate["transition_offered"] = True
        return candidate

    async def review(*args, **kwargs):
        nonlocal review_calls, unsafe_body_only_reviews
        review_calls += 1
        assert review_calls <= 3
        candidate = kwargs["actor_performance"]
        if review_calls == 1:
            assert candidate["scene_narration"] == unsafe_update
        unsafe = candidate["performance"] == unsafe_performance
        if unsafe and not candidate.get("suggested_inputs"):
            # 模拟对原文再次做纯正文复核时随机放行；新链路禁止走到这里。
            unsafe_body_only_reviews += 1
            unsafe = False
        if unsafe and remaining_violation and actor_calls == 1:
            return NumericV2TransitionOfferReview(
                offer_present=False,
                valid=False,
                failure_reason="正文先播放了未经授权的结果。",
                unsafe_suggestion_indexes=(),
                body_violations=("author_boundary",),
            )
        if remaining_violation == "invalid_offer" and actor_calls == 2:
            return NumericV2TransitionOfferReview(
                offer_present=True,
                valid=False,
                body_violations=(),
                unsafe_suggestion_indexes=(),
                failure_reason="改稿仍提出了与下一幕方向不符的行动。",
            )
        scene_violation = unsafe and bool(candidate.get("scene_narration"))
        body_violation = unsafe and not scene_violation
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason=original_reason if scene_violation else "正文仍虚构了玩家按快门。" if body_violation else "",
            unsafe_suggestion_indexes=(),
            body_violations=("scene_boundary",) if scene_violation else ("player_action", "author_boundary") if body_violation else (),
        )

    async def no_suggestions(*args, **kwargs):
        pytest.fail("Guard 之后不能再调用补推荐")

    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_current_scene_suggestions", no_suggestions, raising=False)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )
    session_path = tmp_path / "theater" / "numeric_v2" / "sessions" / "failed_update_removal.json"
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "failed_update_removal"},
        )
        assert started.status_code == 200
        before_bytes = session_path.read_bytes()
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "failed_update_removal",
                "client_turn_id": "failed_update_removal_1",
                "base_revision": 0,
                "message": "（举起相机对焦）这个角度可以吗？",
            },
        )

    assert actor_calls == 2
    # 首稿快速与争议复查各一次，改写稿仅快速复核一次。
    assert review_calls == 3
    assert unsafe_body_only_reviews == 0
    assert submitted.status_code == 200
    if remaining_violation:
        assert session_path.read_bytes() != before_bytes
        saved = json.loads(session_path.read_text(encoding="utf-8"))["session"]["performance_history"][-1]
        assert saved["performance"] == submitted.json()["performance"]["performance"]
        assert saved["transition_offered"] is (remaining_violation == "invalid_offer")
    else:
        assert submitted.status_code == 200
        assert submitted.json()["performance"]["performance"] == safe_performance
        assert "scene_narration" not in submitted.json()["performance"]


def test_numeric_v2_boundary_repair_keeps_diagnostic_without_rejected_candidate(
    tmp_path,
    monkeypatch,
):
    """The sole ordinary correction retains specific diagnostics but rewrites from original context instead of reusing the rejected candidate."""

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        retry_hint = str(kwargs.get("retry_hint") or "")
        if actor_calls == 1:
            return {"performance": "第一版包含受保护事实。", "transition_offered": False}
        assert "第一版包含受保护事实" not in retry_hint
        assert "正文包含尚未获准公开的事实" in retry_hint
        assert "唯一一次正文与提议修复" in retry_hint
        return {"performance": "（保持边界）只能确认眼前已知情况。", "transition_offered": False}

    async def review(*args, **kwargs):
        text = str(kwargs["actor_performance"].get("performance") or "")
        safe = "保持边界" in text
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="正文包含尚未获准公开的事实。" if not safe else "",
            body_violations=(() if safe else ("author_boundary",)),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "candidate_boundary_repair"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "candidate_boundary_repair",
                "client_turn_id": "candidate_boundary_repair_1",
                "base_revision": 0,
                "message": "先确认眼前情况。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    assert submitted.json()["performance"]["performance"] == "（保持边界）只能确认眼前已知情况。"


@pytest.mark.parametrize("remove_conflict", [True, False])
def test_numeric_v2_boundary_repair_commits_last_reply_after_correction_budget(
    tmp_path,
    monkeypatch,
    remove_conflict,
):
    """Request removal of conflicts; persistent rejection adopts the final draft with consistent display, history and metrics."""

    actor_calls = 0
    review_calls = 0
    client = _client(tmp_path, monkeypatch)
    player_input = "（举起相机对焦）这个角度可以吗？"
    performance = "（站稳在窗边）这样就可以，我先保持这个姿势。"
    rejected_update = "玩家已经按下快门，新的照片已经生成。"
    failure_reason = "scene_update 把玩家尚未实施的按下快门写成完成，并生成了新照片。"

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(MetricChangeV2("trust", 1, "玩家兑现承诺", player_input),),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        candidate = {
            "performance": performance,
            "suggested_inputs": ["（按下快门）拍好了。", "（放下相机）先休息一下。"],
            "transition_offered": False,
        }
        if actor_calls == 1:
            candidate["scene_narration"] = rejected_update
        else:
            assert actor_calls == 2
            assert kwargs["player_input"] == player_input
            hint = str(kwargs.get("retry_hint") or "")
            assert performance not in hint
            assert rejected_update not in hint
            assert '"scene_narration"' not in hint
            assert failure_reason in hint
            assert "从本轮原始上下文重新回应" in hint
            if not remove_conflict:
                candidate["scene_narration"] = "手机屏幕上已出现刚拍好的照片。"
        return candidate

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        candidate = kwargs["actor_performance"]
        assert candidate["performance"] == performance
        # 仅未来推荐中按快门不是已发生事实；违规来自场景更新的成片结果。
        unsafe = bool(candidate.get("scene_narration"))
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason=failure_reason if unsafe else "",
            unsafe_suggestion_indexes=(),
            body_violations=("player_action", "author_boundary") if unsafe else (),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )
    session_path = tmp_path / "theater" / "numeric_v2" / "sessions" / "result_repair.json"
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "result_repair"},
        )
        assert started.status_code == 200
        before_bytes = session_path.read_bytes()
        before = json.loads(before_bytes)
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "result_repair",
                "client_turn_id": "result_repair_1",
                "base_revision": 0,
                "message": player_input,
            },
        )
    assert actor_calls == 2
    # 首稿快速与争议复查各一次，改写稿仅快速复核一次。
    assert review_calls == 3
    after = json.loads(session_path.read_bytes())
    assert submitted.status_code == 200
    assert after["session"]["revision"] == before["session"]["revision"] + 1
    assert after["session"]["metrics"]["trust"] == before["session"]["metrics"]["trust"] + 1
    assert len(after["ledger_events"]) == len(before["ledger_events"]) + 1
    if remove_conflict:
        assert submitted.status_code == 200
        assert submitted.json()["performance"]["performance"] == performance
        assert "scene_narration" not in submitted.json()["performance"]
        assert after["session"]["revision"] == before["session"]["revision"] + 1
        assert after["session"]["metrics"]["trust"] == before["session"]["metrics"]["trust"] + 1
        assert len(after["ledger_events"]) == len(before["ledger_events"]) + 1
        assert rejected_update not in session_path.read_text(encoding="utf-8")
    else:
        last_update = "手机屏幕上已出现刚拍好的照片。"
        assert submitted.json()["performance"]["scene_narration"] == last_update
        assert after["session"]["performance_history"][-1]["scene_narration"] == last_update
        assert rejected_update not in session_path.read_text(encoding="utf-8")


@pytest.mark.parametrize("offer_valid", [True, False])
def test_numeric_v2_unsafe_button_does_not_override_body_offer_validity(
    tmp_path,
    monkeypatch,
    offer_valid,
):
    """Remove unsafe buttons independently; latch valid body invitations and still rewrite invalid ones."""

    actor_calls = 0
    review_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls == 2:
            assert not offer_valid
            assert "正文邀请的目的地不符合当前获准方向" in kwargs["retry_hint"]
            return {
                "performance": "（停在原地）我们先把眼前的事说清楚。",
                "suggested_inputs": ["先说说现在的情况。"],
                "transition_offered": False,
            }
        assert not kwargs.get("retry_hint")
        return {
            "performance": "（指向楼梯）要和人家一起下去查看吗？",
            "suggested_inputs": [
                "（走向楼梯）我自己下去，你留在这里。",
                "（退后一步）先不下去。",
            ],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        # 复查不改变首稿的无效邀请；只有真实改写后才返回安全。
        if actor_calls == 2:
            return NumericV2TransitionOfferReview(
                offer_present=False,
                valid=False,
                body_violations=(),
                unsafe_suggestion_indexes=(),
            )
        return NumericV2TransitionOfferReview(
            offer_present=True,
            valid=offer_valid,
            failure_reason=(
                "推荐要求玩家独自离开，与正文和下一幕的共同行动冲突。"
                if offer_valid else
                "正文邀请的目的地不符合当前获准方向，推荐也不符合共同前往的要求。"
            ),
            body_violations=(),
            unsafe_suggestion_indexes=(0,),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "unflagged_offer_repair"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "unflagged_offer_repair",
                "client_turn_id": "unflagged_offer_repair_1",
                "base_revision": 0,
                "message": "入口已经打开了，我们接下来怎么办？",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == (1 if offer_valid else 2)
    assert review_calls == (1 if offer_valid else 3)
    assert submitted.json()["performance"]["transition_offered"] is offer_valid
    assert submitted.json()["suggested_inputs"] == (
        ["（退后一步）先不下去。"] if offer_valid else ["先说说现在的情况。"]
    )


def test_numeric_v2_clears_phantom_transition_flag_without_actor_retry(
    tmp_path,
    monkeypatch,
):
    """If visible prose has no offer, clear only the erroneous internal flag without resampling valid prose."""

    actor_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": "（看了一眼焊点）只能确认它经过人为处理，时间仍不清楚。",
            "suggested_inputs": [
                "（收回手）那先记录现有线索。",
                "（看向终端）还有别的已知异常吗？",
            ],
            "transition_offered": True,
        }

    async def review(*args, **kwargs):
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "phantom_transition"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "phantom_transition",
                "client_turn_id": "phantom_transition_1",
                "base_revision": 0,
                "message": "这个焊点能看出是什么时候处理的吗？",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 1
    assert submitted.json()["performance"]["transition_offered"] is False
    assert submitted.json()["suggested_inputs"] == [
        "（收回手）那先记录现有线索。",
        "（看向终端）还有别的已知异常吗？",
    ]


@pytest.mark.parametrize("actor_flag", [False, True])
@pytest.mark.parametrize("review_failed", [False, True])
def test_numeric_v2_offer_uses_one_guard_and_never_rewrites_service_failure(
    tmp_path,
    monkeypatch,
    actor_flag,
    review_failed,
):
    """Valid offers do not depend on the Actor flag; Guard failure withdraws only the signal, without resampling the body."""

    actor_calls = 0
    review_calls = 0
    first_performance = "（看向门外）眼前的事已经处理好，要和我一起出发吗？"
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=True,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": first_performance,
            "suggested_inputs": ["（点头）好，我们一起出发。", "（摇头）我再留一会儿。"],
            "transition_offered": actor_flag,
        }

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        if review_failed:
            raise NumericV2EvaluatorError("numeric_v2_transition_judge_unavailable")
        return NumericV2TransitionOfferReview(
            offer_present=True,
            valid=True,
            body_violations=(),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "actor_offer"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "actor_offer",
                "client_turn_id": "actor_offer_1",
                "base_revision": 0,
                "message": "眼前的事已经处理好了。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 1
    assert review_calls == 1
    assert submitted.json()["performance"]["performance"] == first_performance
    assert submitted.json()["performance"]["transition_offered"] is (not review_failed)
    persisted = json.loads(
        (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "sessions"
            / "actor_offer.json"
        ).read_text(encoding="utf-8")
    )
    assert persisted["session"]["transition_offered"] is (not review_failed)
    assert persisted["session"]["revision"] == 1
    assert persisted["session"]["current_node_id"] == "start"


def test_numeric_v2_premature_scene_update_requires_the_single_actor_rewrite(
    tmp_path,
    monkeypatch,
):
    """A scene update crossing the scene boundary is a body violation requiring the sole rewrite, not field deletion followed by review."""

    actor_calls = 0
    review_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        candidate = {
            "performance": "（把毛毯递给你）要现在休息到天亮吗？",
            "scene_narration": "时间已经推进到第二天清晨。",
            "suggested_inputs": [
                "（接过毛毯躺下）好，今晚就休息吧。",
                "（摇摇头）我还想再坐一会儿。",
            ],
            "transition_offered": True,
        }
        if actor_calls == 2:
            assert "scene_narration 提前写成天亮" in kwargs["retry_hint"]
            candidate.pop("scene_narration")
        return candidate

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        candidate = kwargs["actor_performance"]
        # 首稿复查保持原旁白；不能先删掉违规字段再要求放行。
        assert review_calls == (actor_calls + 1 if review_calls > 1 else 1)
        if actor_calls == 1:
            assert candidate["scene_narration"] == "时间已经推进到第二天清晨。"
        return NumericV2TransitionOfferReview(
            offer_present=True,
            valid="scene_narration" not in candidate,
            failure_reason="scene_narration 提前写成天亮。" if "scene_narration" in candidate else "",
            unsafe_suggestion_indexes=(),
            body_violations=("scene_boundary",) if "scene_narration" in candidate else (),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "drop_scene_update"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "drop_scene_update",
                "client_turn_id": "drop_scene_update_1",
                "base_revision": 0,
                "message": "（在角落坐下）今晚就在这里休息。",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    # 首稿快速与争议复查各一次，改写稿仅快速复核一次。
    assert review_calls == 3
    assert submitted.json()["performance"]["performance"].startswith("（把毛毯递给你）")
    assert "scene_narration" not in submitted.json()["performance"]
    assert submitted.json()["performance"]["transition_offered"] is True


def test_numeric_v2_rewrites_fact_boundary_without_structured_field_scope(
    tmp_path,
    monkeypatch,
):
    """Author-fact violations require one rewrite, not diagnostic-driven field deletion."""

    actor_calls = 0
    review_calls = 0
    client = _client(tmp_path, monkeypatch)

    async def evaluate(*args, **kwargs):
        return NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            interaction_intent="scene_action",
        )

    async def turn(*args, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        if actor_calls > 1:
            return {
                "performance": "（收回视线）目前只能确认外面仍有不明嗡鸣。",
                "suggested_inputs": ["（继续倾听）声音有变化吗？", "（保持安静）先别行动。"],
                "transition_offered": False,
            }
        return {
            "performance": "（侧耳倾听）目前只能确认外面仍有嗡鸣声。",
            "scene_narration": "她已经确认了无人机的具体方位与距离。",
            "suggested_inputs": ["（继续倾听）声音有变化吗？", "（保持安静）先别行动。"],
            "transition_offered": False,
        }

    async def review(*args, **kwargs):
        nonlocal review_calls
        review_calls += 1
        candidate = kwargs["actor_performance"]
        unsafe = "scene_narration" in candidate
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            failure_reason="scene_update 虚构了无人机的具体方位与距离。" if unsafe else "",
            unsafe_suggestion_indexes=(),
            body_violations=("author_boundary",) if unsafe else (),
        )

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", turn)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2MetricEvaluator,
        "validate_transition_offer",
        review,
    )

    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "drop_fact_scene_update"},
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "drop_fact_scene_update",
                "client_turn_id": "drop_fact_scene_update_1",
                "base_revision": 0,
                "message": "外面的声音有变化吗？",
            },
        )

    assert submitted.status_code == 200
    assert actor_calls == 2
    # 首稿快速与争议复查各一次，改写稿仅快速复核一次。
    assert review_calls == 3
    assert "scene_narration" not in submitted.json()["performance"]
    assert "只能确认外面仍有不明嗡鸣" in submitted.json()["performance"]["performance"]


def test_numeric_v2_router_rejects_unknown_actor_budget_before_opening(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    with client:
        response = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "invalid_budget",
                "actor_budget_profile": "unlimited",
            },
        )

    assert response.status_code == 400
    assert response.json()["reason"] == "numeric_actor_budget_profile_invalid"


def test_numeric_v2_router_passes_budget_profile_to_opening(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    captured: dict[str, str] = {}

    async def opening(_self, *, engine, actor_budget_profile):
        captured["profile"] = actor_budget_profile
        return _performance("按精简档生成的开场。", opening=True)

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_opening",
        opening,
    )

    with client:
        response = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "economy_opening",
                "actor_budget_profile": "economy",
            },
        )

    assert response.status_code == 200
    assert captured == {"profile": "economy"}
    assert response.json()["session"]["actor_budget_profile"] == "economy"


def test_numeric_v2_router_removed_legacy_evidence_migration_endpoint(tmp_path, monkeypatch):
    """旧证据迁移接口不再存在，旧 Session 不通过 HTTP 迁移回运行时。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    with client:
        response = client.post(
            "/api/theater-numeric/session/migrate-evidence",
            json={"story_id": "numeric_v2_contract", "session_id": "legacy"},
        )
    # 路径已不再注册 POST；FastAPI 对未知方法返回 405，等价于接口删除。
    assert response.status_code == 405


def test_numeric_v2_router_starts_restores_and_submits_free_input(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        listed = client.get("/api/theater-numeric/stories")
        assert listed.status_code == 200
        assert listed.json()["stories"][0]["story_id"] == "numeric_v2_contract"
        assert listed.json()["stories"][0]["display_intro"]["player_identity"].startswith("你，")

        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "http_v2"},
        )
        body = started.json()
        assert started.status_code == 200
        assert body["session"]["schema"] == "neko.script.session.numeric.v2"
        assert body["session"]["actor_budget_profile"] == "balanced"
        assert body["session"]["opening_performance"]["performance"] == "你回来了。"
        assert "metrics" not in body["session"]
        assert "current_node_id" not in body["session"]
        assert "id" not in body["scene"]
        assert body["scene"]["min_turns"] == 2
        assert "recommended_turns" not in body["scene"]
        assert body["story_intro"]["player_identity"].startswith("哥哥，")
        assert body["story_intro"]["catgirl_identity"].startswith("测试猫娘，")
        assert body["participants"] == {
            "player_name": "哥哥",
            "catgirl_name": "测试猫娘",
        }
        assert "林舟" not in json.dumps(body, ensure_ascii=False)
        assert "小岚" not in json.dumps(body, ensure_ascii=False)

        resumed_start = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "http_v2_duplicate"},
        )
        assert resumed_start.status_code == 200
        assert resumed_start.json()["resumed"] is True
        assert resumed_start.json()["session"]["session_id"] == "http_v2"

        active = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )
        assert active.status_code == 200
        assert active.json()["session"]["session_id"] == "http_v2"
        assert len(list((tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json"))) == 1

        old_turn = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "http_v2",
                "client_turn_id": "old_turn_1",
                "base_revision": 0,
                "message": "这是旧会话的记录。",
            },
        )
        assert old_turn.status_code == 200

        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "http_v2",
                "base_revision": 1,
                "base_lifecycle_revision": 0,
            },
        )
        assert ended.status_code == 200
        assert ended.json()["session"]["status"] == "ended"
        assert ended.json()["end_receipt_id"].startswith("theater_end_")

        async def regenerated_opening(*args, **kwargs):
            return _performance("这是重新生成的新开场。", opening=True)

        monkeypatch.setattr(
            numeric_theater_router.NumericV2Actor,
            "generate_opening",
            regenerated_opening,
        )

        restarted = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "http_v2_after_restart",
                "replace_existing": True,
                "actor_budget_profile": "quality",
            },
        )
        assert restarted.status_code == 200
        assert restarted.json()["session"]["session_id"] == "http_v2_after_restart"
        assert restarted.json()["session"]["status"] == "active"
        assert restarted.json()["session"]["revision"] == 0
        assert restarted.json()["session"]["performance_history"] == []
        assert restarted.json()["session"]["actor_budget_profile"] == "quality"
        assert restarted.json()["session"]["opening_performance"]["performance"] == "这是重新生成的新开场。"
        assert len(list((tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json"))) == 1

        ended_history = client.get(
            "/api/theater-numeric/session/http_v2",
            params={"story_id": "numeric_v2_contract"},
        )
        assert ended_history.status_code == 404
        assert ended_history.json()["reason"] == "numeric_session_not_found"

        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "http_v2_after_restart",
                "client_turn_id": "http_turn_1",
                "base_revision": 0,
                "message": "我先听你说。",
            },
        )
        result = submitted.json()
        assert submitted.status_code == 200
        # 新状态机不再以 min_turns 阻断普通演绎；没有可见转场提议时保持 playing。
        assert result["resolved_turn"] == {"route_status": "playing", "route_changed": False}
        assert result["session"]["performance_history"][0]["input_text"] == "我先听你说。"
        assert "from_node_id" not in result["session"]["performance_history"][0]
        assert "to_node_id" not in result["session"]["performance_history"][0]
        assert result["suggested_inputs"] == ["继续听她说"]

        restored = client.get(
            "/api/theater-numeric/session/http_v2_after_restart",
            params={"story_id": "numeric_v2_contract"},
        )
        assert restored.status_code == 200
        assert restored.json()["session"]["revision"] == 1


def test_numeric_v2_user_exit_can_resume_same_session(tmp_path, monkeypatch):
    """主动退出只离开演绎界面，继续时必须恢复原 Session、revision 和历史。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "resumable_exit"},
        )
        assert started.status_code == 200
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "resumable_exit",
                "client_turn_id": "before_exit",
                "base_revision": 0,
                "message": "我先把信收好。",
            },
        )
        assert submitted.status_code == 200
        exited = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "resumable_exit",
                "base_revision": 1,
                "base_lifecycle_revision": 0,
            },
        )
        assert exited.status_code == 200
        assert exited.json()["session"]["status"] == "ended"
        assert exited.json()["session"]["ended_reason"] == "user_exit"
        assert exited.json()["session"]["lifecycle_revision"] == 1

        resumed = client.post(
            "/api/theater-numeric/session/resume",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "resumable_exit",
                "base_revision": 1,
                "base_lifecycle_revision": 1,
            },
        )
        assert resumed.status_code == 200
        resumed_session = resumed.json()["session"]
        assert resumed_session["session_id"] == "resumable_exit"
        assert resumed_session["status"] == "active"
        assert resumed_session["ended_reason"] is None
        assert resumed_session["revision"] == 1
        assert resumed_session["lifecycle_revision"] == 2
        assert resumed_session["performance_history"][0]["input_text"] == "我先把信收好。"

        continued = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "resumable_exit",
                "client_turn_id": "after_resume",
                "base_revision": 1,
                "message": "我们接着刚才的话说。",
            },
        )
        assert continued.status_code == 200
        assert continued.json()["session"]["revision"] == 2


def test_numeric_v2_can_end_session_after_story_package_upgrade(tmp_path, monkeypatch):
    """剧本更新后旧 Session 不可继续，但仍必须能够原子结束。"""  # noqa: DOCSTRING_CJK

    from types import SimpleNamespace
    async def cache(*args, **kwargs):
        return SimpleNamespace(content=b"{}", is_success=True, json=lambda: {"status": "cached"})
    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: SimpleNamespace(post=cache))
    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "old_package_exit"},
        )
        assert started.status_code == 200

        upgraded_story = numeric_v2_story()
        upgraded_story["meta"]["revision"] = "router-upgraded-revision"
        package_path = (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "packages"
            / "numeric_v2_contract.json"
        )
        package_path.write_text(
            json.dumps(upgraded_story, ensure_ascii=False),
            encoding="utf-8",
        )

        recovered = client.get("/api/theater-numeric/session/active", params={"story_id": "numeric_v2_contract"})
        assert recovered.status_code == 200
        assert recovered.json()["session"]["session_id"] == "old_package_exit"
        assert recovered.json()["session"]["continuation_allowed"] is False
        assert recovered.json()["scene"] is None

        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "old_package_exit",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        )

        assert ended.status_code == 200
        assert ended.json()["session"]["status"] == "ended"
        assert ended.json()["scene"] is None
        assert ended.json()["suggested_inputs"] == []
        assert ended.json()["end_receipt_id"].startswith("theater_end_")

        resumed = client.post(
            "/api/theater-numeric/session/resume",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "old_package_exit",
                "base_revision": 0,
                "base_lifecycle_revision": 1,
            },
        )
        assert resumed.status_code == 409
        assert resumed.json()["reason"] == "story_package_revision_mismatch"

        archived = client.post("/api/theater-numeric/session/archive", json={
            "story_id": "numeric_v2_contract", "session_id": "old_package_exit", "revision": 0,
            "end_receipt_id": ended.json()["end_receipt_id"], "archive_request_id": ended.json()["archive_request_id"],
        })
        assert archived.status_code == 200
        assert archived.json()["status"] == "written"

        restarted = client.post("/api/theater-numeric/session/start", json={
            "story_id": "numeric_v2_contract", "session_id": "after_upgrade", "replace_existing": True,
        })
        assert restarted.status_code == 200
        assert restarted.json()["session"]["session_id"] == "after_upgrade"


def test_numeric_v2_resume_rechecks_catgirl_inside_lifecycle_locks(
    tmp_path,
    monkeypatch,
):
    """恢复前的角色复验必须与 Session 状态变更处于同一锁区间。"""  # noqa: DOCSTRING_CJK

    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。"),
                    "新猫娘": _catgirl_profile("新猫娘", "活泼而坦率。"),
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _MutableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    with client:
        assert client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "resume_lock_guard"},
        ).status_code == 200
        assert client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "resume_lock_guard",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).status_code == 200

        story_guard_depth = {"value": 0}
        check_states = []
        original_guard = numeric_theater_router.NumericV2Runtime.story_session_guard
        original_check = numeric_theater_router._ensure_current_catgirl

        @asynccontextmanager
        async def tracked_story_guard(runtime):
            async with original_guard(runtime):
                story_guard_depth["value"] += 1
                try:
                    yield
                finally:
                    story_guard_depth["value"] -= 1

        def tracked_check(session, config_manager):
            check_states.append({
                "character": numeric_theater_router.character_config_mutation_lock.locked(),
                "story": story_guard_depth["value"] > 0,
            })
            return original_check(session, config_manager)

        monkeypatch.setattr(
            numeric_theater_router.NumericV2Runtime,
            "story_session_guard",
            tracked_story_guard,
        )
        monkeypatch.setattr(
            numeric_theater_router,
            "_ensure_current_catgirl",
            tracked_check,
        )
        resumed = client.post(
            "/api/theater-numeric/session/resume",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "resume_lock_guard",
                "base_revision": 0,
                "base_lifecycle_revision": 1,
            },
        )

        assert resumed.status_code == 200
        assert check_states == [{"character": True, "story": True}]


def test_numeric_v2_end_rechecks_catgirl_inside_lifecycle_locks(
    tmp_path,
    monkeypatch,
):
    """结束 Session 的角色复验、状态提交和回执创建必须处于同一双锁区间。"""  # noqa: DOCSTRING_CJK

    story_guard_depth = {"value": 0}
    check_states = []
    end_states = []
    original_guard = numeric_theater_router.NumericV2Runtime.story_session_guard
    original_check = numeric_theater_router._ensure_current_catgirl
    original_end = numeric_theater_router.NumericV2Runtime.end_session

    @asynccontextmanager
    async def tracked_story_guard(runtime):
        async with original_guard(runtime):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    def lock_state():
        return {
            "character": numeric_theater_router.character_config_mutation_lock.locked(),
            "story": story_guard_depth["value"] > 0,
        }

    def tracked_check(session, config_manager):
        check_states.append(lock_state())
        return original_check(session, config_manager)

    async def tracked_end(runtime, *args, **kwargs):
        end_states.append(lock_state())
        return await original_end(runtime, *args, **kwargs)

    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "end_lock_guard"},
        ).status_code == 200
        monkeypatch.setattr(
            numeric_theater_router.NumericV2Runtime,
            "story_session_guard",
            tracked_story_guard,
        )
        monkeypatch.setattr(
            numeric_theater_router,
            "_ensure_current_catgirl",
            tracked_check,
        )
        monkeypatch.setattr(
            numeric_theater_router.NumericV2Runtime,
            "end_session",
            tracked_end,
        )
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "end_lock_guard",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        )

    assert ended.status_code == 200
    assert check_states == [{"character": True, "story": True}]
    assert end_states == [{"character": True, "story": True}]


def test_numeric_v2_ended_retries_rebuild_missing_receipt(tmp_path, monkeypatch):
    """结束状态已提交后，结束重试和回合幂等重放都应补建缺失回执。"""  # noqa: DOCSTRING_CJK

    story_guard_depth = {"value": 0}
    receipt_lock_states = []
    original_guard = numeric_theater_router.NumericV2Runtime.story_session_guard
    original_create_receipt = numeric_theater_router._create_ended_receipt

    @asynccontextmanager
    async def tracked_story_guard(runtime):
        async with original_guard(runtime):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    async def tracked_create_receipt(config_manager, session):
        receipt_lock_states.append({
            "character_guard": numeric_theater_router.character_config_mutation_lock.locked(),
            "story_guard": story_guard_depth["value"] > 0,
        })
        return await original_create_receipt(config_manager, session)

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(
        numeric_theater_router,
        "_create_ended_receipt",
        tracked_create_receipt,
    )
    client = _client(tmp_path, monkeypatch)
    turn_payload = {
        "story_id": "numeric_v2_contract",
        "session_id": "receipt_retry_session",
        "client_turn_id": "receipt_retry_turn",
        "base_revision": 0,
        "message": "先把这句话说完。",
    }
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "receipt_retry_session",
            },
        )
        assert started.status_code == 200
        assert client.post(
            "/api/theater-numeric/session/input",
            json=turn_payload,
        ).status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "receipt_retry_session",
                "base_revision": 1,
                "base_lifecycle_revision": 0,
            },
        )
        assert ended.status_code == 200

        receipt_root = tmp_path / "theater" / "numeric_v2" / "end_receipts"
        for path in receipt_root.glob("*.json"):
            path.unlink()
        retried_end = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "receipt_retry_session",
                "base_revision": 1,
                "base_lifecycle_revision": 0,
            },
        )
        assert retried_end.status_code == 200
        assert retried_end.json()["idempotent_replay"] is True
        assert retried_end.json()["end_receipt_id"].startswith("theater_end_")

        for path in receipt_root.glob("*.json"):
            path.unlink()
        replayed_turn = client.post(
            "/api/theater-numeric/session/input",
            json=turn_payload,
        )

    assert replayed_turn.status_code == 200
    assert replayed_turn.json()["idempotent_replay"] is True
    assert replayed_turn.json()["end_receipt_id"].startswith("theater_end_")
    assert len(receipt_lock_states) == 3
    assert all(
        state == {"character_guard": True, "story_guard": True}
        for state in receipt_lock_states
    )


def test_numeric_v2_restore_creates_receipts_inside_lifecycle_locks(
    tmp_path,
    monkeypatch,
):
    """两个恢复入口补建结束回执时都必须持有角色锁和故事锁。"""  # noqa: DOCSTRING_CJK

    story_guard_depth = {"value": 0}
    receipt_lock_states = []
    original_guard = numeric_theater_router.NumericV2Runtime.story_session_guard
    original_create_receipt = numeric_theater_router._create_ended_receipt

    @asynccontextmanager
    async def tracked_story_guard(runtime):
        async with original_guard(runtime):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    async def tracked_create_receipt(config_manager, session):
        receipt_lock_states.append({
            "character_guard": numeric_theater_router.character_config_mutation_lock.locked(),
            "story_guard": story_guard_depth["value"] > 0,
        })
        return await original_create_receipt(config_manager, session)

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(
        numeric_theater_router,
        "_create_ended_receipt",
        tracked_create_receipt,
    )
    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "restore_receipt_lock_session",
            },
        ).status_code == 200
        assert client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "restore_receipt_lock_session",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).status_code == 200
        receipt_lock_states.clear()

        receipt_root = tmp_path / "theater" / "numeric_v2" / "end_receipts"
        for path in receipt_root.glob("*.json"):
            path.unlink()
        active = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )

        for path in receipt_root.glob("*.json"):
            path.unlink()
        restored = client.get(
            "/api/theater-numeric/session/restore_receipt_lock_session",
            params={"story_id": "numeric_v2_contract"},
        )

    assert active.status_code == 200
    assert restored.status_code == 200
    assert receipt_lock_states == [
        {"character_guard": True, "story_guard": True},
        {"character_guard": True, "story_guard": True},
    ]


def test_numeric_v2_session_restore_rejects_cross_story_session_id(
    tmp_path,
    monkeypatch,
):
    """指定 Session 恢复入口不能用另一个剧本的 Runtime 投影节点。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    other_story = numeric_v2_story()
    other_story["meta"]["story_id"] = "numeric_v2_other_story"
    with client:
        imported = client.post(
            "/api/theater-numeric/packages/import",
            json=other_story,
        )
        started = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "cross_story_restore_session",
            },
        )
        mismatched = client.get(
            "/api/theater-numeric/session/cross_story_restore_session",
            params={"story_id": "numeric_v2_other_story"},
        )

    assert imported.status_code == 200
    assert started.status_code == 200
    assert mismatched.status_code == 404
    assert mismatched.json()["reason"] == "numeric_session_not_found"


def test_numeric_v2_archive_skip_holds_lifecycle_locks(tmp_path, monkeypatch):
    """跳过归档的校验与提交必须位于角色和剧本生命周期锁内。"""  # noqa: DOCSTRING_CJK

    story_guard_active = {"value": False}
    observed = {}
    original_guard = numeric_theater_router.numeric_v2_story_session_guard
    original_update = NumericV2ArchiveStore.aupdate

    @asynccontextmanager
    async def tracked_story_guard(*args, **kwargs):
        async with original_guard(*args, **kwargs):
            story_guard_active["value"] = True
            try:
                yield
            finally:
                story_guard_active["value"] = False

    async def tracked_update(self, receipt, **changes):
        if changes.get("status") == "skipped":
            observed["story_guard"] = story_guard_active["value"]
            observed["character_guard"] = (
                numeric_theater_router.character_config_mutation_lock.locked()
            )
        return await original_update(self, receipt, **changes)

    monkeypatch.setattr(
        numeric_theater_router,
        "numeric_v2_story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(NumericV2ArchiveStore, "aupdate", tracked_update)
    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "skip_locked"},
        ).status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "skip_locked",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).json()
        skipped = client.post(
            "/api/theater-numeric/session/archive/skip",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "skip_locked",
                "revision": 0,
                "end_receipt_id": ended["end_receipt_id"],
            },
        )

    assert skipped.status_code == 200
    assert skipped.json()["status"] == "skipped"
    assert observed == {"story_guard": True, "character_guard": True}


def test_skip_archive_rechecks_owner_after_waiting_for_character_lock(tmp_path, monkeypatch):
    class Config(_ConfigManager):
        current_name = "测试猫娘"
        def load_characters(self, *, require_authoritative=False):
            data = super().load_characters()
            data["猫娘"]["新猫娘"] = _catgirl_profile("新猫娘", "另一个角色")
            data["当前猫娘"] = self.current_name
            return data
    config = Config(tmp_path)
    client = _client(tmp_path, monkeypatch, config)
    with client:
        client.post("/api/theater-numeric/session/start", json={"story_id": "numeric_v2_contract", "session_id": "skip_owner"})
        ended = client.post("/api/theater-numeric/session/end", json={
            "story_id": "numeric_v2_contract", "session_id": "skip_owner", "base_revision": 0, "base_lifecycle_revision": 0,
        }).json()
        original_lock = numeric_theater_router.character_config_mutation_lock
        @asynccontextmanager
        async def switch_at_lock():
            async with original_lock:
                config.current_name = "新猫娘"
                yield
        monkeypatch.setattr(numeric_theater_router, "character_config_mutation_lock", switch_at_lock())
        skipped = client.post("/api/theater-numeric/session/archive/skip", json={
            "story_id": "numeric_v2_contract", "session_id": "skip_owner", "revision": 0,
            "end_receipt_id": ended["end_receipt_id"],
        })
    assert skipped.status_code == 409
    assert skipped.json()["reason"] == "numeric_end_receipt_character_mismatch"
    store = NumericV2ArchiveStore(tmp_path / "theater")
    assert store.load(ended["end_receipt_id"])["status"] == "pending"


def test_forget_preserves_maintenance_error_for_the_common_handler(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    error = MaintenanceModeError("applying_snapshot", operation="save", target="theater/memory")
    async def blocked(*args):
        raise error
    monkeypatch.setattr(numeric_theater_router, "_assert_numeric_writable", blocked)
    with client, pytest.raises(MaintenanceModeError) as caught:
        client.post("/api/theater-numeric/memory/forget", json={
            "story_id": "numeric_v2_contract", "character_id": "character_" + "1" * 32,
        })
    assert caught.value is error


@pytest.mark.parametrize("failure", ["watermark", "remote", "partial_files", "completion"])
def test_forget_transaction_recovers_after_interruption_and_blocks_archival(tmp_path, monkeypatch, failure):
    from services.theater.numeric_v2_store import NumericV2SessionStore
    from types import SimpleNamespace
    remote_calls = []
    async def post(url, **kwargs):
        if url.endswith("/theater/forget"):
            remote_calls.append(url)
            if failure == "remote" and len(remote_calls) == 1:
                raise OSError("lost response after remote deletion")
            return SimpleNamespace(content=b"{}", is_success=True, json=lambda: {"ok": True})
        return SimpleNamespace(content=b"{}", is_success=True, json=lambda: {"status": "cached"})
    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: SimpleNamespace(post=post))
    client = _client(tmp_path, monkeypatch)
    store = NumericV2ArchiveStore(tmp_path / "theater")
    scope = {"story_id": "numeric_v2_contract", "character_id": "character_" + "1" * 32}
    with client:
        client.post("/api/theater-numeric/session/start", json={"story_id": scope["story_id"], "session_id": "forget_retry"})
        ended = client.post("/api/theater-numeric/session/end", json={
            "story_id": scope["story_id"], "session_id": "forget_retry", "base_revision": 0, "base_lifecycle_revision": 0,
        }).json()
        archive_payload = {"story_id": scope["story_id"], "session_id": "forget_retry", "revision": 0,
                           "end_receipt_id": ended["end_receipt_id"], "archive_request_id": ended["archive_request_id"]}
        assert client.post("/api/theater-numeric/session/archive", json=archive_payload).status_code == 200
        with monkeypatch.context() as broken:
            if failure == "watermark":
                async def fail_watermark(*args, **kwargs):
                    raise OSError("watermark write failed")
                broken.setattr(NumericV2SessionStore, "forget_history_through_current_revision", fail_watermark)
            elif failure == "partial_files":
                def fail_files(self, pending):
                    self._receipt_path(ended["end_receipt_id"]).unlink()
                    raise OSError("interrupted before pointer cleanup")
                broken.setattr(NumericV2ArchiveStore, "delete_forget_files", fail_files)
            elif failure == "completion":
                def fail_completion(*args):
                    raise OSError("interrupted before intent removal")
                broken.setattr(NumericV2ArchiveStore, "complete_forget", fail_completion)
            first = client.post("/api/theater-numeric/memory/forget", json=scope)
        assert first.status_code == 502
        pending = NumericV2ArchiveStore(tmp_path / "theater").pending_forget(**scope)
        assert pending is not None and pending["through_revision"] == 0
        blocked = client.post("/api/theater-numeric/session/archive", json=archive_payload)
        assert blocked.status_code != 200
        retry = client.post("/api/theater-numeric/memory/forget", json=scope)
        assert retry.status_code == 200
        assert store.pending_forget(**scope) is None
        assert store.list_public_archives(**scope) == []
        active = client.get("/api/theater-numeric/session/active", params={"story_id": scope["story_id"]})
        assert active.status_code == 200
        assert active.json()["archive_status"] == "skipped"
        session_data = json.loads((tmp_path / "theater/numeric_v2/sessions/forget_retry.json").read_text(encoding="utf-8"))
        assert session_data["session"]["forgotten_through_revision"] == 0
    if failure == "watermark":
        assert len(remote_calls) == 1


def test_workflow_timing_log_never_serializes_review_or_candidate_text(tmp_path, monkeypatch):
    from services.theater import numeric_v2_workflow
    logs = []
    client = _client(tmp_path, monkeypatch)
    async def review(*args, **kwargs):
        return NumericV2TransitionOfferReview(offer_present=False, valid=False,
            body_violations=(), unsafe_suggestion_indexes=(), failure_reason="PRIVATE_REVIEW_TEXT")
    async def actor(*args, **kwargs):
        return _performance("PRIVATE_CANDIDATE_TEXT")
    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", actor)
    monkeypatch.setattr(numeric_v2_workflow.logger, "info", lambda *args: logs.append(args))
    with client:
        client.post("/api/theater-numeric/session/start", json={"story_id": "numeric_v2_contract", "session_id": "private_logs"})
        response = client.post("/api/theater-numeric/session/input", json={
            "story_id": "numeric_v2_contract", "session_id": "private_logs", "base_revision": 0,
            "client_turn_id": "private_turn", "message": "PRIVATE_PLAYER_TEXT",
        })
    assert response.status_code == 200
    assert logs and "PRIVATE_" not in str(logs)
    payload = logs[-1][-1]
    assert payload["completed"] is True
    assert payload["actor_generation_attempts"] == 1
    assert payload["timings_ms"]["total_wall"] >= 0
    assert "transition_review_results" not in payload


def test_numeric_v2_actor_failure_does_not_commit_half_turn(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "http_actor_fail"},
        )

        async def fail_actor(*args, **kwargs):
            raise NumericV2ActorError("test_actor_failure")

        monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", fail_actor)
        failed = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "http_actor_fail",
                "client_turn_id": "failed_turn",
                "base_revision": 0,
                "message": "这一轮不能留下半回合。",
            },
        )
        assert failed.status_code == 502

        restored = client.get(
            "/api/theater-numeric/session/http_actor_fail",
            params={"story_id": "numeric_v2_contract"},
        )
        assert restored.json()["session"]["revision"] == 0
        assert restored.json()["session"]["performance_history"] == []


def test_numeric_v2_router_delete_story_reports_active_catgirls_and_cascades_sessions(
    tmp_path,
    monkeypatch,
):
    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。"),
                    "新猫娘": _catgirl_profile("新猫娘", "活泼而坦率。"),
                },
                "主人": {"昵称": "哥哥"},
            }

    delete_lock_states = []
    original_delete_transaction = (
        numeric_theater_router.delete_numeric_v2_story_transactionally
    )
    original_story_guard = numeric_theater_router.numeric_v2_story_session_guard
    story_guard_depth = {"value": 0}

    @asynccontextmanager
    async def tracked_story_guard(theater_root, story_id):
        async with original_story_guard(theater_root, story_id):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    async def tracked_delete_transaction(*args, **kwargs):
        delete_lock_states.append({
            "character": numeric_theater_router.character_config_mutation_lock.locked(),
            "story": story_guard_depth["value"] > 0,
        })
        return await original_delete_transaction(*args, **kwargs)

    monkeypatch.setattr(
        numeric_theater_router,
        "numeric_v2_story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(
        numeric_theater_router,
        "delete_numeric_v2_story_transactionally",
        tracked_delete_transaction,
    )
    manager = _MutableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    with client:
        first = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "delete_story_first"},
        )
        assert first.status_code == 200
        manager.current_name = "新猫娘"
        second = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "delete_story_second"},
        )
        assert second.status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "delete_story_second",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        )
        assert ended.status_code == 200
        archive_store = NumericV2ArchiveStore(tmp_path / "theater")
        public_archive_path = archive_store._public_archive_path(
            "delete_story_second"
        )
        archive_store._write(public_archive_path, {
            "schema": "neko.theater.numeric.v2.public-archive",
            "story_id": "numeric_v2_contract",
            "session_id": "delete_story_second",
            "character_id": "",
            "catgirl_name": "新猫娘",
        })
        assert public_archive_path.is_file()

        preview = client.get(
            "/api/theater-numeric/packages/numeric_v2_contract/delete-preview"
        )
        assert preview.status_code == 200
        assert preview.json()["active_catgirl_names"] == ["测试猫娘"]
        assert preview.json()["session_count"] == 2

        package_path = (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "packages"
            / "numeric_v2_contract.json"
        )
        # 损坏包会从列表隐藏，但删除恢复入口仍须绕过 Engine 编译并完成原子级联。
        package_path.write_text("{invalid-json", encoding="utf-8")
        assert client.get("/api/theater-numeric/stories").json()["stories"] == []

        def unexpected_load(*_args, **_kwargs):
            raise AssertionError("删除损坏包不应加载或编译 Engine")

        monkeypatch.setattr(
            numeric_theater_router.NumericV2PackageRegistry,
            "load_engine",
            unexpected_load,
        )
        damaged_preview = client.get(
            "/api/theater-numeric/packages/numeric_v2_contract/delete-preview"
        )
        assert damaged_preview.status_code == 200
        assert damaged_preview.json() == preview.json()
        deleted = client.delete(
            "/api/theater-numeric/packages/numeric_v2_contract"
        )
        assert deleted.status_code == 200
        assert deleted.json()["deleted_session_count"] == 2
        assert not list(
            (tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json")
        )
        assert not package_path.exists()
        assert not public_archive_path.exists()
        assert client.get("/api/theater-numeric/stories").json()["stories"] == []
        assert delete_lock_states == [{"character": True, "story": True}]


def test_numeric_v2_story_delete_rolls_back_package_sessions_and_index(
    tmp_path,
    monkeypatch,
):
    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "delete_rollback"},
        )
        assert started.status_code == 200
        package_path = (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "packages"
            / "numeric_v2_contract.json"
        )
        session_path = (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "sessions"
            / "delete_rollback.json"
        )
        index_path = tmp_path / "theater" / "numeric_v2" / "story_sessions.json"
        original_index = index_path.read_bytes()
        archive_store = NumericV2ArchiveStore(tmp_path / "theater")
        public_archive_path = archive_store._public_archive_path("delete_rollback")
        archive_store._write(public_archive_path, {
            "schema": "neko.theater.numeric.v2.public-archive",
            "story_id": "numeric_v2_contract",
            "session_id": "delete_rollback",
            "character_id": "",
            "catgirl_name": "测试猫娘",
        })
        original_delete = numeric_theater_router.NumericV2PackageRegistry.delete_package

        def delete_then_fail(registry, story_id):
            original_delete(registry, story_id)
            raise NumericV2PackageError("forced_delete_failure")

        monkeypatch.setattr(
            numeric_theater_router.NumericV2PackageRegistry,
            "delete_package",
            delete_then_fail,
        )
        failed = client.delete(
            "/api/theater-numeric/packages/numeric_v2_contract"
        )

        assert failed.status_code == 422
        assert package_path.is_file()
        assert session_path.is_file()
        assert public_archive_path.is_file()
        assert index_path.read_bytes() == original_index
        transaction_root = (
            tmp_path / "theater" / "numeric_v2" / "delete_transactions"
        )
        assert not list(transaction_root.glob("*"))


def test_numeric_v2_router_rejects_stale_or_ended_turn_before_evaluator(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)

    async def fail_if_called(*_args, **_kwargs):
        raise AssertionError("Evaluator must not run for a stale or ended session")

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", fail_if_called)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "router_precheck"},
        )
        assert started.status_code == 200

        stale = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "router_precheck",
                "client_turn_id": "stale_turn",
                "base_revision": 1,
                "message": "旧状态不能调用模型。",
            },
        )
        assert stale.status_code == 409
        assert stale.json()["reason"] == "numeric_base_revision_mismatch"

        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "router_precheck",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        )
        assert ended.status_code == 200

        after_end = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "router_precheck",
                "client_turn_id": "after_end",
                "base_revision": 0,
                "message": "已结束状态不能调用模型。",
            },
        )
        assert after_end.status_code == 409
        assert after_end.json()["reason"] == "session_already_ended"


def test_numeric_v2_router_rechecks_catgirl_before_commit(tmp_path, monkeypatch):
    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {self.current_name: _catgirl_profile(self.current_name, "测试人格")},
                "主人": {"昵称": "哥哥"},
            }

    manager = _MutableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    events = []
    original_check = numeric_theater_router._ensure_current_catgirl

    def record_check(session, config_manager):
        events.append("check")
        return original_check(session, config_manager)

    async def change_during_actor(*args, **kwargs):
        events.append("actor")
        manager.current_name = "新猫娘"
        events.append("changed")
        return _performance("这一轮不应提交。")

    monkeypatch.setattr(numeric_theater_router, "_ensure_current_catgirl", record_check)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", change_during_actor)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "catgirl_commit_guard"},
        )
        assert started.status_code == 200

        blocked = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "catgirl_commit_guard",
                "client_turn_id": "catgirl_changed_during_model",
                "base_revision": 0,
                "message": "这一轮不应提交。",
            },
        )
        assert blocked.status_code == 409
        assert blocked.json()["reason"] == "catgirl_changed_requires_new_session"
        assert events == ["check", "check", "actor", "changed", "check"]

        manager.current_name = "测试猫娘"
        restored = client.get(
            "/api/theater-numeric/session/catgirl_commit_guard",
            params={"story_id": "numeric_v2_contract"},
        )
        assert restored.status_code == 200
        assert restored.json()["session"]["revision"] == 0


def test_numeric_v2_router_rechecks_catgirl_after_opening(tmp_path, monkeypatch):
    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {self.current_name: _catgirl_profile(self.current_name, "测试人格")},
                "主人": {"昵称": "哥哥"},
            }

    manager = _MutableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    events = []

    async def change_during_opening(*args, **kwargs):
        events.append("actor")
        manager.current_name = "新猫娘"
        events.append("changed")
        return _performance("开场不应写入旧角色。", opening=True)

    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_opening", change_during_opening)
    with client:
        blocked = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "catgirl_opening_guard"},
        )
        assert blocked.status_code == 409
        assert blocked.json()["reason"] == "catgirl_changed_requires_new_session"
        assert events == ["actor", "changed"]
        assert not list((tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json"))


def test_numeric_v2_start_rejects_selector_character_after_switch(
    tmp_path,
    monkeypatch,
):
    """选剧页属于旧角色时，开始请求不能为新当前角色创建或替换 Session。"""  # noqa: DOCSTRING_CJK

    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。"),
                    "新猫娘": _catgirl_profile("新猫娘", "活泼而坦率。"),
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _MutableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    opening_calls = []

    async def unexpected_opening(*_args, **_kwargs):
        opening_calls.append(True)
        return _performance("不应生成。", opening=True)

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_opening",
        unexpected_opening,
    )
    with client:
        old_character_id = client.get("/api/theater-numeric/stories").json()[
            "character_id"
        ]
        manager.current_name = "新猫娘"
        blocked = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "stale_selector_start",
                "character_id": old_character_id,
            },
        )

    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "catgirl_changed_requires_new_session"
    assert opening_calls == []
    assert not list((tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json"))


def test_numeric_v2_router_rechecks_package_after_opening(tmp_path, monkeypatch):
    """开场生成期间剧本被删除后，迟到结果不能重建孤儿 Session。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    package_path = (
        tmp_path
        / "theater"
        / "numeric_v2"
        / "packages"
        / "numeric_v2_contract.json"
    )

    async def delete_during_opening(*_args, **_kwargs):
        package_path.unlink()
        return _performance("这个开场不应提交。", opening=True)

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_opening",
        delete_during_opening,
    )

    with client:
        blocked = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "deleted_story_start"},
        )

    assert blocked.status_code == 404
    assert blocked.json()["reason"] == "numeric_story_not_found"
    assert not list((tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json"))


def test_numeric_v2_router_locks_final_session_creation_only(tmp_path, monkeypatch):
    """开场生成不长占锁，最终复验与 Session 创建必须同时持有两层生命周期锁。"""  # noqa: DOCSTRING_CJK

    story_guard_depth = {"value": 0}
    actor_lock_states = []
    commit_lock_states = []
    original_guard = numeric_theater_router.NumericV2Runtime.story_session_guard
    original_start = numeric_theater_router.NumericV2Runtime.start_session

    @asynccontextmanager
    async def tracked_story_guard(runtime):
        async with original_guard(runtime):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    async def tracked_opening(*_args, **_kwargs):
        actor_lock_states.append({
            "character": numeric_theater_router.character_config_mutation_lock.locked(),
            "story": story_guard_depth["value"] > 0,
        })
        return _performance("开场生成完成。", opening=True)

    async def tracked_start(runtime, *args, **kwargs):
        commit_lock_states.append({
            "character": numeric_theater_router.character_config_mutation_lock.locked(),
            "story": story_guard_depth["value"] > 0,
        })
        return await original_start(runtime, *args, **kwargs)

    client = _client(tmp_path, monkeypatch)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "start_session",
        tracked_start,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_opening",
        tracked_opening,
    )

    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "locked_start"},
        )

    assert started.status_code == 200
    assert actor_lock_states == [{"character": False, "story": False}]
    assert commit_lock_states == [{"character": True, "story": True}]


def test_numeric_v2_router_preserves_each_catgirls_story_session(tmp_path, monkeypatch):
    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。"),
                    "新猫娘": _catgirl_profile("新猫娘", "活泼而坦率。"),
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _MutableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)

    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "catgirl_before_change"},
        )
        assert started.status_code == 200
        manager.current_name = "新猫娘"
        second = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "catgirl_after_change"},
        )
        assert second.status_code == 200
        assert second.json()["session"]["session_id"] == "catgirl_after_change"
        assert sorted(
            path.stem
            for path in (tmp_path / "theater" / "numeric_v2" / "sessions").glob("*.json")
        ) == ["catgirl_after_change", "catgirl_before_change"]

        stale_tab = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "catgirl_before_change",
                "client_turn_id": "stale_tab_turn",
                "base_revision": 0,
                "message": "旧页面不能推进新演绎。",
            },
        )
        assert stale_tab.status_code == 409
        assert stale_tab.json()["reason"] == "catgirl_changed_requires_new_session"

        manager.current_name = "测试猫娘"
        restored_first = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )
        assert restored_first.status_code == 200
        assert restored_first.json()["session"]["session_id"] == "catgirl_before_change"

        manager.current_name = "新猫娘"
        restored_second = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )
        assert restored_second.status_code == 200
        assert restored_second.json()["session"]["session_id"] == "catgirl_after_change"


def test_numeric_v2_router_rejects_reusing_id_when_same_catgirl_profile_changed(
    tmp_path,
    monkeypatch,
):
    class _MutableProfileConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.personality = "安静而认真。"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": "测试猫娘",
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", self.personality)
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _MutableProfileConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "same_id_profile"},
        )
        assert started.status_code == 200
        manager.personality = "更新后更活泼。"

        replacement = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "same_id_profile",
                "replace_existing": True,
            },
        )
        active_replacement = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "same_id_profile_new",
                "replace_existing": True,
            },
        )

        assert replacement.status_code == 400
        assert replacement.json()["reason"] == "numeric_replacement_session_id_must_differ"
        assert active_replacement.status_code == 409
        assert active_replacement.json()["reason"] == "numeric_active_session_cannot_restart"
        assert (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "sessions"
            / "same_id_profile.json"
        ).is_file()


def test_numeric_v2_restart_keeps_ended_session_when_new_opening_fails(tmp_path, monkeypatch):
    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "restart_source"},
        )
        assert started.status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "restart_source",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        )
        assert ended.status_code == 200

        async def failed_opening(*args, **kwargs):
            raise NumericV2ActorError("numeric_v2_actor_model_call_failed")

        monkeypatch.setattr(
            numeric_theater_router.NumericV2Actor,
            "generate_opening",
            failed_opening,
        )
        restarted = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "restart_target",
                "replace_existing": True,
            },
        )

        assert restarted.status_code == 502
        assert restarted.json()["reason"] == "numeric_v2_actor_failed"
        active = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )
        assert active.status_code == 200
        assert active.json()["session"]["session_id"] == "restart_source"
        assert active.json()["session"]["status"] == "ended"
        sessions = tmp_path / "theater" / "numeric_v2" / "sessions"
        assert (sessions / "restart_source.json").is_file()
        assert not (sessions / "restart_target.json").exists()


@pytest.mark.parametrize("cleanup_error", [
    OSError("cleanup failed"),
    numeric_theater_router.NumericV2StoreRevisionConflictError("numeric_storage_root_changed"),
    MaintenanceModeError("applying_snapshot", operation="delete", target="theater/receipts"),
])
def test_numeric_v2_restart_stays_successful_when_old_receipt_cleanup_fails(
    tmp_path,
    monkeypatch,
    cleanup_error,
):
    """新 Session 提交后的旧回执清理失败不能反转成功结果。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    with client:
        assert client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "cleanup_source"},
        ).status_code == 200
        assert client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "cleanup_source",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).status_code == 200

        def fail_cleanup(_self, _session_id):
            raise cleanup_error

        monkeypatch.setattr(
            NumericV2ArchiveStore,
            "delete_session_receipts",
            fail_cleanup,
        )
        restarted = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "cleanup_target",
                "replace_existing": True,
            },
        )
        active = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )

    assert restarted.status_code == 200
    assert restarted.json()["session"]["session_id"] == "cleanup_target"
    assert active.status_code == 200
    assert active.json()["session"]["session_id"] == "cleanup_target"


def test_numeric_tts_merges_committed_dialogue_blocks_without_actions(tmp_path, monkeypatch):
    captured = {}
    story_guard_depth = {"value": 0}
    original_guard = numeric_theater_router.NumericV2Runtime.story_session_guard
    original_restore = numeric_theater_router.NumericV2Runtime.restore_session
    restore_calls = []

    @asynccontextmanager
    async def tracked_story_guard(runtime):
        async with original_guard(runtime):
            story_guard_depth["value"] += 1
            try:
                yield
            finally:
                story_guard_depth["value"] -= 1

    async def capture_speech(*args, **kwargs):
        captured["args"] = args
        captured.update(kwargs)
        captured["character_lock_held"] = (
            numeric_theater_router.character_config_mutation_lock.locked()
        )
        captured["story_lock_held"] = story_guard_depth["value"] > 0
        return {"audio_queued": True, "speech_id": "speech-1"}

    async def tracked_restore(runtime, session_id):
        restore_calls.append(story_guard_depth["value"] > 0)
        return await original_restore(runtime, session_id)

    monkeypatch.setattr(numeric_theater_router, "speak_committed_line", capture_speech)
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "story_session_guard",
        tracked_story_guard,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "restore_session",
        tracked_restore,
    )
    client = _client(
        tmp_path,
        monkeypatch,
        opening_text="（她抬起眼睛）你回来了。（她让开门口）先进来吧。",
    )
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "numeric_tts_binding"},
        )
        assert started.status_code == 200

        narration = client.post(
            "/api/theater-numeric/session/speak-block",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "numeric_tts_binding",
                "revision": 0,
                "block_index": 0,
                "playback_request_id": "tts-narration",
                "lifecycle_revision": 0,
            },
        )
        assert narration.status_code == 422
        assert narration.json()["reason"] == "numeric_speak_block_not_dialogue"
        restore_calls.clear()

        dialogue = client.post(
            "/api/theater-numeric/session/speak-block",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "numeric_tts_binding",
                "revision": 0,
                "block_index": 2,
                "dialogue_block_indexes": [2, 4],
                "playback_request_id": "tts-dialogue",
                "lifecycle_revision": 0,
            },
        )

    assert dialogue.status_code == 200
    assert dialogue.json()["speech_id"] == "speech-1"
    assert dialogue.json()["dialogue_block_count"] == 2
    assert captured["lanlan_name"] == "测试猫娘"
    assert captured["args"][0] == "你回来了。 先进来吧。"
    assert captured["interrupt_audio"] is True
    assert captured["character_lock_held"] is True
    assert captured["story_lock_held"] is True
    assert restore_calls == [False, True]


@pytest.mark.parametrize("change", ["exit", "exit_current", "exit_resume", "resume_current", "ending", "none"])
@pytest.mark.parametrize("during_request", [False, True])
def test_numeric_tts_fences_lifecycle_changes(tmp_path, monkeypatch, change, during_request):
    """Reject stale speech while allowing current speech and natural ending playback."""
    queued = []
    original_restore = numeric_theater_router.NumericV2Runtime.restore_session
    scope = {"story_id": "numeric_v2_contract", "session_id": "tts_lifecycle"}

    async def capture_speech(*args, **kwargs):
        queued.append(args[0])
        return {"audio_queued": True, "speech_id": "lifecycle-speech"}

    async def change_lifecycle(runtime):
        if change == "none":
            return
        await runtime.end_session(scope["session_id"], base_revision=0,
            base_lifecycle_revision=0, reason="natural_ending" if change == "ending" else "user_exit")
        if change in {"exit_resume", "resume_current"}:
            await runtime.resume_session(scope["session_id"], base_revision=0, base_lifecycle_revision=1)

    async def restore_then_change(runtime, session_id):
        stored = await original_restore(runtime, session_id)
        # Place the lifecycle change between the unlocked read and final enqueue guard.
        monkeypatch.setattr(numeric_theater_router.NumericV2Runtime, "restore_session", original_restore)
        await change_lifecycle(runtime)
        return stored

    monkeypatch.setattr(numeric_theater_router, "speak_committed_line", capture_speech)
    with _client(tmp_path, monkeypatch) as client:
        assert client.post("/api/theater-numeric/session/start", json=scope).status_code == 200
        if during_request:
            monkeypatch.setattr(numeric_theater_router.NumericV2Runtime, "restore_session", restore_then_change)
        else:
            runtime = client.portal.call(numeric_theater_router._runtime_for_story,
                numeric_theater_router.get_config_manager(), scope["story_id"])
            client.portal.call(change_lifecycle, runtime)
        # A natural ending response carries its new lifecycle version.
        lifecycle_revision = 1 if change == "ending" and not during_request else 0
        if not during_request and change in {"exit_current", "resume_current"}:
            lifecycle_revision = 1 if change == "exit_current" else 2
        response = client.post("/api/theater-numeric/session/speak-block", json={**scope,
            "revision": 0, "lifecycle_revision": lifecycle_revision, "block_index": 1,
            "playback_request_id": f"tts-{change}-{during_request}"})
        allowed = change == "none" or (change in {"ending", "resume_current"} and not during_request)
        assert response.status_code == (200 if allowed else 409), response.text
        assert bool(queued) is allowed


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["malformed", "unreadable", "missing", "non_object", "bad_map", "duplicate_name"])
async def test_numeric_audit_defers_when_character_source_is_unavailable(tmp_path, monkeypatch, failure):
    """Fallback profiles must never quarantine real saves or consume the audit-once marker."""
    import builtins
    from contextlib import nullcontext
    from tests.unit.test_character_memory_regression import _make_config_manager
    from services.theater import numeric_v2_maintenance
    from services.theater.numeric_v2_identity import numeric_v2_catgirl_binding

    cm = _make_config_manager(tmp_path)
    cm.save_characters({"当前猫娘": "C0", "猫娘": {
        f"C{i}": {"_reserved": {"character_id": "character_" + f"{i + 1:032x}"}}
        for i in range(8)}, "主人": {"昵称": "哥哥"}}, bypass_write_fence=True)
    root = numeric_theater_router._numeric_root(cm)
    registry = numeric_theater_router.NumericV2PackageRegistry(root / "numeric_v2" / "packages")
    registry.import_package(numeric_v2_story())
    runtime = numeric_theater_router.NumericV2Runtime(registry.load_engine("numeric_v2_contract"), root)
    for i in range(8):
        await runtime.start_session(session_id=f"audit_c{i}",
            catgirl_binding=numeric_v2_catgirl_binding(cm, f"C{i}"),
            opening_performance=_performance("你回来了。", opening=True))
    before = {path: path.read_bytes() for path in (root / "numeric_v2").rglob("*.json")}
    config_path = Path(cm.get_config_path("characters.json"))
    original_config = config_path.read_bytes()
    cm._characters_cache = None
    monkeypatch.setattr(numeric_theater_router, "assert_cloudsave_writable", lambda *a, **kw: None)
    monkeypatch.setattr(numeric_theater_router, "_numeric_write_transaction", lambda *a: nullcontext)
    with monkeypatch.context() as broken:
        if failure == "unreadable":
            original_open = builtins.open
            def unreadable(path, *args, **kwargs):
                if Path(path) == config_path:
                    raise PermissionError("test-unreadable")
                return original_open(path, *args, **kwargs)
            broken.setattr(builtins, "open", unreadable)
        elif failure == "missing":
            config_path.unlink()
        elif failure == "duplicate_name":
            ambiguous = json.loads(original_config)
            ambiguous["猫娘"][" C0 "] = ambiguous["猫娘"].pop("C1")
            config_path.write_text(json.dumps(ambiguous), encoding="utf-8")
        else:
            config_path.write_text({"malformed": '{"猫娘":', "non_object": '[]',
                                    "bad_map": '{"猫娘": []}'}[failure], encoding="utf-8")
        with pytest.raises(ValueError, match="numeric_character_config_unavailable"):
            await numeric_theater_router._registry(cm)
    after = {path: path.read_bytes() for path in (root / "numeric_v2").rglob("*.json")}
    assert after == before
    assert not (root / "numeric_v2" / "quarantine").exists()
    assert str(root.resolve()) not in numeric_v2_maintenance._MAINTAINED_ROOTS
    config_path.write_bytes(original_config)
    await numeric_theater_router._registry(cm)
    assert str(root.resolve()) in numeric_v2_maintenance._MAINTAINED_ROOTS
    assert len(list((root / "numeric_v2" / "sessions").glob("*.json"))) == 8


def test_numeric_end_receipt_archives_public_performance_once(tmp_path, monkeypatch):
    captured = {"calls": 0}

    class _MemoryResponse:
        content = b'{"status":"success","count":1}'
        is_success = True

        @staticmethod
        def json():
            return {"status": "success", "count": 1}

    class _MemoryClient:
        async def post(self, url, *, json, timeout):
            captured["calls"] += 1
            captured["character_lock_held"] = (
                numeric_theater_router.character_config_mutation_lock.locked()
            )
            captured["url"] = url
            captured["payload"] = json
            captured["timeout"] = timeout
            return _MemoryResponse()

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: _MemoryClient(),
    )
    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "archive_session"},
        )
        assert started.status_code == 200
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "archive_session",
                "client_turn_id": "archive_turn",
                "base_revision": 0,
                "message": "我把信放在桌上。",
            },
        )
        assert submitted.status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "archive_session",
                "base_revision": 1,
                "base_lifecycle_revision": 0,
            },
        ).json()
        receipt = {
            "story_id": "numeric_v2_contract",
            "session_id": "archive_session",
            "revision": 1,
            "end_receipt_id": ended["end_receipt_id"],
        }

        archived = client.post(
            "/api/theater-numeric/session/archive",
            json={**receipt, "archive_request_id": ended["archive_request_id"]},
        )
        archive_detail = client.get(
            "/api/theater-numeric/memory/archive",
            params={
                "story_id": "numeric_v2_contract",
                "session_id": "archive_session",
            },
        )
        archive_store = NumericV2ArchiveStore(tmp_path / "theater")
        # 模拟进程在 written 回执落盘后、Session 水位指针写入前中断。
        archive_store._write(
            archive_store._session_path("archive_session"),
            {
                "receipt_id": ended["end_receipt_id"],
                "archived_through_revision": -1,
            },
        )
        replay = client.post(
            "/api/theater-numeric/session/archive",
            json={**receipt, "archive_request_id": ended["archive_request_id"]},
        )
        repaired_pointer = archive_store._read(
            archive_store._session_path("archive_session")
        )
        restored = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )
        restarted = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "archive_session_next_run",
                "replace_existing": True,
            },
        )

    assert archived.status_code == 200
    assert archived.json()["status"] == "written"
    assert archive_detail.status_code == 200
    public_archive = archive_detail.json()["archive"]
    assert public_archive["opening"]["performance"] == "风铃轻轻响了一声。\n\n你回来了。"
    assert public_archive["turns"] == [{
        "revision": 1,
        "player_input": "我把信放在桌上。",
        "performance": "（风铃轻轻响了一声）我在听。",
        "parts": [
            {"kind": "action", "phase": "ordinary", "text": "（风铃轻轻响了一声）"},
            {"kind": "dialogue", "phase": "ordinary", "text": "我在听。"},
        ],
    }]
    assert "metrics" not in json.dumps(public_archive, ensure_ascii=False)
    assert replay.status_code == 200
    assert replay.json()["status"] == "already_written"
    assert repaired_pointer["archived_through_revision"] == 1
    assert restored.json()["end_receipt_id"] == receipt["end_receipt_id"]
    assert restored.json()["archive_status"] == "written"
    assert restarted.status_code == 200
    assert restarted.json()["session"]["session_id"] == "archive_session_next_run"
    assert not (
        tmp_path / "theater" / "numeric_v2" / "sessions" / "archive_session.json"
    ).exists()
    assert captured["calls"] == 1
    assert captured["character_lock_held"] is True
    assert captured["url"].endswith("/%E6%B5%8B%E8%AF%95%E7%8C%AB%E5%A8%98")
    assert captured["payload"]["idempotency_key"] == ended["archive_request_id"]
    memory_text = captured["payload"]["input_history"]
    memory_messages = json.loads(memory_text)
    assert "我在听。" in memory_text
    assert "我把信放在桌上。" not in memory_text
    assert [message["role"] for message in memory_messages] == ["system"]
    assert all(
        message["metadata"]["source"] == THEATER_MEMORY_SOURCE
        for message in memory_messages
    )
    assert all(message["metadata"]["episode_status"] == "paused" for message in memory_messages)
    assert memory_messages[0]["metadata"]["memory_tier"] == "episode_summary"
    assert memory_messages[0]["metadata"]["message_kind"] == "episode_summary"
    assert "【" not in memory_text
    assert "metrics" not in memory_text
    assert "suggested_inputs" not in memory_text
    assert "mainline_" not in memory_text

    # 单集胶囊经过统一消息转换后仍是 system，且内部来源元数据不丢失。
    persisted_messages = messages_to_dict(convert_to_messages(memory_messages))
    assert [message["type"] for message in persisted_messages] == ["system"]
    assert all(
        message["data"]["metadata"]["source"] == THEATER_MEMORY_SOURCE
        for message in persisted_messages
    )
    public_archives = list(
        (tmp_path / "theater" / "numeric_v2" / "public_archives").glob("*.json")
    )
    assert len(public_archives) == 1
    public_archive = json.loads(public_archives[0].read_text(encoding="utf-8"))
    assert public_archive["schema"] == "neko.theater.numeric.v2.public-archive"
    assert public_archive["session_id"] == "archive_session"
    assert public_archive["turns"][0]["player_input"] == "我把信放在桌上。"
    assert "我在听。" in public_archive["turns"][0]["performance"]
    assert "metrics" not in json.dumps(public_archive, ensure_ascii=False)
    assert "mainline_" not in json.dumps(public_archive, ensure_ascii=False)


def test_numeric_archive_written_retry_respects_cloudsave_write_fence(
    tmp_path,
    monkeypatch,
):
    """维护态不得借 written 回执重试隐式修复 Session 归档水位。"""  # noqa: DOCSTRING_CJK

    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "archive_fence_replay",
            },
        )
        assert started.status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "archive_fence_replay",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).json()

        archive_store = NumericV2ArchiveStore(tmp_path / "theater")
        receipt = archive_store.load(ended["end_receipt_id"])
        assert receipt is not None
        archive_store.update(
            receipt,
            status="written",
            archive_request_id=ended["archive_request_id"],
        )
        session_pointer_path = archive_store._session_path("archive_fence_replay")
        # 模拟 written 回执已落盘但 Session 水位尚未提交的进程中断窗口。
        archive_store._write(
            session_pointer_path,
            {
                "receipt_id": ended["end_receipt_id"],
                "archived_through_revision": -1,
            },
        )
        fence_calls = []

        def _blocked(_config_manager, *, operation: str, target: str):
            fence_calls.append((operation, target))
            raise MaintenanceModeError(
                "applying_snapshot",
                operation=operation,
                target=target,
            )

        monkeypatch.setattr(
            numeric_theater_router,
            "assert_cloudsave_writable",
            _blocked,
        )
        with pytest.raises(MaintenanceModeError):
            client.post(
                "/api/theater-numeric/session/archive",
                json={
                    "story_id": "numeric_v2_contract",
                    "session_id": "archive_fence_replay",
                    "revision": 0,
                    "end_receipt_id": ended["end_receipt_id"],
                    "archive_request_id": ended["archive_request_id"],
                },
            )

    pointer = archive_store._read(session_pointer_path)
    assert fence_calls == [("save", "theater/numeric_v2/archives")]
    assert pointer["archived_through_revision"] == -1


def test_numeric_story_memory_can_be_pinned_and_forgotten(tmp_path, monkeypatch):
    """显式忘记应清理热记忆、冷档案与旧回执，但保留 Session。"""  # noqa: DOCSTRING_CJK

    captured = {"forgotten": False}
    pin_lock_states = []
    original_pin = NumericV2ArchiveStore.set_public_archive_pinned

    def tracked_pin(store, *args, **kwargs):
        pin_lock_states.append(
            numeric_theater_router.character_config_mutation_lock.locked()
        )
        return original_pin(store, *args, **kwargs)

    monkeypatch.setattr(
        NumericV2ArchiveStore,
        "set_public_archive_pinned",
        tracked_pin,
    )

    class _MemoryResponse:
        content = b"{}"
        is_success = True

        def __init__(self, payload):
            self.payload = payload

        def json(self):
            return self.payload

    class _MemoryClient:
        async def post(self, url, *, json, timeout):
            if url.endswith("/theater/forget"):
                captured["forgotten"] = True
                return _MemoryResponse({
                    "ok": True,
                    "removed_recent": 1,
                    "removed_time_index": 3,
                })
            return _MemoryResponse({"status": "cached", "count": 1})

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: _MemoryClient(),
    )
    client = _client(tmp_path, monkeypatch)
    with client:
        client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "forget_session"},
        )
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "forget_session",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).json()
        archived = client.post(
            "/api/theater-numeric/session/archive",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "forget_session",
                "revision": 0,
                "end_receipt_id": ended["end_receipt_id"],
                "archive_request_id": ended["archive_request_id"],
            },
        )
        listed = client.get(
            "/api/theater-numeric/memory/archives",
            params={"story_id": "numeric_v2_contract"},
        )
        pinned = client.post(
            "/api/theater-numeric/memory/archive/pin",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "forget_session",
                "pinned": True,
            },
        )
        forgotten = client.post(
            "/api/theater-numeric/memory/forget",
            json={
                "story_id": "numeric_v2_contract",
                "character_id": "character_" + "1" * 32,
            },
        )
        after = client.get(
            "/api/theater-numeric/memory/archives",
            params={"story_id": "numeric_v2_contract"},
        )
        active = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )

    session_payload = json.loads(
        (
            tmp_path
            / "theater"
            / "numeric_v2"
            / "sessions"
            / "forget_session.json"
        ).read_text(encoding="utf-8")
    )

    assert archived.json()["status"] == "written"
    assert len(listed.json()["archives"]) == 1
    assert pinned.json()["archive"]["pinned"] is True
    assert pin_lock_states == [True]
    assert forgotten.json() == {
        "ok": True,
        "removed_recent": 1,
        "removed_time_index": 3,
        "removed_archives": 1,
        "removed_receipts": 2,
    }
    assert captured["forgotten"] is True
    assert after.json()["archives"] == []
    assert active.json()["session"]["session_id"] == "forget_session"
    assert active.json()["archive_status"] == "skipped"
    assert session_payload["session"]["forgotten_through_revision"] == 0


@pytest.mark.parametrize("read_failure", ["permission", "invalid_json"])
def test_numeric_story_memory_forget_aborts_before_partial_delete_on_receipt_read_error(
    tmp_path,
    monkeypatch,
    read_failure,
):
    """回执无法读取或解析时必须在删除记忆摘要前中止遗忘。"""  # noqa: DOCSTRING_CJK

    memory_calls = []

    class _UnexpectedMemoryClient:
        async def post(self, *_args, **_kwargs):
            memory_calls.append(True)
            raise AssertionError("本地遗忘目标预检失败后不能删除记忆摘要")

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: _UnexpectedMemoryClient(),
    )
    client = _client(tmp_path, monkeypatch)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "forget_receipt_io_failure",
            },
        )
        assert started.status_code == 200
        ended = client.post(
            "/api/theater-numeric/session/end",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "forget_receipt_io_failure",
                "base_revision": 0,
                "base_lifecycle_revision": 0,
            },
        ).json()

        archive_store = NumericV2ArchiveStore(tmp_path / "theater")
        receipt_path = archive_store._receipt_path(ended["end_receipt_id"])
        pointer_path = archive_store._session_path("forget_receipt_io_failure")
        path_type = type(receipt_path)
        original_read_text = path_type.read_text

        def transient_read(path, *args, **kwargs):
            if path == receipt_path:
                if read_failure == "invalid_json":
                    return "{"
                raise PermissionError("temporary receipt failure")
            return original_read_text(path, *args, **kwargs)

        monkeypatch.setattr(path_type, "read_text", transient_read)
        forgotten = client.post(
            "/api/theater-numeric/memory/forget",
            json={
                "story_id": "numeric_v2_contract",
                "character_id": "character_" + "1" * 32,
            },
        )

    assert forgotten.status_code == 502
    assert forgotten.json()["reason"] == "numeric_theater_memory_forget_failed"
    assert memory_calls == []
    assert receipt_path.is_file()
    assert pointer_path.is_file()


def test_numeric_story_memory_can_be_forgotten_after_package_deletion(
    tmp_path,
    monkeypatch,
):
    """剧本包删除后仍应按稳定 story_id 清理残留记忆。"""  # noqa: DOCSTRING_CJK

    captured = {}

    class _MemoryResponse:
        content = b"{}"
        is_success = True

        @staticmethod
        def json():
            return {
                "ok": True,
                "removed_recent": 1,
                "removed_time_index": 1,
            }

    class _MemoryClient:
        async def post(self, url, *, json, timeout):
            captured["url"] = url
            captured["payload"] = json
            captured["character_lock_held"] = (
                numeric_theater_router.character_config_mutation_lock.locked()
            )
            return _MemoryResponse()

    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: _MemoryClient(),
    )
    client = _client(tmp_path, monkeypatch)
    with client:
        deleted = client.delete(
            "/api/theater-numeric/packages/numeric_v2_contract",
        )
        forgotten = client.post(
            "/api/theater-numeric/memory/forget",
            json={
                "story_id": "numeric_v2_contract",
                "character_id": "character_" + "1" * 32,
            },
        )

    assert deleted.status_code == 200
    assert forgotten.status_code == 200
    assert forgotten.json() == {
        "ok": True,
        "removed_recent": 1,
        "removed_time_index": 1,
        "removed_archives": 0,
        "removed_receipts": 0,
    }
    assert captured["payload"] == {"story_id": "numeric_v2_contract"}
    assert captured["character_lock_held"] is True


def test_numeric_story_memory_forget_rejects_switched_character(
    tmp_path,
    monkeypatch,
):
    """确认弹窗属于旧角色时，服务端不能删除新当前角色的记忆。"""  # noqa: DOCSTRING_CJK

    class _MutableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "测试猫娘"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。"),
                    "新猫娘": _catgirl_profile("新猫娘", "活泼而坦率。"),
                },
                "主人": {"昵称": "哥哥"},
            }

    class _UnexpectedMemoryClient:
        async def post(self, *_args, **_kwargs):
            raise AssertionError("角色不匹配时不能调用记忆删除")

    manager = _MutableConfigManager(tmp_path)
    monkeypatch.setattr(
        "utils.internal_http_client.get_internal_http_client",
        lambda: _UnexpectedMemoryClient(),
    )
    client = _client(tmp_path, monkeypatch, config_manager=manager)
    with client:
        listed = client.get("/api/theater-numeric/stories")
        old_character_id = listed.json()["character_id"]
        manager.current_name = "新猫娘"
        blocked = client.post(
            "/api/theater-numeric/memory/forget",
            json={
                "story_id": "numeric_v2_contract",
                "character_id": old_character_id,
            },
        )

    assert blocked.status_code == 409
    assert blocked.json()["reason"] == "catgirl_changed_requires_refresh"


def test_numeric_memory_projection_builds_one_compact_episode_summary():
    """完整换场留在 Session；日常记忆只接收一条有序单集摘要。"""  # noqa: DOCSTRING_CJK

    session = SimpleNamespace(
        story_package_id="numeric_v2_contract",
        session_id="memory_projection",
        revision=2,
        opening_performance={
            "scene_narration": "雨点敲在窗沿。",
            "performance": "（抬起头）你来了。",
        },
        performance_history=(
            {
                "revision": 1,
                "input_text": "把合同递给她。",
                "performance": "（接过合同）我看看。",
            },
            {
                "revision": 2,
                "input_text": "一起去找中介。",
                "segments": [
                    {
                        "phase": "source_response",
                        "performance": "（站起身）走吧。",
                    },
                    {
                        "phase": "transition_bridge",
                        "scene_narration": "雨停后，两人来到街角。",
                    },
                    {
                        "phase": "target_opening",
                        "scene_narration": "卷帘门已经落锁。",
                        "performance": "（攥紧合同）他们跑了。",
                    },
                ],
            },
        ),
    )

    messages = build_numeric_v2_memory_messages(
        title="雨夜合租",
        session=session,
        ending=None,
    )

    assert [message["role"] for message in messages] == ["system"]
    capsule = messages[0]
    transition_text = json.dumps(capsule["content"], ensure_ascii=False)
    expected_order = [
        "走吧。",
        "雨停后，两人来到街角。",
        "卷帘门已经落锁。",
        "他们跑了。",
    ]
    assert [transition_text.index(text) for text in expected_order] == sorted(
        transition_text.index(text) for text in expected_order
    )
    assert capsule["metadata"]["memory_tier"] == "episode_summary"
    assert capsule["metadata"]["episode_summary"]
    assert "把合同递给她" not in transition_text
    assert "一起去找中介" not in transition_text
    assert "【" not in json.dumps(messages, ensure_ascii=False)


def test_sql_history_serialization_preserves_theater_and_allowed_runtime_metadata():
    """剧场元数据与运行时白名单字段必须能在同一条时间索引消息中共存。"""  # noqa: DOCSTRING_CJK

    history = SQLChatMessageHistory.__new__(SQLChatMessageHistory)
    serialized = json.loads(history._serialize(HumanMessage(
        content="把合同递过去。",
        additional_kwargs={
            "anti_repeat_response_id": "response-1",
            "private_note": "不能进入时间索引",
        },
        metadata={"source": THEATER_MEMORY_SOURCE, "session_id": "memory_projection"},
    )))

    assert serialized == {
        "type": "human",
        "data": {
            "content": "把合同递过去。",
            "additional_kwargs": {
                "anti_repeat_response_id": "response-1",
            },
            "metadata": {
                "source": THEATER_MEMORY_SOURCE,
                "session_id": "memory_projection",
            },
        },
    }
    assert HumanMessage(
        content="把合同递过去。",
        metadata={"source": THEATER_MEMORY_SOURCE},
    ).to_openai() == {
        "role": "user",
        "content": "把合同递过去。",
    }


def test_sql_history_replaces_theater_story_event_atomically(tmp_path):
    """同一稳定事件更新后只保留最新周目胶囊。"""  # noqa: DOCSTRING_CJK

    from sqlalchemy import create_engine, text

    database_path = tmp_path / "theater-memory.db"
    connection_string = f"sqlite:///{database_path}"
    history = SQLChatMessageHistory(
        connection_string=connection_string,
        session_id="theater-story-stable",
        table_name="message_store",
    )
    history.add_messages([SystemMessage(content="旧周目摘要")])
    history.replace_messages([
        SystemMessage(content="新周目一"),
        SystemMessage(content="新周目二"),
    ])
    with pytest.raises(ValueError, match="empty_conversation_replacement"):
        history.replace_messages([])

    with create_engine(connection_string).connect() as connection:
        rows = connection.execute(
            text(
                "SELECT message FROM message_store "
                "WHERE session_id = :session_id ORDER BY id"
            ),
            {"session_id": "theater-story-stable"},
        ).fetchall()

    assert len(rows) == 2
    assert "旧周目摘要" not in "\n".join(row[0] for row in rows)
    assert "新周目一" in rows[0][0]
    assert "新周目二" in rows[1][0]


def test_recent_compression_prompt_uses_theater_episode_context_only():
    """摘要提示只引用剧场单集上下文，不重新展开完整演绎正文。"""  # noqa: DOCSTRING_CJK

    manager = CompressedRecentHistoryManager.__new__(CompressedRecentHistoryManager)
    manager.name_mapping = {"human": "哥哥"}
    message = AIMessage(
        content="雨点敲在窗沿。\n\n（抬起头）你来了。",
        metadata={
            "source": THEATER_MEMORY_SOURCE,
            "session_id": "memory_projection",
            "archive_from_revision": 1,
            "archive_through_revision": 1,
            "story_title": "雨夜合租",
            "episode_status": "paused",
            "parts": [
                {"kind": "scene_narration", "phase": "opening", "text": "雨点敲在窗沿。"},
                {"kind": "action", "phase": "opening", "text": "（抬起头）"},
                {"kind": "dialogue", "phase": "opening", "text": "你来了。"},
            ],
        },
    )

    rendered = manager._render_messages_to_text([message], "测试猫娘")

    assert "共同演绎小剧场《雨夜合租》" in rendered
    assert "属于虚构剧情，不代表现实经历" in rendered
    assert "雨点敲在窗沿。" not in rendered
    assert "测试猫娘 | （抬起头）你来了。" not in rendered
    assert "【旁白】" not in rendered


@pytest.mark.asyncio
async def test_numeric_end_receipt_concurrent_creation_converges(tmp_path):
    """同一结束事实的并发读取必须得到同一个回执和归档请求 ID。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    session = SimpleNamespace(
        story_package_id="numeric_v2_contract",
        session_id="receipt_concurrent",
        revision=7,
        catgirl_binding={
            "character_id": "character_" + "1" * 32,
            "catgirl_name": "测试猫娘",
        },
    )

    receipts = await asyncio.gather(
        *(store.acreate_or_get(session) for _ in range(8))
    )

    assert len({item["receipt_id"] for item in receipts}) == 1
    assert len({item["archive_request_id"] for item in receipts}) == 1
    assert receipts[0]["archive_request_id"].startswith("theater_archive_")

    # 同一 Session 继续演绎并产生新 revision 后再次退出，必须生成新的记忆回执。
    later_session = SimpleNamespace(
        story_package_id=session.story_package_id,
        session_id=session.session_id,
        revision=8,
        catgirl_binding=session.catgirl_binding,
    )
    later_receipt = await store.acreate_or_get(later_session)
    assert later_receipt["receipt_id"] != receipts[0]["receipt_id"]
    assert later_receipt["revision"] == 8


def test_numeric_archive_receipt_advances_incremental_watermark_after_success(tmp_path):
    """继续演绎再次退出时，只归档上次成功写入之后的新公开回合。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    binding = {
        "character_id": "character_" + "1" * 32,
        "catgirl_name": "测试猫娘",
    }
    first_session = SimpleNamespace(
        story_package_id="numeric_v2_contract",
        session_id="incremental_archive",
        revision=3,
        catgirl_binding=binding,
    )
    first_receipt = store.create_or_get(first_session)
    assert first_receipt["archive_from_revision"] == 1
    assert first_receipt["archive_through_revision"] == 3
    assert first_receipt["include_opening"] is True

    store.update(first_receipt, status="written")
    resumed_session = SimpleNamespace(
        story_package_id=first_session.story_package_id,
        session_id=first_session.session_id,
        revision=6,
        catgirl_binding=binding,
    )
    resumed_receipt = store.create_or_get(resumed_session)

    assert resumed_receipt["archive_from_revision"] == 4
    assert resumed_receipt["archive_through_revision"] == 6
    assert resumed_receipt["include_opening"] is False


def test_numeric_archive_forget_watermark_excludes_previous_transcript(tmp_path):
    """继续旧 Session 后归档时，只能写入显式遗忘之后的新回合。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    session = SimpleNamespace(
        story_package_id="numeric_v2_contract",
        session_id="forgotten_archive",
        revision=2,
        forgotten_through_revision=1,
        catgirl_binding={
            "character_id": "character_" + "1" * 32,
            "catgirl_name": "测试猫娘",
            "player_address": "哥哥",
        },
        opening_performance={"performance": "这是已遗忘的开场。"},
        performance_history=(
            {
                "revision": 1,
                "input_text": "这是已遗忘的输入。",
                "performance": "这是已遗忘的回应。",
            },
            {
                "revision": 2,
                "input_text": "这是遗忘后的输入。",
                "performance": "这是遗忘后的回应。",
            },
        ),
    )

    receipt = store.create_or_get(session)
    archive = build_numeric_v2_public_archive(
        title="遗忘边界测试",
        session=session,
        ending=None,
    )

    assert receipt["archive_from_revision"] == 2
    assert receipt["archive_through_revision"] == 2
    assert receipt["include_opening"] is False
    assert archive["opening"] == {"performance": "", "parts": []}
    assert [turn["revision"] for turn in archive["turns"]] == [2]
    assert archive["turns"][0]["player_input"] == "这是遗忘后的输入。"
    assert archive["turns"][0]["performance"] == "这是遗忘后的回应。"


def test_numeric_archive_receipt_repairs_watermark_before_next_revision(tmp_path):
    """没有发生接口重试时，下一次退出也必须先从 written 回执修复水位。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    binding = {
        "character_id": "character_" + "1" * 32,
        "catgirl_name": "测试猫娘",
    }
    first_session = SimpleNamespace(
        story_package_id="numeric_v2_contract",
        session_id="interrupted_watermark",
        revision=3,
        catgirl_binding=binding,
    )
    first_receipt = store.create_or_get(first_session)
    interrupted_written = {**first_receipt, "status": "written"}
    # 只写回执，不写 Session 指针，精确模拟两次原子写之间掉电。
    store._write(
        store._receipt_path(first_receipt["receipt_id"]),
        interrupted_written,
    )

    resumed_receipt = store.create_or_get(SimpleNamespace(
        story_package_id=first_session.story_package_id,
        session_id=first_session.session_id,
        revision=6,
        catgirl_binding=binding,
    ))

    assert resumed_receipt["archive_from_revision"] == 4
    assert resumed_receipt["include_opening"] is False


def test_numeric_receipt_gc_preserves_legacy_written_receipt_until_cold_archive_exists(tmp_path):
    """升级前已写入记忆但未生成冷档案的回执不能被 GC 提前销毁。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    binding = {
        "character_id": "character_legacy_archive",
        "catgirl_name": "小葵",
    }
    first = SimpleNamespace(
        story_package_id="story_legacy_archive",
        session_id="legacy_archive_session",
        revision=1,
        catgirl_binding=binding,
    )
    first_receipt = store.create_or_get(first)
    store.update(first_receipt, status="written")
    second = SimpleNamespace(
        story_package_id=first.story_package_id,
        session_id=first.session_id,
        revision=2,
        catgirl_binding=binding,
    )
    second_receipt = store.create_or_get(second)
    store.update(second_receipt, status="skipped")

    store.cleanup_receipts({first.session_id})

    assert store.has_written_receipt_for_session(first.session_id) is True
    assert store._receipt_path(first_receipt["receipt_id"]).is_file()
    assert store.load_for_session(first.session_id)["receipt_id"] == second_receipt["receipt_id"]


def test_numeric_receipt_gc_aborts_on_transient_receipt_read_failure(
    tmp_path,
    monkeypatch,
):
    """回执暂时不可读时必须中止 GC，不能删除其指针或兼容证据。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    session = SimpleNamespace(
        story_package_id="story_receipt_transient_io",
        session_id="receipt_transient_io_session",
        revision=1,
        catgirl_binding={
            "character_id": "character_receipt_transient_io",
            "catgirl_name": "小葵",
        },
    )
    receipt = store.create_or_get(session)
    receipt_path = store._receipt_path(receipt["receipt_id"])
    pointer_path = store._session_path(session.session_id)
    path_type = type(receipt_path)
    original_read_text = path_type.read_text

    def transient_read(path, *args, **kwargs):
        if path == receipt_path:
            raise PermissionError("temporary receipt failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", transient_read)

    with pytest.raises(
        NumericV2ArchiveError,
        match="numeric_end_receipt_read_failed",
    ):
        store.cleanup_receipts({session.session_id})

    assert receipt_path.is_file()
    assert pointer_path.is_file()


def test_numeric_public_archives_keep_latest_five_plus_pinned(tmp_path):
    """冷档案默认有界，用户收藏的旧周目不参与自动淘汰。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)

    def session(index: int):
        return SimpleNamespace(
            story_package_id="story_retention",
            session_id=f"session_{index}",
            revision=index,
            catgirl_binding={
                "character_id": "character_retention",
                "catgirl_name": "小葵",
                "player_address": "哥哥",
            },
            opening_performance={"performance": "你来了。"},
            performance_history=(),
        )

    store.write_public_archive(title="有界剧本", session=session(0), ending=None)
    store.set_public_archive_pinned(
        story_id="story_retention",
        session_id="session_0",
        character_id="character_retention",
        legacy_catgirl_name="小葵",
        pinned=True,
    )
    for index in range(1, 7):
        store.write_public_archive(title="有界剧本", session=session(index), ending=None)

    archives = store.list_public_archives(
        story_id="story_retention",
        character_id="character_retention",
        legacy_catgirl_name="小葵",
    )
    assert len(archives) == 6
    assert {archive["session_id"] for archive in archives} == {
        "session_0", "session_2", "session_3", "session_4", "session_5", "session_6",
    }
    assert next(item for item in archives if item["session_id"] == "session_0")["pinned"] is True

    store.set_public_archive_pinned(
        story_id="story_retention",
        session_id="session_0",
        character_id="character_retention",
        legacy_catgirl_name="小葵",
        pinned=False,
    )
    remaining = store.list_public_archives(
        story_id="story_retention",
        character_id="character_retention",
        legacy_catgirl_name="小葵",
    )
    assert {archive["session_id"] for archive in remaining} == {
        "session_2", "session_3", "session_4", "session_5", "session_6",
    }


def test_numeric_public_archive_stage_can_commit_or_discard(tmp_path):
    """记忆请求失败时只留待提交副本，用户改选不记录后必须可销毁。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    session = SimpleNamespace(
        story_package_id="story_stage",
        session_id="session_stage",
        revision=1,
        catgirl_binding={
            "character_id": "character_stage",
            "catgirl_name": "小葵",
            "player_address": "哥哥",
        },
        opening_performance={"performance": "你来了。"},
        performance_history=(),
    )
    receipt = store.create_or_get(session)

    store.stage_public_archive(
        receipt=receipt,
        title="两阶段归档",
        session=session,
        ending=None,
    )
    assert not store._public_archive_path(session.session_id).exists()
    assert store._staged_archive_path(receipt["receipt_id"]).is_file()
    store.discard_staged_public_archive(receipt["receipt_id"])
    assert not store._staged_archive_path(receipt["receipt_id"]).exists()

    store.stage_public_archive(
        receipt=receipt,
        title="两阶段归档",
        session=session,
        ending=None,
    )
    store.commit_staged_public_archive(receipt)
    assert store._public_archive_path(session.session_id).is_file()
    assert not store._staged_archive_path(receipt["receipt_id"]).exists()


def test_numeric_public_archive_detail_rejects_other_character(tmp_path):
    """完整演绎详情不能因知道 Session ID 而跨角色读取。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)
    session = SimpleNamespace(
        story_package_id="story_private_archive",
        session_id="session_private_archive",
        revision=0,
        catgirl_binding={
            "character_id": "character_archive_owner",
            "catgirl_name": "小葵",
            "player_address": "哥哥",
        },
        opening_performance={"performance": "你来了。"},
        performance_history=(),
    )
    store.write_public_archive(title="归属测试", session=session, ending=None)

    with pytest.raises(
        NumericV2ArchiveError,
        match="numeric_public_archive_not_found",
    ):
        store.load_public_archive(
            story_id=session.story_package_id,
            session_id=session.session_id,
            character_id="character_archive_other",
            legacy_catgirl_name="其他猫娘",
        )


def test_numeric_receipt_gc_keeps_only_active_session_pointer(tmp_path):
    """冷启动回执 GC 只保留仍可恢复 Session 的最新指针。"""  # noqa: DOCSTRING_CJK

    store = NumericV2ArchiveStore(tmp_path)

    def session(index: int):
        return SimpleNamespace(
            story_package_id="story_receipt_gc",
            session_id=f"receipt_session_{index}",
            revision=index,
            catgirl_binding={
                "character_id": "character_receipt_gc",
                "catgirl_name": "小葵",
            },
        )

    for index in range(3):
        store.create_or_get(session(index))

    result = store.cleanup_receipts({"receipt_session_2"})

    assert result == {"receipts_removed": 2, "pointers_removed": 2}
    assert len(list(store.root.glob("theater_end_*.json"))) == 1
    assert len(list(store.root.glob("session-*.json"))) == 1


def test_numeric_session_survives_current_catgirl_rename(tmp_path, monkeypatch):
    """角色改名只改变展示名称，不能让相同 character_id 的进度失效。"""  # noqa: DOCSTRING_CJK

    class _RenamableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "改名前"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    self.current_name: {
                        "昵称": self.current_name,
                        "人格": "安静而认真。",
                        "_reserved": {"character_id": "character_" + "1" * 32},
                    },
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _RenamableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, manager)
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "rename_session"},
        )
        assert started.status_code == 200

        manager.current_name = "改名后"
        restored = client.get(
            "/api/theater-numeric/session/active",
            params={"story_id": "numeric_v2_contract"},
        )
        resumed = client.post(
            "/api/theater-numeric/session/start",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "rename_session_duplicate_start",
            },
        )

    assert restored.status_code == 200
    assert restored.json()["session"]["session_id"] == "rename_session"
    assert resumed.status_code == 200
    assert resumed.json()["resumed"] is True
    assert resumed.json()["session"]["session_id"] == "rename_session"


def test_numeric_turn_preserves_catgirl_rename_during_model_wait(tmp_path, monkeypatch):
    """模型等待期间的同角色改名必须随本轮提交保留，不能被旧候选状态覆盖。"""  # noqa: DOCSTRING_CJK

    class _RenamableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.current_name = "改名前"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": self.current_name,
                "猫娘": {
                    self.current_name: {
                        "昵称": self.current_name,
                        "人格": "安静而认真。",
                        "_reserved": {"character_id": "character_" + "1" * 32},
                    },
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _RenamableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, manager)
    commit_lock_states = []
    original_commit = numeric_theater_router.NumericV2Runtime.commit_turn

    async def rename_during_actor(*args, **kwargs):
        manager.current_name = "改名后"
        return _performance("我在听。")

    async def record_commit_lock(self, *args, **kwargs):
        commit_lock_states.append(
            numeric_theater_router.character_config_mutation_lock.locked()
        )
        return await original_commit(self, *args, **kwargs)

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_turn",
        rename_during_actor,
    )
    monkeypatch.setattr(
        numeric_theater_router.NumericV2Runtime,
        "commit_turn",
        record_commit_lock,
    )
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "rename_mid_turn"},
        )
        assert started.status_code == 200

        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "rename_mid_turn",
                "client_turn_id": "rename_mid_turn_1",
                "base_revision": 0,
                "message": "继续说吧。",
            },
        )

    assert submitted.status_code == 200
    session_path = (
        tmp_path
        / "theater"
        / "numeric_v2"
        / "sessions"
        / "rename_mid_turn.json"
    )
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted["session"]["catgirl_binding"]["catgirl_name"] == "改名后"
    assert commit_lock_states == [True]


def test_numeric_turn_rejects_profile_edit_during_actor_wait(tmp_path, monkeypatch):
    """Actor 使用旧人格生成时，同名角色的新资料不能被错误标记为本轮版本。"""  # noqa: DOCSTRING_CJK

    class _EditableConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.personality = "安静而认真。"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": "测试猫娘",
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", self.personality),
                },
                "主人": {"昵称": "哥哥"},
            }

    manager = _EditableConfigManager(tmp_path)
    client = _client(tmp_path, monkeypatch, manager)
    captured = {}

    async def edit_during_actor(*args, **kwargs):
        captured["character_profile"] = kwargs.get("character_profile")
        manager.personality = "编辑后变得活泼而坦率。"
        return _performance("这句旧人格输出不能提交。")

    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "profile_mid_turn"},
        )
        assert started.status_code == 200
        monkeypatch.setattr(
            numeric_theater_router.NumericV2Actor,
            "generate_turn",
            edit_during_actor,
        )
        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "profile_mid_turn",
                "client_turn_id": "profile_mid_turn_1",
                "base_revision": 0,
                "message": "继续说吧。",
            },
        )
        restored = client.get(
            "/api/theater-numeric/session/profile_mid_turn",
            params={"story_id": "numeric_v2_contract"},
        )

    assert "character_profile" in captured
    assert submitted.status_code == 409
    assert submitted.json()["reason"] == "catgirl_profile_changed_requires_retry"
    assert restored.status_code == 200
    assert restored.json()["session"]["revision"] == 0


def test_numeric_turn_preserves_player_address_fact_during_model_wait(
    tmp_path,
    monkeypatch,
):
    """本轮称呼事实生成后即被冻结，配置并发变化不能破坏 Ledger 重放。"""  # noqa: DOCSTRING_CJK

    class _MutableAddressConfigManager(_ConfigManager):
        def __init__(self, root: Path):
            super().__init__(root)
            self.player_address = "你"

        def load_characters(self, *, require_authoritative=False) -> dict:
            return {
                "当前猫娘": "测试猫娘",
                "猫娘": {
                    "测试猫娘": _catgirl_profile("测试猫娘", "安静而认真。"),
                },
                "主人": {"昵称": self.player_address},
            }

    manager = _MutableAddressConfigManager(tmp_path)
    client = _client(
        tmp_path,
        monkeypatch,
        manager,
        player_address_known=False,
    )

    async def change_address_during_actor(*args, **kwargs):
        manager.player_address = "哥哥"
        return _performance("我在听。")

    monkeypatch.setattr(
        numeric_theater_router.NumericV2Actor,
        "generate_turn",
        change_address_during_actor,
    )
    with client:
        started = client.post(
            "/api/theater-numeric/session/start",
            json={"story_id": "numeric_v2_contract", "session_id": "address_mid_turn"},
        )
        assert started.status_code == 200

        submitted = client.post(
            "/api/theater-numeric/session/input",
            json={
                "story_id": "numeric_v2_contract",
                "session_id": "address_mid_turn",
                "client_turn_id": "address_mid_turn_1",
                "base_revision": 0,
                "message": "哥哥，我们继续吧。",
            },
        )
        restored = client.get(
            "/api/theater-numeric/session/address_mid_turn",
            params={"story_id": "numeric_v2_contract"},
        )

    assert submitted.status_code == 200
    assert restored.status_code == 200
    assert restored.json()["session"]["player_address_known"] is False
    session_path = (
        tmp_path
        / "theater"
        / "numeric_v2"
        / "sessions"
        / "address_mid_turn.json"
    )
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    assert persisted["session"]["catgirl_binding"]["player_address"] == "你"


def test_numeric_v2_review_fallback_replay_and_next_turn_keep_same_history(tmp_path, monkeypatch):
    """Commit fallback prose through HTTP; retries neither rescore nor recall the model, and next-turn Actor and evaluator see the final draft."""
    client = _client(tmp_path, monkeypatch)
    calls = {"actor": 0, "review": 0, "evaluator": 0}
    last_reply = "（指向窗外）雨已经停了，我们可以继续聊天。"

    async def evaluate(*args, **kwargs):
        calls["evaluator"] += 1
        if kwargs["session"].revision == 1:
            assert kwargs["session"].performance_history[-1]["performance"] == last_reply
        return NumericV2EvaluationResult(
            (MetricChangeV2("trust", 2, "玩家兑现承诺", "我把毛巾递给你。"),), False)

    async def generate(*args, **kwargs):
        calls["actor"] += 1
        if kwargs["session"].revision == 1:
            assert kwargs["session"].performance_history[-1]["performance"] == last_reply
            return {"performance": "（望着窗外）对，刚才雨停了。", "suggested_inputs": []}
        return {"performance": last_reply if calls["actor"] == 2 else "这一版会被改写。", "suggested_inputs": []}

    async def review(*args, **kwargs):
        calls["review"] += 1
        # 精确模拟第一回合持续误拦；第二回合正常，避免把概率采样冒充流程覆盖。
        bad = kwargs["message"] == "我把毛巾递给你。"
        return NumericV2TransitionOfferReview(False, False, ("author_boundary",) if bad else (), (),
                                             "待修正的语义问题。" if bad else "")

    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(numeric_theater_router.NumericV2MetricEvaluator, "validate_transition_offer", review)
    monkeypatch.setattr(numeric_theater_router.NumericV2Actor, "generate_turn", generate)
    body = dict(story_id="numeric_v2_contract", session_id="fallback_replay", client_turn_id="first",
                base_revision=0, message="我把毛巾递给你。")
    path = tmp_path / "theater/numeric_v2/sessions/fallback_replay.json"
    with client:
        assert client.post("/api/theater-numeric/session/start", json={
            "story_id": body["story_id"], "session_id": body["session_id"]}).status_code == 200
        initial = json.loads(path.read_text(encoding="utf-8"))
        submitted = client.post("/api/theater-numeric/session/input", json=body)
        assert submitted.status_code == 200
        assert submitted.json()["performance"]["performance"] == last_reply
        committed = path.read_bytes()
        stored = json.loads(committed)
        assert stored["session"]["metrics"]["trust"] == initial["session"]["metrics"]["trust"] + 2
        assert len(stored["ledger_events"]) == 1
        assert calls == {"actor": 2, "review": 3, "evaluator": 1}
        replay = client.post("/api/theater-numeric/session/input", json=body)
        assert replay.status_code == 200 and replay.json()["idempotent_replay"]
        assert replay.json()["session"] == submitted.json()["session"]
        assert path.read_bytes() == committed
        assert calls == {"actor": 2, "review": 3, "evaluator": 1}
        resumed = client.get("/api/theater-numeric/session/fallback_replay", params={"story_id": body["story_id"]})
        assert resumed.json()["session"] == submitted.json()["session"]
        following = client.post("/api/theater-numeric/session/input", json={
            **body, "client_turn_id": "second", "base_revision": 1, "message": "你刚才说雨停了？"})
        assert following.status_code == 200
        assert following.json()["session"]["revision"] == 2
        assert calls == {"actor": 3, "review": 4, "evaluator": 2}


@pytest.mark.asyncio
async def test_final_storage_mutation_rejects_changed_root(tmp_path):
    from services.theater.numeric_v2_storage_transaction import run_storage_mutation
    manager = _ConfigManager(tmp_path / "old")
    transaction = numeric_theater_router._numeric_write_transaction(manager, manager.app_docs_dir / "theater")
    manager.app_docs_dir = tmp_path / "new"
    target = tmp_path / "old" / "theater" / "late.json"
    with pytest.raises(numeric_theater_router.NumericV2StoreRevisionConflictError, match="numeric_storage_root_changed"):
        await run_storage_mutation(transaction, target.write_text, "must not be saved")
    assert not target.exists()


@pytest.mark.asyncio
async def test_final_storage_mutation_holds_cloud_lock_and_waits_on_cancel(tmp_path):
    from contextlib import contextmanager
    import threading
    from services.theater.numeric_v2_storage_transaction import run_storage_mutation
    from utils.cloudsave_runtime import fence

    manager = _ConfigManager(tmp_path)
    transaction = numeric_theater_router._numeric_write_transaction(manager, tmp_path / "theater")
    entered, release = threading.Event(), threading.Event()
    events = []

    @contextmanager
    def tracked_transaction():
        with transaction():
            assert fence._process_holds_cloud_apply_lock()
            events.append("locked")
            try:
                yield
            finally:
                events.append("finished")

    def save():
        entered.set()
        assert release.wait(5)
        (tmp_path / "saved.txt").write_text("complete", encoding="utf-8")

    task = asyncio.create_task(run_storage_mutation(tracked_transaction, save))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert events == ["locked"]
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["locked", "finished"]
    assert (tmp_path / "saved.txt").read_text(encoding="utf-8") == "complete"
    assert not fence._process_holds_cloud_apply_lock()


def test_memory_summary_respects_forget_and_requested_revision_range(tmp_path):
    session = SimpleNamespace(story_package_id='story', session_id='session', revision=2,
        forgotten_through_revision=2, catgirl_binding={'character_id': 'character', 'catgirl_name': 'Lan'},
        opening_performance={'performance': 'Forgotten opening'}, performance_history=(
            {'revision': 1, 'performance': 'First turn'}, {'revision': 2, 'performance': 'Forgotten last turn'},))
    receipt = NumericV2ArchiveStore(tmp_path).create_or_get(session)
    assert receipt['status'] == 'skipped'
    assert build_numeric_v2_memory_messages(title='Story', session=session, ending=None, include_opening=False) == []
    session.forgotten_through_revision = 0
    messages = build_numeric_v2_memory_messages(title='Story', session=session, ending={'summary': 'Later ending'},
        archive_from_revision=1, archive_through_revision=1, include_opening=False)
    assert messages[0]['content'][0]['text'] == 'First turn'


@pytest.mark.parametrize('memory_available', [True, False])
def test_deleted_package_keeps_pending_forget_discoverable_and_retryable(tmp_path, monkeypatch, memory_available):
    class MemoryClient:
        async def get(self, url, **kwargs):
            if not memory_available:
                raise OSError('memory offline')
            return SimpleNamespace(is_success=True, json=lambda: {'ok': True, 'stories': []})

        async def post(self, url, **kwargs):
            return SimpleNamespace(is_success=True, content=b'{}', json=lambda: {'ok': True})

    monkeypatch.setattr('utils.internal_http_client.get_internal_http_client', lambda: MemoryClient())
    client = _client(tmp_path, monkeypatch)
    scope = {'story_id': 'numeric_v2_contract', 'character_id': 'character_' + '1' * 32}
    store = NumericV2ArchiveStore(tmp_path / 'theater')
    with client:
        assert client.post('/api/theater-numeric/session/start', json={'story_id': scope['story_id'], 'session_id': 'forget_deleted'}).status_code == 200
        store.prepare_forget(**scope, legacy_catgirl_name='测试猫娘', session=SimpleNamespace(session_id='forget_deleted', revision=0))
        store.prepare_forget(story_id='other_story', character_id='other_character', legacy_catgirl_name='Other')
        assert client.delete('/api/theater-numeric/packages/' + scope['story_id']).status_code == 200
        pending = store.pending_forget(**scope)
        listed = client.get('/api/theater-numeric/memory/stories').json()
        assert listed == {'ok': True, 'character_id': scope['character_id'], 'memory_available': memory_available,
            'stories': [{'story_id': scope['story_id'], 'title': scope['story_id'], 'memory_summaries': [], 'forget_pending': True, 'memory_only': True}]}
        assert store.pending_forget(**scope) == pending
        assert client.post('/api/theater-numeric/memory/forget', json=scope).status_code == 200
        assert store.pending_forget(**scope) is None
        assert client.get('/api/theater-numeric/memory/stories').json()['stories'] == []
        assert client.post('/api/theater-numeric/packages/import', json=numeric_v2_story()).status_code == 200
        assert client.post('/api/theater-numeric/session/start', json={'story_id': scope['story_id'], 'session_id': 'after_retry'}).status_code == 200
    assert store.pending_forget('other_story', 'other_character') is not None


def test_deleted_story_summary_list_excludes_installed_packages(tmp_path, monkeypatch):
    rows = [dict(story_id=key, title='Title', memory_summaries=['公开摘要']) for key in ['numeric_v2_contract', 'deleted_story']]

    class MemoryClient:
        async def get(self, url, **kwargs):
            assert numeric_theater_router.character_config_mutation_lock.locked()
            return SimpleNamespace(is_success=True, json=lambda: {'ok': True, 'stories': rows})

    monkeypatch.setattr('utils.internal_http_client.get_internal_http_client', lambda: MemoryClient())
    with _client(tmp_path, monkeypatch) as client:
        listed = client.get('/api/theater-numeric/memory/stories').json()
    assert listed['stories'] == [{**rows[1], 'memory_only': True}]


def test_forget_then_exit_without_new_turn_cannot_archive_old_content(tmp_path, monkeypatch):
    memory_calls = []

    class MemoryClient:
        async def post(self, url, **kwargs):
            memory_calls.append(url)
            return SimpleNamespace(is_success=True, content=b'{}', json=lambda: {'ok': True})

    monkeypatch.setattr('utils.internal_http_client.get_internal_http_client', lambda: MemoryClient())
    scope = {'story_id': 'numeric_v2_contract', 'session_id': 'forget_exit'}
    with _client(tmp_path, monkeypatch) as client:
        assert client.post('/api/theater-numeric/session/start', json=scope).status_code == 200
        assert client.post('/api/theater-numeric/memory/forget', json={'story_id': scope['story_id'], 'character_id': 'character_' + '1' * 32}).status_code == 200
        ended = client.post('/api/theater-numeric/session/end', json={**scope, 'base_revision': 0, 'base_lifecycle_revision': 0}).json()
        assert ended['archive_status'] == 'skipped'
        response = client.post('/api/theater-numeric/session/archive', json={**scope, 'revision': 0,
            'end_receipt_id': ended['end_receipt_id'], 'archive_request_id': ended['archive_request_id']})
        assert response.status_code == 409
        assert response.json()['reason'] == 'numeric_archive_already_skipped'
    assert len(memory_calls) == 1 and memory_calls[0].endswith('/theater/forget')
