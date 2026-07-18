"""验证轻量 Runtime 的启动、推进、自由对话和事务能力。"""  # noqa: DOCSTRING_CJK

import asyncio
from copy import deepcopy
import json

import pytest

from services.theater import (
    branch_contracts,
    branch_lifecycle,
    branch_runtime,
    intent_tracker,
    model_trace,
    observability,
    rules,
    runtime,
    session_store,
    story_graph,
    story_loader,
    turn_service,
)
from tests.utils.theater_story_fixture import (
    THEATER_TEST_ANCHOR_NODE_ID,
    THEATER_TEST_EXCHANGE_NODE_ID,
    THEATER_TEST_GOAL_ID,
    THEATER_TEST_START_NODE_ID,
    THEATER_TEST_STORY_ID,
)


@pytest.mark.asyncio
async def test_choice_roleplay_restore_and_exit(tmp_path):
    """一场演出可以推进、自由回应、恢复并主动离场。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    assert started["ok"] is True
    assert started["state_revision"] == 0
    assert started["suggestion_options"]
    saved_start = await session_store.load_session(root, started["session_id"])
    assert saved_start["schema_version"] == session_store.SESSION_SCHEMA_VERSION
    assert saved_start["story_revision"]
    assert saved_start["llm_return_records"] == []
    assert saved_start["turn_causality_records"] == []

    roleplay = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="我想先听听你的心情",
        client_turn_id="turn_roleplay",
        base_revision=0,
    )
    assert roleplay["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert roleplay["suggestion_options"]

    choice = roleplay["suggestion_options"][0]
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=choice["choice_id"],
        client_turn_id="turn_choice",
        base_revision=1,
    )
    assert progressed["scenario_trace"]["progress_kind"] == "graph_progress"
    restored = await runtime.get_state(root, started["session_id"])
    assert restored["state_revision"] == 2
    assert restored["dialogue"] == progressed["dialogue"]

    exited = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_exit",
        base_revision=2,
    )
    assert exited["ending"]["reason"] == "user_exit"
    assert exited["can_resume"] is False
    assert exited["session_lifecycle"] == "ended"
    saved_exit = await session_store.load_session(root, started["session_id"])
    assert saved_exit["end_reason"] == "user_exit"


@pytest.mark.asyncio
async def test_successful_turn_persists_private_model_returns_once(
    monkeypatch, tmp_path
):
    """成功回合原子保存全部模型返回，幂等重放不重复追加且公开响应不泄漏。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "model_return_session"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", story_id=THEATER_TEST_STORY_ID
    )

    async def _fake_route(**_kwargs):
        """模拟 Router 已经经过统一模型入口并留下原始供应商正文。"""  # noqa: DOCSTRING_CJK
        model_trace.record_model_return(
            call_type="theater_router",
            surface="free_input",
            status="success",
            model="router-model",
            provider_type="openai",
            content='{"route_kind":"idle","private_trace_marker":"router-only"}',
        )
        return {
            "route_kind": "idle",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": {},
            "response_focus": {
                "focus_type": "object",
                "evidence_excerpt": "今天的云层",
                "requires_state_change": False,
            },
        }

    async def _fake_performance(**_kwargs):
        """模拟 Actor 返回被解析后，Session 仍保留解析前的完整原始正文。"""  # noqa: DOCSTRING_CJK
        model_trace.record_model_return(
            call_type="theater_actor",
            surface="roleplay_response",
            status="success",
            model="actor-model",
            provider_type="openai",
            content='{"dialogue":"公开回应","private_trace_marker":"actor-only"}',
        )
        return {
            "narration": "她顺着你的视线望向窗外。",
            "dialogue": "今天的云确实很适合慢慢看喵。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr(turn_service.llm, "route_free_input_async", _fake_route)
    monkeypatch.setattr(turn_service.llm, "generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="我想先聊一会儿今天的云层",
        client_turn_id="turn_model_trace_once",
        base_revision=0,
    )

    assert result["ok"] is True
    assert "private_trace_marker" not in json.dumps(result, ensure_ascii=False)
    saved = await session_store.load_session(root, started["session_id"])
    records = saved["llm_return_records"]
    assert [item["call_index"] for item in records] == [0, 1]
    assert [item["call_type"] for item in records] == [
        "theater_router",
        "theater_actor",
    ]
    assert [item["client_turn_id"] for item in records] == [
        "turn_model_trace_once",
        "turn_model_trace_once",
    ]
    assert all(item["session_id"] == started["session_id"] for item in records)
    assert all(item["base_revision"] == 0 for item in records)
    assert all(item["result_revision"] == 1 for item in records)
    assert "router-only" in records[0]["content"]
    assert "actor-only" in records[1]["content"]
    assert "private_trace_marker" not in json.dumps(
        saved["public_snapshot"], ensure_ascii=False
    )
    causal_records = saved["turn_causality_records"]
    assert len(causal_records) == 1
    causal = causal_records[0]
    assert causal["client_turn_id"] == "turn_model_trace_once"
    assert causal["base_revision"] == 0
    assert causal["result_revision"] == 1
    assert causal["input"] == {
        "input_kind": "free_input",
        "message": "我想先聊一会儿今天的云层",
        "choice_id": "",
    }
    assert causal["response_focus"] == {
        "focus_type": "object",
        "evidence_excerpt": "今天的云层",
        "requires_state_change": False,
    }
    assert causal["model_return_refs"] == [
        {
            "call_index": 0,
            "call_type": "theater_router",
            "surface": "free_input",
            "status": "success",
        },
        {
            "call_index": 1,
            "call_type": "theater_actor",
            "surface": "roleplay_response",
            "status": "success",
        },
    ]
    assert causal["final_public_output"] == {
        "narration": result["narration"],
        "dialogue": result["dialogue"],
        "scenario_trace": result["scenario_trace"],
        "ending": result["ending"],
    }
    assert causal["commit_summary"]["narrative_facts_added"] == []
    assert causal["commit_summary"]["branch_facts_added"] == []
    assert causal["commit_summary"]["completed_goal_ids_added"] == []
    assert causal["commit_summary"]["branch_history_entries_added"] == []
    assert causal["commit_summary"]["active_runtime_branch_before"] is False
    assert causal["commit_summary"]["active_runtime_branch_after"] is False
    assert causal["commit_summary"]["session_ended"] is False
    assert "turn_causality_records" not in json.dumps(result, ensure_ascii=False)
    assert "turn_causality_records" not in json.dumps(
        saved["public_snapshot"], ensure_ascii=False
    )

    replay = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="我想先聊一会儿今天的云层",
        client_turn_id="turn_model_trace_once",
        base_revision=0,
    )
    assert replay["ok"] is True
    replayed_session = await session_store.load_session(root, started["session_id"])
    assert len(replayed_session["llm_return_records"]) == 2
    assert len(replayed_session["turn_causality_records"]) == 1


@pytest.mark.asyncio
async def test_turn_causality_lazily_migrates_old_session_on_success(tmp_path):
    """旧 Session 缺少私有因果字段时只在下一次成功提交中补齐。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "causality_lazy_migration"
    started = await runtime.start_session(root, lanlan_name="旧档测试猫娘")
    session = await session_store.load_session(root, started["session_id"])
    session.pop("turn_causality_records")
    await session_store.save_session(root, session)

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="先确认旧存档还能继续",
        client_turn_id="turn_causality_lazy_migration",
        base_revision=0,
    )

    assert result["ok"] is True
    saved = await session_store.load_session(root, started["session_id"])
    assert len(saved["turn_causality_records"]) == 1
    assert saved["turn_causality_records"][0]["result_revision"] == 1


@pytest.mark.asyncio
async def test_turn_causality_rejects_corrupt_private_record_container(tmp_path):
    """私有因果字段类型损坏时保留原文件，不能用新回合静默覆盖证据。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "causality_corrupt_container"
    started = await runtime.start_session(root, lanlan_name="坏档测试猫娘")
    session = await session_store.load_session(root, started["session_id"])
    session["turn_causality_records"] = {"broken": True}
    await session_store.save_session(root, session)

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="这次输入不应该覆盖坏记录",
        client_turn_id="turn_causality_corrupt_container",
        base_revision=0,
    )

    assert result["ok"] is False
    assert result["reason"] == "session_state_invalid"
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["state_revision"] == 0
    assert saved["turn_causality_records"] == {"broken": True}


@pytest.mark.asyncio
async def test_turn_causality_keeps_only_latest_32_successful_turns(tmp_path):
    """私有因果记录与幂等缓存使用同一上限，长演绎不会无限放大 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "causality_retention"
    started = await runtime.start_session(root, lanlan_name="长演绎测试猫娘")

    for revision in range(33):
        result = await runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message=f"第 {revision + 1} 次普通确认",
            client_turn_id=f"turn_causality_retention_{revision}",
            base_revision=revision,
        )
        assert result["ok"] is True

    saved = await session_store.load_session(root, started["session_id"])
    records = saved["turn_causality_records"]
    assert len(records) == 32
    assert records[0]["result_revision"] == 2
    assert records[-1]["result_revision"] == 33


@pytest.mark.asyncio
async def test_long_free_input_tail_cannot_advance_authority(monkeypatch, tmp_path):
    """长输入句尾否定若无法完整进入模型，节点、意图和权威事实必须保持不变。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "long_input_context"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    before = await session_store.load_session(root, started["session_id"])
    before_state = deepcopy(before["story_state"])
    model_calls = 0

    class _ConfiguredModel:
        """提供占位模型配置，确保测试验证的是上下文门禁而非缺少配置。"""  # noqa: DOCSTRING_CJK

        def get_model_api_config(self, _tier):
            """返回不会真正使用的占位配置。"""  # noqa: DOCSTRING_CJK
            return {"model": "fake-model", "base_url": "https://example.invalid"}

    async def _unexpected_model_call(*_args, **_kwargs):
        """Router 和 Actor 都不应收到被截断的本轮原话。"""  # noqa: DOCSTRING_CJK
        nonlocal model_calls
        model_calls += 1
        raise AssertionError("truncated free input must not reach model")

    monkeypatch.setattr(turn_service.llm, "_invoke_model_once", _unexpected_model_call)
    long_message = "我准备执行当前推荐行动，" * 220 + "但是最后决定不要执行"
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=long_message,
        client_turn_id="turn_long_context_incomplete",
        base_revision=0,
        config_manager=_ConfiguredModel(),
    )
    after = await session_store.load_session(root, started["session_id"])

    assert result["ok"] is True
    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert after["story_state"]["current_node_id"] == before_state["current_node_id"]
    assert after["story_state"]["dynamic_intent"] == before_state["dynamic_intent"]
    assert after["story_state"]["narrative_facts"] == before_state["narrative_facts"]
    assert after["story_state"]["scene_notes"] == before_state["scene_notes"]
    assert all(
        long_message not in str(item)
        for item in turn_service.llm._recent_public_turns(after["turns"])
    )
    assert "最后决定不要执行" not in result["dialogue"]["text"]
    assert model_calls == 0


@pytest.mark.asyncio
async def test_long_input_prefix_cannot_reenter_next_turn_router_context(
    monkeypatch, tmp_path
):
    """首轮长输入的正向前缀不能在下一轮被截断后重新送入权威 Router。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "long_input_two_turns"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    before = await session_store.load_session(root, started["session_id"])
    long_message = "执行当前推荐行动，" * 180 + "但是最后决定不要执行"
    prompts: list[str] = []

    class _ConfiguredModel:
        """提供占位配置，第二轮才允许进入本地假模型。"""  # noqa: DOCSTRING_CJK

        def get_model_api_config(self, _tier):
            """返回不会访问网络的占位配置。"""  # noqa: DOCSTRING_CJK
            return {"model": "fake-model", "base_url": "https://example.invalid"}

    async def _fake_model_call(
        _api, _system_prompt, user_prompt, *, call_type, **_kwargs
    ):
        """验证第二轮 Prompt 不含第一轮残缺语义，并返回不推进的合法结构。"""  # noqa: DOCSTRING_CJK
        prompts.append(user_prompt)
        assert "执行当前推荐行动" not in user_prompt
        assert "最后决定不要执行" not in user_prompt
        if call_type == "theater_router":
            return type("Result", (), {"content": '{"route_kind":"idle"}'})()
        return type(
            "Result",
            (),
            {
                "content": (
                    '{"narration":"","dialogue":"我会先确认清楚再继续。",'
                    '"choice_rewrites":[]}'
                )
            },
        )()

    monkeypatch.setattr(turn_service.llm, "_invoke_model_once", _fake_model_call)
    first = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=long_message,
        client_turn_id="turn_long_prefix_first",
        base_revision=0,
        config_manager=_ConfiguredModel(),
    )
    second = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="继续",
        client_turn_id="turn_long_prefix_second",
        base_revision=first["state_revision"],
        config_manager=_ConfiguredModel(),
    )
    saved = await session_store.load_session(root, started["session_id"])

    assert second["ok"] is True
    assert (
        saved["story_state"]["current_node_id"]
        == before["story_state"]["current_node_id"]
    )
    assert saved["story_state"]["dynamic_intent"] == {}
    assert prompts


@pytest.mark.asyncio
async def test_start_uses_author_initial_scene_id(monkeypatch, tmp_path):
    """同一 phase 有多个场景时，开场必须使用作者指定的 initial_scene_id。"""  # noqa: DOCSTRING_CJK
    story = {
        "id": "scene-choice",
        "title": "场景选择测试",
        "initial_scene_id": "scene_selected",
        "opening_dialogue": "从指定场景开始喵。",
        "scenes": [
            {
                "id": "scene_wrong",
                "phase": "setup",
                "title": "错误场景",
                "text": "不应显示",
            },
            {
                "id": "scene_selected",
                "phase": "setup",
                "title": "指定场景",
                "text": "正确开场",
            },
        ],
        "narrative_nodes": [
            {
                "node_id": "node_start",
                "belong_phase": "setup",
                "node_type": "seed",
                "state_diff": {"add": []},
            }
        ],
        "edges": [],
    }

    async def _load_story(_story_id):
        """返回包含同 phase 多场景的可控故事。"""  # noqa: DOCSTRING_CJK
        return story

    monkeypatch.setattr(runtime.story_loader, "load_story", _load_story)
    started = await runtime.start_session(tmp_path / "theater", lanlan_name="测试猫娘")
    assert started["scene"] == {
        "scene_id": "scene_selected",
        "title": "指定场景",
        "text": "正确开场",
    }


@pytest.mark.asyncio
async def test_start_uses_author_opening_dialogue_without_model_rewrite(
    monkeypatch, tmp_path
):
    """正式开场对白必须逐字来自 Story，模型和框架都不能代写。"""  # noqa: DOCSTRING_CJK

    class _CurrentCatgirlConfig:
        """只提供 Runtime 角色归属校验所需的当前猫娘。"""  # noqa: DOCSTRING_CJK

        def load_characters(self):
            return {"当前猫娘": "霜瞳", "猫娘": {"霜瞳": {}}}

    async def _unexpected_opening_model(**_kwargs):
        """开场作者已提供完整对白时，不应再取得模型改写。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("作者开场对白不应交给模型改写")

    # 监控当前回合服务实际使用的模型入口，避免为了旧 runtime 挂点保留无用导入。
    monkeypatch.setattr(
        turn_service.llm, "generate_turn_async", _unexpected_opening_model
    )
    started = await runtime.start_session(
        tmp_path / "theater",
        lanlan_name="霜瞳",
        story_id=THEATER_TEST_STORY_ID,
        config_manager=_CurrentCatgirlConfig(),
    )

    story = await story_loader.load_story_exact(THEATER_TEST_STORY_ID)
    author_opening = story["opening_dialogue"]
    assert started["dialogue"]["text"] == author_opening
    assert author_opening.startswith("测试牌已经放在桌上")
    assert "公开可见的步骤" in author_opening


@pytest.mark.asyncio
async def test_graph_progress_gives_model_the_next_visible_choices(
    monkeypatch, tmp_path
):
    """人格化当前对白时必须提供下一轮按钮，避免模型省略按钮所依赖的剧情邀请。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="霜瞳", story_id=THEATER_TEST_STORY_ID
    )
    captured = {}

    async def _fake_performance(**kwargs):
        captured.update(kwargs)
        return {
            "narration": kwargs["callback"],
            "dialogue": "测试牌的编号已经确认，我们继续核对公开交换步骤。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id="choice_confirm_test_token",
        client_turn_id="turn_personalized_handoff",
        base_revision=0,
    )

    assert captured["progress_kind"] == "graph_progress"
    assert [item["choice_id"] for item in captured["choice_options"]] == [
        "choice_complete_public_exchange",
    ]
    assert [item["choice_id"] for item in result["suggestion_options"]] == [
        "choice_complete_public_exchange",
    ]
    assert result["dialogue"]["text"] == captured["node"]["scripted_dialogue"]


@pytest.mark.asyncio
async def test_management_end_and_legacy_dormancy_have_distinct_lifecycle(
    tmp_path,
):
    """管理结束不可恢复，旧版休眠存档则必须原样保留剧情并允许继续。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    manually_started = await runtime.start_session(root, lanlan_name="手动结束猫娘")
    assert (await runtime.end_session(root, session_id=manually_started["session_id"]))[
        "ok"
    ] is True
    manual_state = await runtime.get_state(root, manually_started["session_id"])
    assert manual_state["phase"] == "ended"
    assert manual_state["can_resume"] is False
    assert manual_state["session_lifecycle"] == "ended"
    saved_manual = await session_store.load_session(
        root, manually_started["session_id"]
    )
    assert saved_manual["end_reason"] == "management_end"

    dormant_started = await runtime.start_session(root, lanlan_name="休眠猫娘")
    dormant_session = await session_store.load_session(
        root, dormant_started["session_id"]
    )
    dormant_session["updated_at"] = 1
    before_phase = dormant_session["phase"]
    before_revision = dormant_session["state_revision"]
    before_story_state = deepcopy(dormant_session["story_state"])
    before_options = deepcopy(dormant_session["public_snapshot"]["suggestion_options"])
    legacy_dormant_at = 86_400_002
    # 方案 A 不再自动生成休眠；这里直接构造旧持久化字段，证明升级后仍能无损恢复。
    dormant_session["dormant_at"] = legacy_dormant_at
    dormant_session["public_snapshot"]["session_lifecycle"] = "dormant"
    await session_store.save_session(root, dormant_session)
    dormant_state = await runtime.get_state(root, dormant_started["session_id"])
    active_state = await runtime.get_active_state(root, lanlan_name="休眠猫娘")
    assert dormant_state["phase"] == before_phase
    assert dormant_state["can_resume"] is True
    assert dormant_state["session_lifecycle"] == "dormant"
    assert dormant_state["suggestion_options"] == before_options
    assert active_state["session_id"] == dormant_started["session_id"]
    assert active_state["session_lifecycle"] == "dormant"
    saved_dormant = await session_store.load_session(
        root, dormant_started["session_id"]
    )
    assert saved_dormant["dormant_at"] == legacy_dormant_at
    assert saved_dormant["ended_at"] is None
    assert saved_dormant["phase"] == before_phase
    assert saved_dormant["state_revision"] == before_revision
    assert saved_dormant["story_state"] == before_story_state
    assert saved_dormant["updated_at"] == 1
    assert saved_dormant["public_snapshot"]["can_resume"] is True
    assert saved_dormant["public_snapshot"]["suggestion_options"] == before_options


@pytest.mark.asyncio
async def test_only_successful_turn_wakes_dormant_session(tmp_path):
    """失败请求和幂等回放不能唤醒旧休眠存档，成功的新回合才可以。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    original = await session_store.load_session(root, started["session_id"])
    original["updated_at"] = 1
    legacy_dormant_at = 86_400_002
    original["dormant_at"] = legacy_dormant_at
    original["public_snapshot"]["session_lifecycle"] = "dormant"
    await session_store.save_session(root, original)

    rejected = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id="choice_that_was_never_authored",
        client_turn_id="turn_dormant_rejected",
        base_revision=0,
    )
    assert rejected == {"ok": False, "reason": "choice_not_available"}
    after_rejected = await session_store.load_session(root, started["session_id"])
    assert after_rejected["dormant_at"] == legacy_dormant_at
    assert after_rejected["state_revision"] == 0

    committed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=started["suggestion_options"][0]["choice_id"],
        client_turn_id="turn_dormant_wake",
        base_revision=0,
    )
    assert committed["ok"] is True
    assert committed["session_lifecycle"] == "active"
    assert committed["state_revision"] == 1
    after_committed = await session_store.load_session(root, started["session_id"])
    assert "dormant_at" not in after_committed
    assert after_committed["state_revision"] == 1

    cached_root = tmp_path / "cached"
    cached_started = await runtime.start_session(cached_root, lanlan_name="缓存猫娘")
    cached_request = dict(
        session_id=cached_started["session_id"],
        input_kind="free_input",
        message="这一轮已经提交",
        client_turn_id="turn_before_dormant_cache",
        base_revision=0,
    )
    first = await runtime.submit_input(cached_root, **cached_request)
    cached_session = await session_store.load_session(
        cached_root, cached_started["session_id"]
    )
    cached_session["updated_at"] = 1
    cached_session["dormant_at"] = legacy_dormant_at
    cached_session["public_snapshot"]["session_lifecycle"] = "dormant"
    await session_store.save_session(cached_root, cached_session)

    replay = await runtime.submit_input(cached_root, **cached_request)
    assert replay["state_revision"] == first["state_revision"]
    assert replay["session_lifecycle"] == "dormant"
    after_replay = await session_store.load_session(
        cached_root, cached_started["session_id"]
    )
    assert after_replay["dormant_at"] == legacy_dormant_at


@pytest.mark.asyncio
async def test_idempotency_and_revision_conflict(tmp_path):
    """重复请求回放首次结果，旧 revision 不得覆盖新状态。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    option = started["suggestion_options"][0]
    request = dict(
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=option["choice_id"],
        client_turn_id="turn_same",
        base_revision=0,
    )
    first = await runtime.submit_input(root, **request)
    replay = await runtime.submit_input(root, **request)
    assert replay == first

    conflict = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="继续聊聊",
        client_turn_id="turn_conflict",
        base_revision=0,
    )
    assert conflict == {
        "ok": False,
        "reason": "state_revision_conflict",
        "retryable": True,
        "state_revision": 1,
    }


@pytest.mark.asyncio
async def test_free_input_rejects_oversized_message_before_persisting(tmp_path):
    """超长自由输入必须在调用模型和写入 Session 前被明确拒绝。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_input_cap"
    )

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="很" * (turn_service.MAX_FREE_INPUT_CHARS + 1),
        client_turn_id="turn_oversized_input",
        base_revision=0,
    )

    assert result == {"ok": False, "reason": "free_input_too_long"}
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["state_revision"] == 0
    assert len(saved["turns"]) == 1


@pytest.mark.asyncio
async def test_cached_nonterminal_turn_cannot_revive_stale_session(tmp_path):
    """旧 Session 被替换后，同一幂等 ID 也不能回放可恢复快照。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_cache_old"
    )
    request = dict(
        session_id=old_session["session_id"],
        input_kind="free_input",
        message="这轮结果会进入幂等缓存",
        client_turn_id="turn_cached_before_replace",
        base_revision=0,
    )
    committed = await runtime.submit_input(root, **request)
    assert committed["ok"] is True
    await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_cache_new"
    )

    replay = await runtime.submit_input(root, **request)

    assert replay == {"ok": False, "reason": "stale_session", "skipped": True}


@pytest.mark.asyncio
async def test_replaced_session_stays_ended_after_replacement_closes(tmp_path):
    """新演出结束并清空 active 后，被替换的旧 Session 也不能重新恢复。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_replaced_old"
    )
    replacement = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_replaced_new"
    )
    await runtime.end_session(root, session_id=replacement["session_id"])

    restored = await runtime.get_state(root, old_session["session_id"])
    submitted = await runtime.submit_input(
        root,
        session_id=old_session["session_id"],
        input_kind="free_input",
        message="旧演出不应复活",
        client_turn_id="turn_replaced_old",
        base_revision=0,
    )

    assert restored["can_resume"] is False
    assert restored["phase"] == "ended"
    assert submitted == {"ok": False, "reason": "session_ended"}
    saved_old = await session_store.load_session(root, old_session["session_id"])
    assert saved_old["end_reason"] == "replaced_by_new_session"


@pytest.mark.asyncio
async def test_cached_terminal_turn_remains_idempotent(tmp_path):
    """主动离场已经提交后，同一幂等 ID 重试仍返回原终局响应。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_exit_cache"
    )
    request = dict(
        session_id=started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_exit_cached",
        base_revision=0,
    )

    first = await runtime.submit_input(root, **request)
    replay = await runtime.submit_input(root, **request)

    assert replay == first
    assert replay["ending"]["reason"] == "user_exit"


@pytest.mark.asyncio
async def test_concurrent_start_retry_reuses_one_session(tmp_path):
    """同一开场幂等 ID 的并发请求只能创建并返回一个 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    results = await asyncio.gather(
        runtime.start_session(
            root, lanlan_name="测试猫娘", client_start_id="start_same"
        ),
        runtime.start_session(
            root, lanlan_name="测试猫娘", client_start_id="start_same"
        ),
    )
    assert results[0] == results[1]
    assert len(await session_store.list_session_ids(root)) == 1
    saved = await session_store.load_session(root, results[0]["session_id"])
    assert saved["start_client_id"] == "start_same"


@pytest.mark.asyncio
async def test_start_rechecks_current_catgirl_after_waiting_for_character_lock(
    tmp_path,
):
    """开场等待旧角色锁期间切换猫娘后，不得创建旧角色 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"

    class _MutableConfigManager:
        """模拟角色切换在开场请求排队期间发布新当前猫娘。"""  # noqa: DOCSTRING_CJK

        current_name = "旧猫娘"

        async def aload_characters(self):
            """返回调用时已经发布的当前猫娘。"""  # noqa: DOCSTRING_CJK
            return {"当前猫娘": self.current_name}

    config_manager = _MutableConfigManager()
    async with session_store.character_guard(root, "旧猫娘"):
        start_task = asyncio.create_task(
            runtime.start_session(
                root,
                lanlan_name="旧猫娘",
                client_start_id="start_waiting_character_switch",
                config_manager=config_manager,
            )
        )
        # 让开场任务进入角色锁等待，再模拟切换事务在同一边界内发布新角色。
        await asyncio.sleep(0)
        config_manager.current_name = "新猫娘"

    result = await start_task

    assert result == {"ok": False, "reason": "session_character_mismatch"}
    assert await session_store.list_session_ids(root) == []
    assert await session_store.load_active_sessions(root) == {}


@pytest.mark.asyncio
async def test_active_index_memory_changes_only_after_persistence(
    monkeypatch, tmp_path
):
    """活动索引写盘失败时不能提前发布只存在于内存的新映射。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"

    async def _load_active_sessions(_root):
        """模拟空的磁盘活动索引。"""  # noqa: DOCSTRING_CJK
        return {}

    async def _save_active_sessions(_root, _active):
        """模拟活动索引持久化失败。"""  # noqa: DOCSTRING_CJK
        raise OSError("disk unavailable")

    monkeypatch.setattr(session_store, "load_active_sessions", _load_active_sessions)
    monkeypatch.setattr(session_store, "save_active_sessions", _save_active_sessions)
    with pytest.raises(OSError, match="disk unavailable"):
        await session_store.set_active_session(
            root,
            "持久化失败猫娘",
            "theater_00000000-0000-0000-0000-000000000001",
        )
    cache_key = session_store._active_cache_key(root, "持久化失败猫娘")
    assert cache_key not in session_store._ACTIVE_BY_ROOT_AND_LANLAN


@pytest.mark.asyncio
async def test_failed_active_publication_ends_unpublished_replacement(
    monkeypatch, tmp_path
):
    """新 Session 发布失败后必须终结，索引重建不能让未公开剧情复活。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    original = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_published"
    )

    async def _fail_active_publication(_root, _lanlan_name, _session_id):
        """模拟新 Session 已保存但活动索引无法持久化。"""  # noqa: DOCSTRING_CJK
        raise OSError("active index unavailable")

    monkeypatch.setattr(session_store, "set_active_session", _fail_active_publication)
    with pytest.raises(OSError, match="active index unavailable"):
        await runtime.start_session(
            root, lanlan_name="测试猫娘", client_start_id="start_unpublished"
        )

    session_ids = await session_store.list_session_ids(root)
    unpublished_id = next(
        session_id for session_id in session_ids if session_id != original["session_id"]
    )
    unpublished = await session_store.load_session(root, unpublished_id)
    restored_original = await session_store.load_session(root, original["session_id"])

    assert unpublished["phase"] == "ended"
    assert unpublished["ended_at"]
    assert unpublished["public_snapshot"]["can_resume"] is False
    assert restored_original["ended_at"] is None


@pytest.mark.asyncio
async def test_dialogue_speech_claims_each_revision_once(tmp_path):
    """TTS 只能取得已提交的公开猫娘对白，同一 revision 的重试不得重复播报。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    first = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
    )
    assert first["line"] == started["dialogue"]["text"]
    assert first["lanlan_name"] == "测试猫娘"

    replay = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
    )
    assert replay["skipped"] == "already_spoken"
    stale = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=1,
    )
    assert stale == {"ok": True, "skipped": "stale_revision", "state_revision": 0}


@pytest.mark.asyncio
async def test_dialogue_claim_and_new_start_share_character_boundary(
    monkeypatch, tmp_path
):
    """旧对白认领写盘完成前，同猫娘新开场不能先替换 active Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_claim_old"
    )
    real_save_session = session_store.save_session
    claim_save_entered = asyncio.Event()
    release_claim_save = asyncio.Event()

    async def _pause_claim_save(target_root, session):
        """只暂停旧 Session 的已朗读 revision 写盘，制造 active 切换竞争窗口。"""  # noqa: DOCSTRING_CJK
        if session.get("session_id") == old_session["session_id"] and session.get(
            "spoken_dialogue_revisions"
        ) == [0]:
            claim_save_entered.set()
            await release_claim_save.wait()
        await real_save_session(target_root, session)

    monkeypatch.setattr(session_store, "save_session", _pause_claim_save)
    claim_task = asyncio.create_task(
        runtime.claim_dialogue_speech(
            root, session_id=old_session["session_id"], state_revision=0
        )
    )
    await claim_save_entered.wait()
    start_task = asyncio.create_task(
        runtime.start_session(
            root, lanlan_name="测试猫娘", client_start_id="start_claim_new"
        )
    )
    done, _pending = await asyncio.wait({start_task}, timeout=0.05)

    assert not done
    release_claim_save.set()
    claim, replacement = await asyncio.gather(claim_task, start_task)
    assert claim["line"] == old_session["dialogue"]["text"]
    assert replacement["session_id"] != old_session["session_id"]


@pytest.mark.asyncio
async def test_dialogue_playback_submission_holds_character_boundary(tmp_path):
    """对白进入 TTS 管线前，新开场不能越过同一角色原子边界。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_play_old"
    )
    playback_entered = asyncio.Event()
    release_playback = asyncio.Event()

    async def _pause_playback(claim):
        """暂停 TTS 提交，验证角色锁覆盖认领返回后的原竞态窗口。"""  # noqa: DOCSTRING_CJK
        playback_entered.set()
        await release_playback.wait()
        return {"ok": True, "line": claim["line"], "audio_queued": True}

    playback_task = asyncio.create_task(
        runtime.claim_dialogue_speech(
            root,
            session_id=old_session["session_id"],
            state_revision=0,
            play=_pause_playback,
        )
    )
    await playback_entered.wait()
    start_task = asyncio.create_task(
        runtime.start_session(
            root, lanlan_name="测试猫娘", client_start_id="start_play_new"
        )
    )
    done, _pending = await asyncio.wait({start_task}, timeout=0.05)

    assert not done
    release_playback.set()
    played, replacement = await asyncio.gather(playback_task, start_task)
    assert played["audio_queued"] is True
    assert replacement["session_id"] != old_session["session_id"]


@pytest.mark.asyncio
async def test_character_publication_waits_for_dialogue_playback(tmp_path):
    """当前猫娘配置不能在旧对白仍向 TTS 提交时提前发布。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="旧猫娘", client_start_id="start_before_publish"
    )
    playback_entered = asyncio.Event()
    release_playback = asyncio.Event()
    published_names = []

    async def _pause_playback(claim):
        """暂停旧对白提交，暴露角色发布与 TTS 的竞争窗口。"""  # noqa: DOCSTRING_CJK
        playback_entered.set()
        await release_playback.wait()
        return {"ok": True, "line": claim["line"], "audio_queued": True}

    async def _publish_new_character():
        """记录配置发布时点，不读写真实角色文件。"""  # noqa: DOCSTRING_CJK
        published_names.append("新猫娘")

    playback_task = asyncio.create_task(
        runtime.claim_dialogue_speech(
            root,
            session_id=started["session_id"],
            state_revision=0,
            play=_pause_playback,
        )
    )
    await playback_entered.wait()
    switch_task = asyncio.create_task(
        runtime.publish_character_switch(
            root,
            old_lanlan_name="旧猫娘",
            publish=_publish_new_character,
        )
    )
    done, _pending = await asyncio.wait({switch_task}, timeout=0.05)

    assert not done
    assert published_names == []
    release_playback.set()
    played, switched = await asyncio.gather(playback_task, switch_task)
    assert played["audio_queued"] is True
    assert switched["cleared"] is True
    assert published_names == ["新猫娘"]
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["ended_at"]
    assert saved["end_reason"] == "character_switch"


@pytest.mark.asyncio
async def test_stale_session_dialogue_cannot_claim_tts(tmp_path):
    """被新开场替代的旧 Session 不得抢播对白或中断当前演出。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_old"
    )
    await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_new"
    )

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=old_session["session_id"],
        state_revision=0,
    )
    assert claim == {"ok": True, "skipped": "stale_session", "state_revision": 0}


@pytest.mark.asyncio
async def test_ended_session_dialogue_cannot_claim_tts(tmp_path):
    """角色切换结束并清空 active 索引后，旧 Session 仍不得认领对白。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="旧猫娘", client_start_id="start_before_switch"
    )
    assert (await runtime.end_session(root, session_id=started["session_id"]))[
        "ok"
    ] is True

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
    )

    assert claim == {"ok": True, "skipped": "stale_session", "state_revision": 0}
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["spoken_dialogue_revisions"] == []


@pytest.mark.asyncio
async def test_user_exit_does_not_create_character_dialogue_for_tts(tmp_path):
    """主动离场只显示管理态提示，不伪造角色对白或占用 TTS revision。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_terminal_tts"
    )
    exited = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_terminal_tts",
        base_revision=0,
    )

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=exited["state_revision"],
    )

    assert exited["dialogue"]["text"] == ""
    assert claim == {
        "ok": True,
        "skipped": "empty_dialogue",
        "state_revision": exited["state_revision"],
    }


@pytest.mark.asyncio
async def test_switched_character_dialogue_cannot_claim_tts(tmp_path):
    """当前猫娘变化后，旧角色对白不得写入已朗读 revision。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="旧猫娘", client_start_id="start_old_tts_character"
    )

    claim = await runtime.claim_dialogue_speech(
        root,
        session_id=started["session_id"],
        state_revision=0,
        expected_lanlan_name="新猫娘",
    )

    assert claim == {"ok": True, "skipped": "character_changed", "state_revision": 0}
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["spoken_dialogue_revisions"] == []


@pytest.mark.asyncio
async def test_turn_rechecks_stale_session_after_llm_returns(monkeypatch, tmp_path):
    """模型等待期间被新开场替换的旧 Session 不得再提交候选状态。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_old"
    )
    llm_entered = asyncio.Event()
    release_llm = asyncio.Event()

    async def _wait_for_replacement(**_kwargs):
        """暂停模型结果，给另一个窗口留下替换活动 Session 的确定窗口。"""  # noqa: DOCSTRING_CJK
        llm_entered.set()
        await release_llm.wait()
        return {
            "narration": "旧演绎不应提交。",
            "dialogue": "这句也不应保存喵。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr(
        "services.theater.llm.generate_turn_async", _wait_for_replacement
    )
    pending_turn = asyncio.create_task(
        runtime.submit_input(
            root,
            session_id=old_session["session_id"],
            input_kind="free_input",
            message="等你回应时我打开了新窗口",
            client_turn_id="turn_waiting_llm",
            base_revision=0,
        )
    )
    await llm_entered.wait()
    replacement = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_new"
    )
    release_llm.set()

    assert (await pending_turn) == {
        "ok": False,
        "reason": "stale_session",
        "skipped": True,
    }
    assert replacement["session_id"] != old_session["session_id"]
    saved_old = await session_store.load_session(root, old_session["session_id"])
    assert saved_old["state_revision"] == 0
    assert len(saved_old["turns"]) == 1
    assert saved_old["turns"][0]["text"] == old_session["dialogue"]["text"]


@pytest.mark.asyncio
async def test_turn_commit_blocks_replacement_start_until_save_finishes(
    monkeypatch, tmp_path
):
    """旧回合从 stale 校验到写盘结束前，新开场不能替换 active Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_before_commit"
    )
    real_save_session = session_store.save_session
    candidate_save_entered = asyncio.Event()
    release_candidate_save = asyncio.Event()

    async def _pause_candidate_save(target_root, session):
        """只暂停 revision 1 的候选写盘，确定性暴露 stale 校验后的替换窗口。"""  # noqa: DOCSTRING_CJK
        if (
            session.get("session_id") == started["session_id"]
            and session.get("state_revision") == 1
        ):
            candidate_save_entered.set()
            await release_candidate_save.wait()
        await real_save_session(target_root, session)

    monkeypatch.setattr(session_store, "save_session", _pause_candidate_save)
    turn_task = asyncio.create_task(
        runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message="这轮正在提交",
            client_turn_id="turn_atomic_commit",
            base_revision=0,
        )
    )
    await candidate_save_entered.wait()
    start_task = asyncio.create_task(
        runtime.start_session(
            root, lanlan_name="测试猫娘", client_start_id="start_after_commit"
        )
    )
    done, _pending = await asyncio.wait({start_task}, timeout=0.05)

    assert not done
    release_candidate_save.set()
    committed, replacement = await asyncio.gather(turn_task, start_task)
    assert committed["ok"] is True
    assert committed["state_revision"] == 1
    assert replacement["session_id"] != started["session_id"]


@pytest.mark.asyncio
async def test_turn_rechecks_current_catgirl_after_llm_returns(monkeypatch, tmp_path):
    """模型等待期间切换猫娘时，旧角色候选回合不得提交。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="旧猫娘", client_start_id="start_old_character"
    )
    llm_entered = asyncio.Event()
    release_llm = asyncio.Event()

    class _MutableConfigManager:
        """模拟角色切换先更新当前猫娘、后异步清理旧 Session 的时序。"""  # noqa: DOCSTRING_CJK

        current_name = "旧猫娘"

        async def aload_characters(self):
            """返回调用时的当前猫娘配置。"""  # noqa: DOCSTRING_CJK
            return {"当前猫娘": self.current_name}

    async def _wait_for_character_switch(**_kwargs):
        """暂停模型结果，暴露角色配置已经切换但 Session 尚未结束的窗口。"""  # noqa: DOCSTRING_CJK
        llm_entered.set()
        await release_llm.wait()
        return {
            "narration": "旧角色结果不应提交。",
            "dialogue": "这句也不应播放喵。",
            "choice_rewrites": [],
        }

    config_manager = _MutableConfigManager()
    monkeypatch.setattr(
        "services.theater.llm.generate_turn_async", _wait_for_character_switch
    )
    pending_turn = asyncio.create_task(
        runtime.submit_input(
            root,
            session_id=old_session["session_id"],
            input_kind="free_input",
            message="你回应时我切换了猫娘",
            client_turn_id="turn_waiting_character_switch",
            base_revision=0,
            config_manager=config_manager,
            expected_lanlan_name="旧猫娘",
        )
    )
    await llm_entered.wait()
    config_manager.current_name = "新猫娘"
    release_llm.set()

    assert (await pending_turn) == {"ok": False, "reason": "session_character_mismatch"}
    saved_old = await session_store.load_session(root, old_session["session_id"])
    assert saved_old["state_revision"] == 0
    assert len(saved_old["turns"]) == 1


@pytest.mark.asyncio
async def test_concurrent_turns_only_commit_one_revision(tmp_path):
    """同一 revision 的并发回合只有一个可以提交。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")

    async def submit(suffix: str):
        return await runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message=f"这是并发输入{suffix}",
            client_turn_id=f"turn_{suffix}",
            base_revision=0,
        )

    results = await asyncio.gather(submit("a"), submit("b"))
    assert sum(result.get("ok") is True for result in results) == 1
    assert (
        sum(result.get("reason") == "state_revision_conflict" for result in results)
        == 1
    )


@pytest.mark.asyncio
async def test_active_session_restores_after_memory_index_reset(tmp_path):
    """进程内索引清空后仍可从文件恢复当前演出。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(root, lanlan_name="测试猫娘")
    session_store.reset_active_sessions_for_tests()
    restored = await runtime.get_active_state(root, lanlan_name="测试猫娘")
    assert restored["ok"] is True
    assert restored["session_id"] == started["session_id"]


@pytest.mark.asyncio
async def test_restore_adds_v25_neutral_defaults_without_changing_revision(tmp_path):
    """同 schema 早期存档只补中性私有字段和 Story revision，不重演模型或推进剧情。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="迁移测试猫娘", story_id=THEATER_TEST_STORY_ID
    )
    session = await session_store.load_session(root, started["session_id"])
    original_dialogue = session["public_snapshot"]["dialogue"]
    session.pop("story_revision")
    for field in (
        "dynamic_intent",
        "pending_intent",
        "active_runtime_branch",
        "branch_facts",
        "completed_goal_ids",
        "branch_history",
    ):
        session["story_state"].pop(field)
    await session_store.save_session(root, session)

    restored = await runtime.get_state(root, started["session_id"])
    migrated = await session_store.load_session(root, started["session_id"])
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)

    assert restored["ok"] is True
    assert restored["state_revision"] == 0
    assert restored["dialogue"] == original_dialogue
    assert migrated["story_revision"] == story["story_revision"]
    assert migrated["story_state"]["dynamic_intent"] == {}
    assert migrated["story_state"]["pending_intent"] == {}
    assert migrated["story_state"]["active_runtime_branch"] == {}
    assert migrated["story_state"]["branch_facts"] == []
    assert migrated["story_state"]["completed_goal_ids"] == []
    assert migrated["story_state"]["branch_history"] == []


@pytest.mark.asyncio
async def test_restore_closes_overbudget_branch_to_saved_author_anchor(tmp_path):
    """仍有可靠服务端身份和作者锚点的损坏支线安全关闭，并移除旧动态按钮。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="修复测试猫娘", story_id=THEATER_TEST_STORY_ID
    )
    session = await session_store.load_session(root, started["session_id"])
    current_node_id = str(session["story_state"]["current_node_id"])
    patch = {
        "origin_node_id": current_node_id,
        "seed_intent": "选择当前场景允许的一件测试物品",
        "objective": "完成作者允许的公开测试交换",
        "entry_callback": "双方仍在当前场景中，交换尚未完成。",
        "turn_budget": 4,
        "content_slot_ids": ["slot_public_exchange_item"],
        "allowed_new_facts": [
            {
                "fact_type": "ordinary_test_item",
                "fact_role": "player_selected_item",
                "content_slot_id": "slot_public_exchange_item",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "catgirl_received_item",
                "content_slot_id": "",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "public_exchange_completed",
                "content_slot_id": "",
            },
        ],
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_public_exchange",
                "objective": "公开完成交换",
                "observable_action": "完成双方都明确参与的交换",
                "exit_preparation": [
                    "player_selected_item",
                    "catgirl_received_item",
                    "public_exchange_completed",
                ],
            }
        ],
        "exit_candidates": [
            {"kind": "converge", "goal_id": THEATER_TEST_GOAL_ID}
        ],
    }
    active = branch_lifecycle.build_active_runtime_branch(
        patch,
        branch_id="branch_restore_overbudget",
        created_revision=0,
        return_anchor={
            "node_id": current_node_id,
            "goal_id": THEATER_TEST_GOAL_ID,
        },
        max_nonprogress_turns=2,
    )
    active["turns_used"] = active["turn_budget"]
    session["story_state"]["active_runtime_branch"] = active
    session["story_state"]["dynamic_intent"] = {
        "intent_key": "intent_restore_private",
        "intent_summary": "仅用于确认恢复清理",
    }
    session["public_snapshot"]["suggestion_options"].append(
        {
            "choice_id": "branch_choice_stale",
            "label": "过期临时行动",
            "choice_mode": "action",
        }
    )
    original_dialogue = session["public_snapshot"]["dialogue"]
    await session_store.save_session(root, session)

    restored = await runtime.get_state(root, started["session_id"])
    repaired = await session_store.load_session(root, started["session_id"])

    assert restored["ok"] is True
    assert restored["state_revision"] == 0
    assert restored["dialogue"] == original_dialogue
    assert not any(
        item["choice_id"].startswith("branch_choice_")
        for item in restored["suggestion_options"]
    )
    assert repaired["story_state"]["current_node_id"] == current_node_id
    assert repaired["story_state"]["active_runtime_branch"] == {}
    assert repaired["story_state"]["dynamic_intent"] == {}
    assert (
        repaired["story_state"]["branch_history"][-1]["exit_kind"] == "restore_invalid"
    )
    assert repaired["story_state"]["branch_history"][-1]["ended_revision"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        ("story_revision", "session_story_revision_mismatch"),
        ("missing_story", "session_story_unavailable"),
        ("missing_anchor", "session_state_invalid"),
        ("missing_snapshot", "session_snapshot_missing"),
        ("bad_dormant_at", "session_state_invalid"),
        ("bad_end_reason", "session_state_invalid"),
        ("orphan_end_reason", "session_state_invalid"),
        ("nonstring_end_reason", "session_state_invalid"),
        ("bad_ended_at", "session_state_invalid"),
    ],
)
async def test_restore_preserves_unrepairable_session_and_active_index(
    tmp_path, mutation, reason
):
    """无法证明 Story 兼容或找不到作者锚点时保留原文件与活动索引，不猜测修复。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / mutation
    lanlan_name = f"保留测试猫娘-{mutation}"
    started = await runtime.start_session(
        root, lanlan_name=lanlan_name, story_id=THEATER_TEST_STORY_ID
    )
    session = await session_store.load_session(root, started["session_id"])
    if mutation == "story_revision":
        session["story_revision"] = "different-author-revision"
    elif mutation == "missing_story":
        # 用户移除自己提供的 Story 后，旧演出仍作为原始存档保留，不能映射到示例剧本。
        session["story_id"] = "removed_user_story"
    elif mutation == "missing_anchor":
        session["story_state"]["active_runtime_branch"] = {
            "branch_id": "branch_without_anchor",
            "patch": {},
        }
    elif mutation == "missing_snapshot":
        # 快照缺失同样不能根据私有状态临时拼出玩家已看到的最后一轮表现。
        session.pop("public_snapshot")
    elif mutation == "bad_dormant_at":
        session["dormant_at"] = "不是合法时间戳"
    elif mutation == "bad_end_reason":
        session["end_reason"] = "任意外部终止原因"
    elif mutation == "orphan_end_reason":
        session["end_reason"] = "management_end"
    elif mutation == "nonstring_end_reason":
        session["end_reason"] = ["management_end"]
    else:
        session["ended_at"] = "不是合法时间戳"
    session["updated_at"] = 1
    original = json.loads(json.dumps(session, ensure_ascii=False))
    await session_store.save_session(root, session)
    assert await session_store.load_session(root, started["session_id"]) == original
    session_store.reset_active_sessions_for_tests()

    restored = await runtime.get_active_state(root, lanlan_name=lanlan_name)
    preserved = await session_store.load_session(root, started["session_id"])

    assert restored == {
        "ok": False,
        "reason": reason,
        "session_id": started["session_id"],
    }
    assert preserved == original
    assert await session_store.load_active_sessions(root) == {
        lanlan_name: started["session_id"]
    }
    if mutation in {
        "bad_dormant_at",
        "bad_end_reason",
        "orphan_end_reason",
        "nonstring_end_reason",
        "bad_ended_at",
    }:
        submitted = await runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message="坏生命周期字段不能借输入接口被静默修复",
            client_turn_id=f"turn_{mutation}",
            base_revision=0,
        )
        assert submitted == {
            "ok": False,
            "reason": "session_state_invalid",
            "session_id": started["session_id"],
        }
        assert await session_store.load_session(root, started["session_id"]) == original
    if mutation in {"story_revision", "missing_story"}:
        submitted = await runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message="旧页面尝试继续",
            client_turn_id=f"turn_old_{mutation}",
            base_revision=0,
        )
        assert submitted == {
            "ok": False,
            "reason": reason,
            "session_id": started["session_id"],
        }
        assert await session_store.load_session(root, started["session_id"]) == original

    # 直接 API 和旧页面同样不能绕过确认；显式重开后旧文件仍必须逐字段保持原样。
    blocked_start = await runtime.start_session(
        root,
        lanlan_name=lanlan_name,
        story_id=THEATER_TEST_STORY_ID,
        client_start_id=f"start_blocked_{mutation}",
    )
    assert blocked_start == {
        "ok": False,
        "reason": reason,
        "session_id": started["session_id"],
    }
    assert await session_store.load_session(root, started["session_id"]) == original

    replacement = await runtime.start_session(
        root,
        lanlan_name=lanlan_name,
        story_id=THEATER_TEST_STORY_ID,
        client_start_id=f"start_confirmed_{mutation}",
        replace_incompatible_session=True,
    )
    assert replacement["ok"] is True
    assert replacement["session_id"] != started["session_id"]
    assert await session_store.load_session(root, started["session_id"]) == original
    assert await session_store.load_active_sessions(root) == {
        lanlan_name: replacement["session_id"]
    }


@pytest.mark.asyncio
async def test_session_state_and_input_reject_another_catgirl(tmp_path):
    """本地旧 Session ID 不能恢复或推进其他猫娘的私有演绎。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="旧猫娘", client_start_id="start_old_catgirl"
    )

    restored = await runtime.get_state(
        root,
        old_session["session_id"],
        expected_lanlan_name="当前猫娘",
    )
    submitted = await runtime.submit_input(
        root,
        session_id=old_session["session_id"],
        input_kind="free_input",
        message="继续上一位猫娘的剧情",
        client_turn_id="turn_wrong_catgirl",
        base_revision=0,
        expected_lanlan_name="当前猫娘",
    )

    assert restored == {"ok": False, "reason": "session_character_mismatch"}
    assert submitted == {"ok": False, "reason": "session_character_mismatch"}
    saved = await session_store.load_session(root, old_session["session_id"])
    assert saved["state_revision"] == 0
    assert len(saved["turns"]) == 1


@pytest.mark.asyncio
async def test_active_session_cache_is_scoped_by_theater_root(tmp_path):
    """同名猫娘在不同数据根中必须分别读取各自的活动 Session。"""  # noqa: DOCSTRING_CJK
    root_a = tmp_path / "root-a"
    root_b = tmp_path / "root-b"
    session_a = "theater_00000000-0000-0000-0000-000000000001"
    session_b = "theater_00000000-0000-0000-0000-000000000002"
    await session_store.save_active_sessions(root_a, {"同名猫娘": session_a})
    await session_store.save_active_sessions(root_b, {"同名猫娘": session_b})
    session_store.reset_active_sessions_for_tests()

    assert await session_store.get_active_session_id(root_a, "同名猫娘") == session_a
    assert await session_store.get_active_session_id(root_b, "同名猫娘") == session_b


@pytest.mark.asyncio
async def test_corrupt_active_session_index_recovers_as_empty(tmp_path):
    """损坏的活动索引不得阻断读取，并应能被下一次正常写入重建。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    path = session_store.active_sessions_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"测试猫娘":', encoding="utf-8")
    session_store.reset_active_sessions_for_tests()

    assert await session_store.load_active_sessions(root) == {}

    session_id = "theater_00000000-0000-0000-0000-000000000009"
    await session_store.set_active_session(root, "测试猫娘", session_id)
    assert await session_store.load_active_sessions(root) == {"测试猫娘": session_id}


@pytest.mark.asyncio
async def test_corrupt_active_index_rebuilds_latest_unended_session(tmp_path):
    """索引损坏后必须恢复最新未结束演出，并继续把被替换 Session 判为 stale。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    old_session = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_rebuild_old"
    )
    replacement = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_rebuild_new"
    )
    path = session_store.active_sessions_path(root)
    path.write_text('{"测试猫娘":', encoding="utf-8")
    session_store.reset_active_sessions_for_tests()

    rebuilt = await session_store.load_active_sessions(root)
    old_saved = await session_store.load_session(root, old_session["session_id"])

    assert rebuilt == {"测试猫娘": replacement["session_id"]}
    assert old_saved["ended_at"]
    assert await session_store.is_stale_session(root, old_saved) is True


@pytest.mark.asyncio
async def test_invalid_active_index_payload_rebuilds_current_session(tmp_path):
    """合法 JSON 的错误顶层结构也必须重建，不能按空索引放行历史 Session。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", client_start_id="start_invalid_index"
    )
    path = session_store.active_sessions_path(root)
    path.write_text("[]", encoding="utf-8")
    session_store.reset_active_sessions_for_tests()

    rebuilt = await session_store.load_active_sessions(root)

    assert rebuilt == {"测试猫娘": started["session_id"]}
    assert json.loads(path.read_text(encoding="utf-8")) == rebuilt


@pytest.mark.asyncio
async def test_active_session_index_serializes_updates_across_characters(
    monkeypatch, tmp_path
):
    """不同猫娘并发更新共享索引时必须保留双方映射。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    stored: dict[str, str] = {}
    load_count = 0
    first_save_entered = asyncio.Event()
    release_first_save = asyncio.Event()

    async def _load_active_sessions(_root):
        """返回当前测试索引副本，并记录并发读取次数。"""  # noqa: DOCSTRING_CJK
        nonlocal load_count
        load_count += 1
        return dict(stored)

    async def _save_active_sessions(_root, active):
        """暂停第一位猫娘的写入，制造可复现的读改写竞争窗口。"""  # noqa: DOCSTRING_CJK
        if "猫娘甲" in active and "猫娘乙" not in active:
            first_save_entered.set()
            await release_first_save.wait()
        stored.clear()
        stored.update(active)

    monkeypatch.setattr(session_store, "load_active_sessions", _load_active_sessions)
    monkeypatch.setattr(session_store, "save_active_sessions", _save_active_sessions)

    first = asyncio.create_task(
        session_store.set_active_session(
            root, "猫娘甲", "theater_00000000-0000-0000-0000-000000000001"
        )
    )
    await first_save_entered.wait()
    second = asyncio.create_task(
        session_store.set_active_session(
            root, "猫娘乙", "theater_00000000-0000-0000-0000-000000000002"
        )
    )
    await asyncio.sleep(0)
    # 第二次读必须等待第一轮完整写入，不能在旧索引上独立计算。
    assert load_count == 1
    release_first_save.set()
    await asyncio.gather(first, second)
    assert stored == {
        "猫娘甲": "theater_00000000-0000-0000-0000-000000000001",
        "猫娘乙": "theater_00000000-0000-0000-0000-000000000002",
    }


@pytest.mark.asyncio
async def test_free_input_matching_current_choice_advances_author_node(
    monkeypatch, tmp_path
):
    """自由输入明确完成当前 Choice 时复用稳定 ID 推进，不要求玩家重复点击。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", story_id=THEATER_TEST_STORY_ID
    )
    selected = started["suggestion_options"][0]
    before = await session_store.load_session(root, started["session_id"])

    async def _fake_performance(**kwargs):
        """路由提交后，演绎模型只读取目标节点，不再决定是否推进。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": kwargs["callback"],
            "dialogue": "我会从这一刻的选择继续回应你喵。",
            "choice_rewrites": [],
        }

    async def _fake_route(**kwargs):
        """模拟独立路由器从当前作者白名单中选中唯一推荐边。"""  # noqa: DOCSTRING_CJK
        return {
            "matched_choice_id": kwargs["choice_options"][0]["choice_id"],
            "observed_intent_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=selected["label"],
        client_turn_id="turn_repeat_choice_label",
        base_revision=0,
    )
    assert result["scenario_trace"] == {
        "progress_kind": "graph_progress",
        "action_label": selected["label"],
    }
    saved = await session_store.load_session(root, started["session_id"])
    assert (
        saved["story_state"]["current_node_id"]
        != before["story_state"]["current_node_id"]
    )
    assert selected["choice_id"] not in [
        item["choice_id"] for item in result["suggestion_options"]
    ]


@pytest.mark.asyncio
async def test_compound_authored_input_commits_once_and_keeps_residual_context(
    monkeypatch, tmp_path
):
    """复合输入必须先提交作者 Choice，再在目标节点回应剩余请求且不重复推荐。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    message = "确认测试牌，之后我们要不要再检查记录板？"
    author_dialogue = str(
        story_graph.node_by_id(story, THEATER_TEST_ANCHOR_NODE_ID).get(
            "scripted_dialogue"
        )
        or ""
    )

    async def _fake_route(**kwargs):
        """验证路由器收到完整原话和两条作者推荐，再返回唯一对白 Choice。"""  # noqa: DOCSTRING_CJK
        assert kwargs["user_message"] == message
        assert {item["choice_id"] for item in kwargs["choice_options"]} == {
            "choice_confirm_test_token",
            "choice_confirm_test_plan",
        }
        return {
            "route_kind": "authored_choice",
            "matched_choice_id": "choice_confirm_test_plan",
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": {
                "summary": "确认后检查记录板",
                "evidence_excerpt": "之后我们要不要再检查记录板",
            },
            "response_focus": {
                "focus_type": "question",
                "evidence_excerpt": "之后我们要不要再检查记录板",
                "requires_state_change": False,
            },
        }

    async def _fake_performance(**kwargs):
        """目标节点仍收到完整复合输入，因此可以回应后半句而不重演 Choice。"""  # noqa: DOCSTRING_CJK
        assert kwargs["node"]["node_id"] == THEATER_TEST_ANCHOR_NODE_ID
        assert kwargs["progress_kind"] == "graph_progress"
        assert kwargs["user_message"] == message
        assert kwargs["response_focus"] == {
            "focus_type": "question",
            "evidence_excerpt": "之后我们要不要再检查记录板",
            "requires_state_change": False,
        }
        return {
            "narration": kwargs["callback"],
            "dialogue": "可以，确认测试牌后我们再一起检查记录板。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=message,
        client_turn_id="turn_confirm_token_and_check_board",
        base_revision=0,
    )

    assert result["scenario_trace"]["progress_kind"] == "graph_progress"
    assert "记录板" in result["dialogue"]["text"]
    assert author_dialogue in result["dialogue"]["text"]
    assert result["dialogue"]["text"].count(author_dialogue) == 1
    assert {item["choice_id"] for item in result["suggestion_options"]} == {
        "choice_complete_public_exchange",
    }
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["completed_node_ids"].count(
        THEATER_TEST_ANCHOR_NODE_ID
    ) == 1
    assert saved["story_state"]["dynamic_intent"] == {}
    assert saved["story_state"]["pending_intent"] == {
        "summary": "确认后检查记录板",
        "evidence_excerpt": "之后我们要不要再检查记录板",
        "source_node_id": THEATER_TEST_START_NODE_ID,
        "target_node_id": THEATER_TEST_ANCHOR_NODE_ID,
        "target_scene_id": "scene_contract_setup",
        "created_revision": 1,
        "expires_revision": 2,
    }
    # Pending 是私有路由证据，不能随公开响应投影给前端。
    assert "pending_intent" not in json.dumps(result, ensure_ascii=False)
    restored = await runtime.get_state(root, started["session_id"])
    after_restore = await session_store.load_session(root, started["session_id"])
    assert restored["ok"] is True
    # 合法 Pending 必须随 Session 恢复保留，不能仅因刷新就丢失后半句。
    assert (
        after_restore["story_state"]["pending_intent"]
        == saved["story_state"]["pending_intent"]
    )


@pytest.mark.asyncio
async def test_idle_response_focus_reaches_same_node_actor_without_committing_fact(
    monkeypatch, tmp_path
):
    """普通纵向追问只增加公开回应和场景笔记，不能借焦点提交剧情事实。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    before = await session_store.load_session(root, started["session_id"])
    before_node_id = before["story_state"]["current_node_id"]
    before_facts = list(before["story_state"]["narrative_facts"])
    message = "为什么测试牌必须放在双方都能看见的位置？"
    focus = {
        "focus_type": "question",
        "evidence_excerpt": "为什么测试牌必须放在双方都能看见的位置",
        "requires_state_change": False,
    }

    async def _fake_route(**_kwargs):
        """普通追问不命中作者边，只返回有玩家原话证据的回应焦点。"""  # noqa: DOCSTRING_CJK
        return {
            "route_kind": "idle",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": {},
            "response_focus": focus,
        }

    async def _fake_performance(**kwargs):
        """同节点 Actor 必须接收焦点，但没有任何事实提交接口。"""  # noqa: DOCSTRING_CJK
        assert kwargs["progress_kind"] == "roleplay_response"
        assert kwargs["response_focus"] == focus
        return {
            "narration": "",
            "dialogue": "因为公开可见的测试牌才能让双方确认同一个结果。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=message,
        client_turn_id="turn_vertical_drilling_token_question",
        base_revision=0,
    )

    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert "双方确认" in result["dialogue"]["text"]
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == before_node_id
    assert saved["story_state"]["narrative_facts"] == before_facts
    assert saved["story_state"]["scene_notes"][-1] == message


def test_residual_evidence_must_be_a_normalized_excerpt_of_player_message():
    """Pending 只保存可由服务端回查到玩家原话的摘录，模型改写或虚构内容必须丢弃。"""  # noqa: DOCSTRING_CJK
    assert (
        turn_service._verified_residual_evidence_excerpt(
            "确认测试牌，\n然后检查记录板",
            "然后检查记录板",
        )
        == "然后检查记录板"
    )
    assert (
        turn_service._verified_residual_evidence_excerpt(
            "确认测试牌，然后检查记录板",
            "然后检查状态面板",
        )
        == ""
    )


@pytest.mark.asyncio
async def test_pending_intent_revalidates_as_first_dynamic_evidence(
    monkeypatch, tmp_path
):
    """目标节点下一回合明确确认 Pending 时，两条玩家证据可在当轮进入 Planner。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    session = await session_store.load_session(root, started["session_id"])
    state = rules.initial_state(
        story, initial_node_id=story_loader.initial_node_id(story)
    )
    rules.apply_node(
        story, state, story_graph.node_by_id(story, THEATER_TEST_ANCHOR_NODE_ID)
    )
    state["pending_intent"] = {
        "summary": "确认后检查记录板",
        "evidence_excerpt": "然后检查记录板",
        "source_node_id": THEATER_TEST_START_NODE_ID,
        "target_node_id": THEATER_TEST_ANCHOR_NODE_ID,
        "target_scene_id": "scene_contract_setup",
        "created_revision": 1,
        "expires_revision": 2,
    }
    session["story_state"] = state
    session["phase"] = "setup"
    session["state_revision"] = 1
    session["turns"] = []
    await session_store.save_session(root, session)

    async def _fake_route(**kwargs):
        """确认 Router 只在正确目标节点看到待重验摘要，并返回本轮的新证据。"""  # noqa: DOCSTRING_CJK
        assert kwargs["state"]["pending_intent"]["summary"] == "确认后检查记录板"
        return {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": "确认后检查记录板",
                "relation": "continue",
                "confidence": 0.95,
            },
            "residual_intent": {},
        }

    async def _fake_performance(**_kwargs):
        """Planner 拒绝后公开演绎保持普通安全回应。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": "",
            "dialogue": "好，我们再检查一次记录板。",
            "choice_rewrites": [],
        }

    planner_calls: list[dict] = []

    async def _fake_plan(**kwargs):
        """记录明确确认已经当轮进入 Planner，同时避免本用例重复构造完整 Patch。"""  # noqa: DOCSTRING_CJK
        planner_calls.append(kwargs)
        return {"ok": False, "reason": "patch_invalid"}

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch", _fake_plan
    )
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="对，确认后再检查记录板",
        client_turn_id="turn_revalidate_pending_board_check",
        base_revision=1,
    )

    saved = await session_store.load_session(root, started["session_id"])
    assert result["ok"] is True
    assert saved["story_state"]["pending_intent"] == {}
    assert saved["story_state"]["dynamic_intent"]["streak"] == 2
    assert saved["story_state"]["dynamic_intent"]["evidence_messages"] == [
        "然后检查记录板",
        "对，确认后再检查记录板",
    ]
    assert (
        intent_tracker.should_plan_branch(
            saved["story_state"]["dynamic_intent"],
            current_node_id=saved["story_state"]["current_node_id"],
        )
        is True
    )
    assert len(planner_calls) == 1


@pytest.mark.asyncio
async def test_expired_pending_intent_is_removed_before_router(monkeypatch, tmp_path):
    """超过短期 revision 的 Pending 必须在模型调用前清除，不能在后续节点意外复活。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    session = await session_store.load_session(root, started["session_id"])
    state = rules.initial_state(
        story, initial_node_id=story_loader.initial_node_id(story)
    )
    rules.apply_node(
        story, state, story_graph.node_by_id(story, THEATER_TEST_ANCHOR_NODE_ID)
    )
    state["pending_intent"] = {
        "summary": "确认后检查记录板",
        "evidence_excerpt": "然后检查记录板",
        "source_node_id": THEATER_TEST_START_NODE_ID,
        "target_node_id": THEATER_TEST_ANCHOR_NODE_ID,
        "target_scene_id": "scene_contract_setup",
        "created_revision": 0,
        "expires_revision": 1,
    }
    session["story_state"] = state
    session["phase"] = "setup"
    session["state_revision"] = 2
    session["turns"] = []
    await session_store.save_session(root, session)

    restored = await runtime.get_state(root, started["session_id"])
    after_restore = await session_store.load_session(root, started["session_id"])
    assert restored["ok"] is True
    assert after_restore["story_state"]["pending_intent"] == {}

    async def _fake_route(**kwargs):
        """模型只能看到清理后的空 Pending，证明过期判断位于 Router 之前。"""  # noqa: DOCSTRING_CJK
        assert kwargs["state"]["pending_intent"] == {}
        return {
            "route_kind": "idle",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": {},
        }

    async def _fake_performance(**_kwargs):
        """过期清理不改变普通闲聊的公开演绎链。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": "",
            "dialogue": "先看看眼前的测试牌吧。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="先聊聊眼前的测试牌",
        client_turn_id="turn_after_pending_expired",
        base_revision=2,
    )

    saved = await session_store.load_session(root, started["session_id"])
    assert result["ok"] is True
    assert saved["story_state"]["pending_intent"] == {}


@pytest.mark.asyncio
async def test_repeated_free_intent_atomically_activates_validated_branch(
    monkeypatch, tmp_path
):
    """第二次连续意图在 Patch 合法后以可提交入口演出原子激活支线。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)

    async def _fake_route(**kwargs):
        """返回阶段二将消费的通用自由意图语义，不提供任何模型状态 ID。"""  # noqa: DOCSTRING_CJK
        assert {item["choice_id"] for item in kwargs["choice_options"]} == {
            "choice_confirm_test_token",
            "choice_confirm_test_plan",
        }
        relation = "new" if kwargs["user_message"].startswith("我想") else "refine"
        response_focus = {
            "focus_type": "action",
            "evidence_excerpt": kwargs["user_message"],
            # 第一句只是提出想法，第二句才明确选定并准备送出。
            "requires_state_change": relation == "refine",
        }
        return {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": "改用备用测试片完成公开交换",
                "relation": relation,
                "confidence": 0.96,
            },
            "response_focus": response_focus,
        }

    async def _fake_performance(**_kwargs):
        """第一次意图仍使用普通演绎；支线入口成功后不得再次调用该路径。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": "",
            "dialogue": "备用测试片还在桌边，等你确认是否要用它。",
            "choice_rewrites": [],
        }

    patch = {
        "origin_node_id": THEATER_TEST_START_NODE_ID,
        "seed_intent": "改用备用测试片完成公开交换",
        "objective": "围绕玩家选择的测试物件完成一次公开交换",
        "entry_callback": "玩家仍把刚才选中的测试片留在手中，双方尚未完成交换。",
        "turn_budget": 4,
        "content_slot_ids": ["slot_public_exchange_item"],
        "allowed_new_facts": [
            {
                "fact_type": "ordinary_test_item",
                "fact_role": "player_selected_item",
                "content_slot_id": "slot_public_exchange_item",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "catgirl_received_item",
                "content_slot_id": "",
            },
            {
                "fact_type": "observable_action",
                "fact_role": "public_exchange_completed",
                "content_slot_id": "",
            },
        ],
        "forbidden_assumptions": [],
        "beat_outline": [
            {
                "beat_id": "beat_confirm_selection",
                "objective": "确认玩家仍选择这枚测试片",
                "observable_action": "玩家选中的测试片仍留在手中",
                "player_choice_label": "拿起备用测试片并公开确认",
                "exit_preparation": ["player_selected_item"],
            },
            {
                "beat_id": "beat_complete_exchange",
                "objective": "公开完成双方交换",
                "observable_action": "把选好的测试片递给对方并共同确认",
                "player_choice_label": "把测试片递给她并共同确认结果",
                "exit_preparation": [
                    "catgirl_received_item",
                    "public_exchange_completed",
                ],
            },
        ],
        "exit_candidates": [
            {"kind": "converge", "goal_id": THEATER_TEST_GOAL_ID}
        ],
    }
    planner_calls: list[dict] = []
    entry_calls: list[dict] = []

    async def _fake_plan(**kwargs):
        """返回已经过合同校验的隔离 Patch，并记录 Planner 只在阈值回合触发。"""  # noqa: DOCSTRING_CJK
        planner_calls.append(kwargs)
        assert kwargs["dynamic_intent"]["streak"] == 2
        return {"ok": True, "patch": patch}

    async def _fake_branch_entry(**kwargs):
        """模拟入口 Actor 连续失败后的安全演出，验证它与活动状态同 revision 提交。"""  # noqa: DOCSTRING_CJK
        entry_calls.append(kwargs)
        assert kwargs["patch"] == patch
        assert not kwargs["state"].get("active_runtime_branch")
        assert kwargs["response_focus"] == {
            "focus_type": "action",
            "evidence_excerpt": "对，就用这枚备用测试片",
            "requires_state_change": True,
        }
        # 直接复用生产回退构造器，确保集成测试覆盖真实的通用无事实入口结果。
        return turn_service.llm.fallback_branch_entry(
            scene_title=str(kwargs["scene"].get("title") or ""),
            activity_summary=str(patch.get("seed_intent") or ""),
        )

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch", _fake_plan
    )
    monkeypatch.setattr(
        "services.theater.llm.generate_branch_entry_async", _fake_branch_entry
    )
    observability.reset_evaluation_window()
    first = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="我想改用备用测试片",
        client_turn_id="turn_alternate_item_1",
        base_revision=0,
    )
    # 网络重试使用同一 client_turn_id，必须直接返回缓存且不能把 streak 重复加到 2。
    retried = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="我想改用备用测试片",
        client_turn_id="turn_alternate_item_1",
        base_revision=0,
    )
    after_retry = await session_store.load_session(root, started["session_id"])
    assert first == retried
    assert after_retry["story_state"]["dynamic_intent"]["streak"] == 1

    second = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="对，就用这枚备用测试片",
        client_turn_id="turn_alternate_item_2",
        base_revision=1,
    )
    assert second["ok"] is True

    saved = await session_store.load_session(root, started["session_id"])
    dynamic_intent = saved["story_state"]["dynamic_intent"]
    assert dynamic_intent["intent_key"].startswith("intent_")
    assert dynamic_intent["intent_summary"] == "改用备用测试片完成公开交换"
    assert dynamic_intent["origin_node_id"] == THEATER_TEST_START_NODE_ID
    assert dynamic_intent["streak"] == 2
    assert dynamic_intent["evidence_messages"] == [
        "我想改用备用测试片",
        "对，就用这枚备用测试片",
    ]
    assert (
        intent_tracker.should_plan_branch(
            dynamic_intent,
            current_node_id=saved["story_state"]["current_node_id"],
        )
        is True
    )
    active = saved["story_state"]["active_runtime_branch"]
    assert active["branch_id"].startswith("branch_")
    assert active["created_revision"] == 2
    assert active["patch"] == patch
    assert active["return_anchor"] == {
        "node_id": THEATER_TEST_START_NODE_ID,
        "goal_id": THEATER_TEST_GOAL_ID,
    }
    assert active["turns_used"] == 0
    assert active["nonprogress_turns"] == 0
    assert len(planner_calls) == 1
    assert len(entry_calls) == 1
    turn_metrics = observability.evaluation_report()["turn_submits"][
        "by_surface_outcome"
    ]
    assert turn_metrics["roleplay_response:success"]["submits"] == 1
    assert turn_metrics["idempotent_replay:idempotent_replay"]["submits"] == 1
    assert turn_metrics["branch_entry:success"]["submits"] == 1
    assert second["narration"]["text"] == ""
    assert second["dialogue"]["text"].startswith("我们还在")
    assert "后面的事还没有发生" in second["dialogue"]["text"]
    assert second["scenario_trace"]["progress_kind"] == "roleplay_response"
    # 私有意图、Patch 和活动支线身份不能进入公开响应或被前端当作权威剧情事实。
    assert "dynamic_intent" not in json.dumps(second, ensure_ascii=False)
    assert "active_runtime_branch" not in json.dumps(second, ensure_ascii=False)
    assert THEATER_TEST_GOAL_ID not in json.dumps(second, ensure_ascii=False)
    branch_options = [
        item
        for item in second["suggestion_options"]
        if item["choice_id"].startswith("branch_choice_")
    ]
    assert len(branch_options) == 1
    assert branch_options == [
        {
            "choice_id": branch_options[0]["choice_id"],
            "label": "拿起备用测试片并公开确认",
            "choice_mode": "action",
        }
    ]
    # 活动支线只展示当前玩家行动，避免已提交选择旁边继续出现互相竞争的主线物件。
    assert second["suggestion_options"] == branch_options
    restored_branch = await runtime.get_state(root, started["session_id"])
    assert restored_branch["suggestion_options"] == second["suggestion_options"]

    branch_turn_outputs = [
        [
            {
                "goal_id": THEATER_TEST_GOAL_ID,
                "fact_type": "ordinary_test_item",
                "fact_role": "player_selected_item",
                "subject": "player",
                "predicate": "selected_item",
                "object": "alternate_test_item",
                "content_slot_id": "slot_public_exchange_item",
                "public_entity": {
                    "kind": "prop",
                    "label": "已公开选择的测试片",
                    "status": "selected",
                },
            }
        ],
        [
            {
                "goal_id": THEATER_TEST_GOAL_ID,
                "fact_type": "observable_action",
                "fact_role": "catgirl_received_item",
                "subject": "active_catgirl",
                "predicate": "received_item",
                "object": "alternate_test_item",
                "content_slot_id": "",
            },
            {
                "goal_id": THEATER_TEST_GOAL_ID,
                "fact_type": "observable_action",
                "fact_role": "public_exchange_completed",
                "subject": "pair",
                "predicate": "completed_exchange",
                "object": "test_items",
                "content_slot_id": "",
            },
        ],
    ]
    branch_turn_calls: list[dict] = []

    async def _fake_branch_turn(**kwargs):
        """依次提交选择事实与交换事实，验证后续回合不重新经过 Router 或 Planner。"""  # noqa: DOCSTRING_CJK
        branch_turn_calls.append(kwargs)
        return {
            "narration": "双方继续完成眼前可见的选择与交换。",
            "dialogue": "好，这一步已经说清楚了，我们继续吧喵。",
            "fact_candidates": branch_turn_outputs.pop(0),
        }

    async def _unexpected_route(**_kwargs):
        """活动支线后续输入若重新调用 Router，测试必须立即失败。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("active branch must bypass Router")

    async def _unexpected_plan(**_kwargs):
        """Patch 激活后若重复调用 Planner，测试必须立即失败。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("active branch must not replan")

    monkeypatch.setattr(
        "services.theater.llm.generate_branch_turn_async", _fake_branch_turn
    )
    monkeypatch.setattr(
        "services.theater.llm.route_free_input_async", _unexpected_route
    )
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch",
        _unexpected_plan,
    )

    forged = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id="branch_choice_forged",
        client_turn_id="turn_forged_branch_choice",
        base_revision=2,
    )
    after_forged = await session_store.load_session(root, started["session_id"])
    assert forged == {"ok": False, "reason": "choice_not_available"}
    assert after_forged["state_revision"] == 2
    assert branch_turn_calls == []

    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=branch_options[0]["choice_id"],
        client_turn_id="turn_alternate_item_branch_1",
        base_revision=2,
    )
    after_progress = await session_store.load_session(root, started["session_id"])
    assert progressed["ok"] is True
    assert after_progress["story_state"]["active_runtime_branch"]["turns_used"] == 1
    assert (
        after_progress["story_state"]["active_runtime_branch"]["nonprogress_turns"] == 0
    )
    assert len(after_progress["story_state"]["branch_facts"]) == 1
    assert after_progress["story_state"]["branch_facts"][0]["fact_id"].startswith(
        "branch_fact_"
    )
    dynamic_available = [
        item
        for item in progressed["scenario_board"]["available_props"]
        if item["label"] == "已公开选择的测试片"
    ]
    assert len(dynamic_available) == 1
    assert dynamic_available[0]["id"].startswith("branch_entity_")
    # Projector 只返回既有 Board 形状，不能把 Branch Fact 身份和 revision 暴露给前端。
    assert set(dynamic_available[0]) == {"id", "label", "public_hint"}
    next_branch_options = [
        item
        for item in progressed["suggestion_options"]
        if item["choice_id"].startswith("branch_choice_")
    ]
    assert len(next_branch_options) == 1
    assert next_branch_options[0]["label"] == "把测试片递给她并共同确认结果"
    assert next_branch_options[0]["choice_id"] != branch_options[0]["choice_id"]
    assert progressed["scenario_trace"]["action_label"] == branch_options[0]["label"]

    converged = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=next_branch_options[0]["choice_id"],
        client_turn_id="turn_alternate_item_branch_2",
        base_revision=3,
    )
    after_convergence = await session_store.load_session(root, started["session_id"])
    assert converged["ok"] is True
    assert len(branch_turn_calls) == 2
    assert [item["user_message"] for item in branch_turn_calls] == [
        "拿起备用测试片并公开确认",
        "把测试片递给她并共同确认结果",
    ]
    assert after_convergence["story_state"]["active_runtime_branch"] == {}
    assert after_convergence["story_state"]["completed_goal_ids"] == [
        THEATER_TEST_GOAL_ID
    ]
    assert (
        after_convergence["story_state"]["current_node_id"]
        == THEATER_TEST_EXCHANGE_NODE_ID
    )
    assert len(after_convergence["story_state"]["branch_facts"]) == 3
    assert (
        after_convergence["story_state"]["branch_history"][-1]["exit_kind"]
        == "goal_converged"
    )
    assert after_convergence["story_state"]["branch_history"][-1]["ended_revision"] == 4
    assert converged["narration"]["text"] == next(
        goal["fallback_convergence_callback"]
        for goal in story["narrative_goals"]
        if goal["goal_id"] == THEATER_TEST_GOAL_ID
    )
    assert "branch_fact_" not in json.dumps(converged, ensure_ascii=False)
    assert any(
        item["label"] == "已公开选择的测试片"
        for item in converged["scenario_board"]["available_props"]
    )


@pytest.mark.asyncio
async def test_branch_technical_degradation_commits_text_without_consuming_budget(
    monkeypatch, tmp_path
):
    """模型不可用与 Fact 合同拒绝都可提交安全文字，但预算、事实和动态按钮保持不变。"""  # noqa: DOCSTRING_CJK

    async def _continue_existing_branch(**kwargs):
        """本测试固定验证 Branch Actor 降级，不让新增 handoff 分类器提前截断该覆盖。"""  # noqa: DOCSTRING_CJK
        user_message = str(kwargs.get("user_message") or "")
        return {
            "classification": "continue_branch",
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": 0.98,
            "response_focus": {
                "focus_type": "action",
                "evidence_excerpt": user_message,
                "requires_state_change": True,
            },
            "route_delivery": "accepted",
        }

    monkeypatch.setattr(
        turn_service.llm,
        "classify_active_branch_handoff_async",
        _continue_existing_branch,
    )

    async def _prepare_active_session(root, suffix):
        """构造带稳定动态按钮和已有预算计数的活动支线，供两类技术故障复用。"""  # noqa: DOCSTRING_CJK
        started = await runtime.start_session(
            root,
            lanlan_name=f"技术降级猫娘{suffix}",
            story_id=THEATER_TEST_STORY_ID,
        )
        session = await session_store.load_session(root, started["session_id"])
        current_node_id = str(session["story_state"]["current_node_id"])
        active = branch_lifecycle.build_active_runtime_branch(
            {
                "origin_node_id": current_node_id,
                "turn_budget": 4,
                "beat_outline": [
                    {
                        "beat_id": "beat_keep_stable",
                        "player_choice_label": "继续确认眼前的行动",
                        "exit_preparation": ["fact_role_not_yet_committed"],
                    }
                ],
            },
            branch_id=f"branch_degraded_{suffix}",
            created_revision=0,
            return_anchor={"node_id": current_node_id, "goal_id": ""},
            max_nonprogress_turns=2,
        )
        active["turns_used"] = 1
        active["nonprogress_turns"] = 1
        session["story_state"]["active_runtime_branch"] = active
        await session_store.save_session(root, session)
        expected_options = branch_runtime.dynamic_choice_options(active, [])
        assert len(expected_options) == 1
        # Projector 会删除私有 beat_id；稳定性只比较公开按钮的三个固定字段。
        public_options = [
            {
                "choice_id": item["choice_id"],
                "label": item["label"],
                "choice_mode": item["choice_mode"],
            }
            for item in expected_options
        ]
        return started, public_options

    fallback_root = tmp_path / "model_fallback"
    fallback_started, fallback_options = await _prepare_active_session(
        fallback_root, "model"
    )
    fallback_result = await runtime.submit_input(
        fallback_root,
        session_id=fallback_started["session_id"],
        input_kind="free_input",
        message="先继续眼前这一步",
        client_turn_id="turn_model_technical_degraded",
        base_revision=0,
        config_manager=None,
    )
    fallback_saved = await session_store.load_session(
        fallback_root, fallback_started["session_id"]
    )
    fallback_active = fallback_saved["story_state"]["active_runtime_branch"]
    assert fallback_result["ok"] is True
    assert fallback_saved["state_revision"] == 1
    assert fallback_active["turns_used"] == 1
    assert fallback_active["nonprogress_turns"] == 1
    assert fallback_saved["story_state"]["branch_facts"] == []
    assert fallback_result["suggestion_options"] == fallback_options
    assert "turn_delivery" not in json.dumps(fallback_result, ensure_ascii=False)
    assert "turn_delivery" not in json.dumps(fallback_saved, ensure_ascii=False)

    # 相同幂等 ID 只回放首次公开结果，不能重复增加 revision 或改变预算。
    replayed = await runtime.submit_input(
        fallback_root,
        session_id=fallback_started["session_id"],
        input_kind="free_input",
        message="先继续眼前这一步",
        client_turn_id="turn_model_technical_degraded",
        base_revision=0,
        config_manager=None,
    )
    replayed_saved = await session_store.load_session(
        fallback_root, fallback_started["session_id"]
    )
    assert replayed == fallback_result
    assert replayed_saved["state_revision"] == 1
    assert replayed_saved["story_state"]["active_runtime_branch"] == fallback_active

    contract_root = tmp_path / "fact_contract_rejected"
    contract_started, contract_options = await _prepare_active_session(
        contract_root, "contract"
    )
    invalid_fact_actor_calls = 0

    async def _invalid_fact_actor(**_kwargs):
        """模拟结构层收到非法 Fact 候选形状，验证整份 Actor 输出被安全替换。"""  # noqa: DOCSTRING_CJK
        nonlocal invalid_fact_actor_calls
        invalid_fact_actor_calls += 1
        return {
            "narration": "这段越权旁白不能公开。",
            "dialogue": "这段越权对白不能公开。",
            "fact_candidates": {"invalid": True},
        }

    monkeypatch.setattr(
        "services.theater.llm.generate_branch_turn_async", _invalid_fact_actor
    )
    contract_result = await runtime.submit_input(
        contract_root,
        session_id=contract_started["session_id"],
        input_kind="free_input",
        message="我已经完成眼前这一步",
        client_turn_id="turn_fact_contract_degraded",
        base_revision=0,
    )
    contract_saved = await session_store.load_session(
        contract_root, contract_started["session_id"]
    )
    contract_active = contract_saved["story_state"]["active_runtime_branch"]
    assert contract_result["ok"] is True
    assert contract_saved["state_revision"] == 1
    assert contract_active["turns_used"] == 1
    assert contract_active["nonprogress_turns"] == 1
    assert contract_saved["story_state"]["branch_facts"] == []
    assert contract_result["suggestion_options"] == contract_options
    assert "越权" not in json.dumps(contract_result, ensure_ascii=False)
    assert invalid_fact_actor_calls == 1
    result_counts = observability.evaluation_report()["result_counts"]
    assert result_counts["generation:branch_turn"]["safe_fallback"] >= 1


@pytest.mark.asyncio
async def test_branch_activation_failure_keeps_ordinary_turn_without_half_state(
    monkeypatch, tmp_path
):
    """Planner 拒绝或入口 Actor 失败时仍提交普通回应，但绝不保存半激活 Patch。"""  # noqa: DOCSTRING_CJK

    async def _prepare_session(root, suffix):
        """为独立测试 Session 预置一次连续自由意图。"""  # noqa: DOCSTRING_CJK
        started = await runtime.start_session(
            root, lanlan_name=f"测试猫娘{suffix}", story_id=THEATER_TEST_STORY_ID
        )
        session = await session_store.load_session(root, started["session_id"])
        state = session["story_state"]
        state["dynamic_intent"] = {
            "intent_key": f"intent_{suffix}",
            "intent_summary": "改用备用测试片",
            "origin_node_id": THEATER_TEST_START_NODE_ID,
            "streak": 1,
            "evidence_messages": ["先看看备用测试片"],
            "relation": "new",
        }
        session["turns"] = []
        await session_store.save_session(root, session)
        return started

    async def _fake_route(**_kwargs):
        """让预置意图在本轮达到第二次连续阈值。"""  # noqa: DOCSTRING_CJK
        return {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": "改用备用测试片",
                "relation": "continue",
                "confidence": 0.95,
            },
        }

    async def _fake_performance(**_kwargs):
        """失败回退仍走普通角色互动，不伪装成已经进入支线。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": "",
            "dialogue": "我听见了，我们先把眼前的选择说清楚喵。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    cases = ("planner_rejected", "actor_failed")
    for suffix in cases:
        root = tmp_path / suffix
        started = await _prepare_session(root, suffix)

        async def _fake_plan(**_kwargs):
            """第一种情况直接拒绝；第二种情况提供最小候选以进入 Actor 失败分支。"""  # noqa: DOCSTRING_CJK
            if suffix == "planner_rejected":
                return {"ok": False, "reason": "patch_invalid"}
            return {
                "ok": True,
                "patch": {
                    "origin_node_id": THEATER_TEST_START_NODE_ID,
                    "entry_callback": "双方仍停在公开测试桌前。",
                    "turn_budget": 4,
                    "exit_candidates": [
                        {"kind": "converge", "goal_id": THEATER_TEST_GOAL_ID}
                    ],
                },
            }

        async def _fake_branch_entry(**_kwargs):
            """严格入口 Actor 失败时返回空结果，调用方不得使用普通 fallback 激活。"""  # noqa: DOCSTRING_CJK
            return None

        monkeypatch.setattr(
            "services.theater.branch_planner.plan_validated_runtime_branch", _fake_plan
        )
        monkeypatch.setattr(
            "services.theater.llm.generate_branch_entry_async", _fake_branch_entry
        )
        result = await runtime.submit_input(
            root,
            session_id=started["session_id"],
            input_kind="free_input",
            message="对，就继续使用备用测试片",
            client_turn_id=f"turn_{suffix}",
            base_revision=0,
        )
        saved = await session_store.load_session(root, started["session_id"])
        assert result["ok"] is True
        assert result["dialogue"]["text"].startswith("我听见了")
        assert saved["state_revision"] == 1
        assert saved["story_state"]["dynamic_intent"]["streak"] == 2
        assert not saved["story_state"].get("active_runtime_branch")


@pytest.mark.asyncio
async def test_stale_author_button_is_rejected_and_user_exit_still_closes_branch(
    tmp_path,
):
    """活动支线隐藏后的旧作者按钮必须失效；玩家主动离场仍能关闭支线。"""  # noqa: DOCSTRING_CJK

    async def _started_with_active_branch(root, suffix):
        """在初始作者节点布置一个零消耗活动支线，供两种高优先级退出路径复用。"""  # noqa: DOCSTRING_CJK
        started = await runtime.start_session(
            root,
            lanlan_name=f"退出测试猫娘{suffix}",
            story_id=THEATER_TEST_STORY_ID,
        )
        session = await session_store.load_session(root, started["session_id"])
        current_node_id = str(session["story_state"]["current_node_id"])
        session["story_state"]["active_runtime_branch"] = (
            branch_lifecycle.build_active_runtime_branch(
                {"origin_node_id": current_node_id, "turn_budget": 4},
                branch_id=f"branch_exit_{suffix}",
                created_revision=0,
                return_anchor={"node_id": current_node_id, "goal_id": ""},
                max_nonprogress_turns=2,
            )
        )
        await session_store.save_session(root, session)
        return started

    choice_root = tmp_path / "author_choice_exit"
    choice_started = await _started_with_active_branch(choice_root, "choice")
    stale_result = await runtime.submit_input(
        choice_root,
        session_id=choice_started["session_id"],
        input_kind="choice",
        choice_id=choice_started["suggestion_options"][0]["choice_id"],
        client_turn_id="turn_stale_author_choice_rejected",
        base_revision=0,
    )
    after_stale = await session_store.load_session(
        choice_root, choice_started["session_id"]
    )
    assert stale_result == {"ok": False, "reason": "choice_not_available"}
    assert after_stale["story_state"]["active_runtime_branch"]
    assert after_stale["state_revision"] == 0

    exit_root = tmp_path / "user_exit_branch"
    exit_started = await _started_with_active_branch(exit_root, "user")
    exit_result = await runtime.submit_input(
        exit_root,
        session_id=exit_started["session_id"],
        input_kind="user_exit",
        client_turn_id="turn_user_exit_closes_branch",
        base_revision=0,
    )
    exit_saved = await session_store.load_session(exit_root, exit_started["session_id"])
    assert exit_result["ending"]["reason"] == "user_exit"
    assert exit_saved["story_state"]["active_runtime_branch"] == {}
    assert exit_saved["story_state"]["branch_history"][-1]["exit_kind"] == "user_exit"
    assert exit_saved["story_state"]["branch_history"][-1]["ended_revision"] == 1


@pytest.mark.asyncio
async def test_idle_route_dormants_and_explicit_continuation_resumes_intent(
    monkeypatch, tmp_path
):
    """一次普通闲聊只休眠旧意图，随后明确继续可恢复身份并当轮进入 Planner。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    session = await session_store.load_session(root, started["session_id"])
    current_node_id = session["story_state"]["current_node_id"]
    # 直接布置一次已保存意图，再让 Router 明确返回真实语义 idle，避免与技术降级混淆。
    session["story_state"]["dynamic_intent"] = {
        "intent_key": "intent_server_1",
        "intent_summary": "改用备用测试片完成公开交换",
        "origin_node_id": current_node_id,
        "streak": 1,
        "evidence_messages": ["看看备用测试片"],
        "relation": "new",
    }
    await session_store.save_session(root, session)

    async def _idle_route(**_kwargs):
        """返回结构合法的普通闲聊分类，明确消费一次短期线程宽限。"""  # noqa: DOCSTRING_CJK
        return {
            "route_kind": "idle",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {},
            "residual_intent": {},
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _idle_route)

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="先随便聊聊天吧",
        client_turn_id="turn_idle_after_dynamic_intent",
        base_revision=0,
    )

    saved = await session_store.load_session(root, started["session_id"])
    assert result["ok"] is True
    dormant = saved["story_state"]["dynamic_intent"]
    assert dormant["intent_key"] == "intent_server_1"
    assert dormant["streak"] == 1
    assert dormant["thread_state"] == "dormant"
    assert (
        intent_tracker.should_plan_branch(
            dormant,
            current_node_id=saved["story_state"]["current_node_id"],
        )
        is False
    )

    async def _fake_route(**_kwargs):
        """明确承接休眠线程，Router 只返回语义关系，不接触服务端身份或次数。"""  # noqa: DOCSTRING_CJK
        return {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": "改用备用测试片完成公开交换",
                "relation": "continue",
                "confidence": 0.95,
            },
            "residual_intent": {},
        }

    async def _fake_performance(**_kwargs):
        """Planner 未接受 Patch 时仍提交普通演绎，不影响本用例的意图线程断言。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": "",
            "dialogue": "好，我们继续确认备用测试片。",
            "choice_rewrites": [],
        }

    planner_calls: list[dict] = []

    async def _fake_plan(**kwargs):
        """确认恢复后的第二条证据会在同一回合触发一次 Planner。"""  # noqa: DOCSTRING_CJK
        planner_calls.append(kwargs)
        return {"ok": False, "reason": "patch_invalid"}

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch", _fake_plan
    )
    continued = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="对，还是继续确认备用测试片",
        client_turn_id="turn_continue_after_idle",
        base_revision=1,
    )

    resumed_session = await session_store.load_session(root, started["session_id"])
    resumed = resumed_session["story_state"]["dynamic_intent"]
    assert continued["ok"] is True
    assert resumed["intent_key"] == "intent_server_1"
    assert resumed["streak"] == 2
    assert resumed["thread_state"] == "active"
    assert len(planner_calls) == 1


@pytest.mark.asyncio
async def test_router_technical_degradation_preserves_intent_and_pending(
    monkeypatch, tmp_path
):
    """Router 基础设施故障不消耗意图休眠额度，也不吞掉下一轮仍可确认的 Pending。"""  # noqa: DOCSTRING_CJK
    dynamic_root = tmp_path / "dynamic_router_degraded"
    dynamic_started = await runtime.start_session(
        dynamic_root,
        lanlan_name="路由降级意图猫娘",
        story_id=THEATER_TEST_STORY_ID,
    )
    dynamic_session = await session_store.load_session(
        dynamic_root, dynamic_started["session_id"]
    )
    dynamic_node_id = str(dynamic_session["story_state"]["current_node_id"])
    expected_dynamic = {
        "intent_key": "intent_preserved_during_router_failure",
        "intent_summary": "继续核对眼前的测试步骤",
        "origin_node_id": dynamic_node_id,
        "streak": 1,
        "evidence_messages": ["先核对一下测试步骤"],
        "relation": "new",
    }
    dynamic_session["story_state"]["dynamic_intent"] = dict(expected_dynamic)
    await session_store.save_session(dynamic_root, dynamic_session)

    for revision in (0, 1):
        result = await runtime.submit_input(
            dynamic_root,
            session_id=dynamic_started["session_id"],
            input_kind="free_input",
            message="模型不可用时也不要消耗意图状态",
            client_turn_id=f"turn_router_degraded_{revision}",
            base_revision=revision,
            config_manager=None,
        )
        assert result["ok"] is True
    dynamic_saved = await session_store.load_session(
        dynamic_root, dynamic_started["session_id"]
    )
    assert dynamic_saved["story_state"]["dynamic_intent"] == expected_dynamic
    assert "route_delivery" not in json.dumps(dynamic_saved, ensure_ascii=False)

    pending_root = tmp_path / "pending_router_degraded"
    pending_started = await runtime.start_session(
        pending_root,
        lanlan_name="路由降级Pending猫娘",
        story_id=THEATER_TEST_STORY_ID,
    )
    story = await story_loader.load_story(THEATER_TEST_STORY_ID)
    pending_session = await session_store.load_session(
        pending_root, pending_started["session_id"]
    )
    pending_state = pending_session["story_state"]
    pending_node = story_graph.current_node(story, pending_state)
    pending_scene = story_loader.scene_for_phase(
        story,
        str(
            pending_node.get("belong_phase") or pending_session.get("phase") or "setup"
        ),
    )
    pending_state["pending_intent"] = branch_lifecycle.build_pending_intent(
        summary="继续核对眼前的测试步骤",
        evidence_excerpt="然后核对一下测试步骤",
        source_node_id="node_previous",
        target_node_id=str(pending_node["node_id"]),
        target_scene_id=str(pending_scene["id"]),
        created_revision=0,
    )
    await session_store.save_session(pending_root, pending_session)

    degraded = await runtime.submit_input(
        pending_root,
        session_id=pending_started["session_id"],
        input_kind="free_input",
        message="对，继续核对",
        client_turn_id="turn_pending_router_degraded",
        base_revision=0,
        config_manager=None,
    )
    after_degraded = await session_store.load_session(
        pending_root, pending_started["session_id"]
    )
    assert degraded["ok"] is True
    assert (
        after_degraded["story_state"]["pending_intent"]
        == pending_state["pending_intent"]
    )

    async def _confirmed_route(**kwargs):
        """下一轮真实 Router 仍能看到未被技术故障吞掉的 Pending。"""  # noqa: DOCSTRING_CJK
        assert (
            kwargs["state"]["pending_intent"]["summary"]
            == "继续核对眼前的测试步骤"
        )
        return {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": "继续核对眼前的测试步骤",
                "relation": "continue",
                "confidence": 0.95,
            },
            "residual_intent": {},
        }

    planner_calls: list[dict] = []

    async def _rejected_plan(**kwargs):
        """只记录确认轮已进入 Planner，避免为本测试重复构造完整 Patch。"""  # noqa: DOCSTRING_CJK
        planner_calls.append(kwargs)
        return {"ok": False, "reason": "patch_invalid"}

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _confirmed_route)
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch", _rejected_plan
    )
    confirmed = await runtime.submit_input(
        pending_root,
        session_id=pending_started["session_id"],
        input_kind="free_input",
        message="是的，还是继续核对测试步骤",
        client_turn_id="turn_pending_confirmed_after_degraded",
        base_revision=1,
        config_manager=None,
    )
    confirmed_saved = await session_store.load_session(
        pending_root, pending_started["session_id"]
    )
    assert confirmed["ok"] is True
    assert confirmed_saved["story_state"]["pending_intent"] == {}
    assert confirmed_saved["story_state"]["dynamic_intent"]["streak"] == 2
    assert len(planner_calls) == 1


@pytest.mark.asyncio
async def test_repeated_latent_intent_branches_only_after_two_goal_pullbacks(
    monkeypatch, tmp_path
):
    """普通岔题会清零计数；同一作者意图连续第三次出现时才进入隐藏支线。"""  # noqa: DOCSTRING_CJK
    story = deepcopy(await story_loader.load_story_exact(THEATER_TEST_STORY_ID))
    story["edges"].append(
        {
            "from_node": THEATER_TEST_START_NODE_ID,
            "to_node": THEATER_TEST_EXCHANGE_NODE_ID,
            "visibility": "latent",
            "transition_id": "transition_review_public_exchange",
            "goal_id": THEATER_TEST_GOAL_ID,
            "intent_id": "intent_review_public_exchange",
            "intent_summary": "直接复核公开交换步骤",
            "intent_examples": ["先复核公开交换步骤"],
            "pullbacks_before_transition": 2,
            "callback": "双方暂不按推荐动作推进，改为直接复核公开交换步骤。",
        }
    )

    async def _load_test_story(_story_id):
        """返回带一条中性隐藏边的测试 Story。"""  # noqa: DOCSTRING_CJK
        return deepcopy(story)

    monkeypatch.setattr(runtime.story_loader, "load_story", _load_test_story)
    monkeypatch.setattr(runtime.story_loader, "load_story_exact", _load_test_story)
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )

    async def _fake_route(**kwargs):
        """独立返回稳定 intent_id，演绎函数不再承担隐藏边分类。"""  # noqa: DOCSTRING_CJK
        observed = (
            "intent_review_public_exchange"
            if kwargs["user_message"].startswith("复核")
            else ""
        )
        return {"matched_choice_id": "", "observed_intent_id": observed}

    async def _fake_performance(**kwargs):
        """根据路由后的最终节点生成当前回合文案。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": kwargs["callback"]
            if kwargs["progress_kind"] == "graph_progress"
            else "",
            "dialogue": (
                "糖糖会和你直接复核公开交换步骤。"
                if kwargs["progress_kind"] == "graph_progress"
                else "糖糖先回应你当前提出的检查方式。"
            ),
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    messages = [
        "复核一：先直接检查公开交换步骤。",
        "先聊聊测试室的布置。",
        "复核二：我还是想直接检查公开交换步骤。",
        "复核三：再具体检查一次。",
        "复核四：先把这一步检查完。",
    ]
    results = []
    for revision, message in enumerate(messages):
        results.append(
            await runtime.submit_input(
                root,
                session_id=started["session_id"],
                input_kind="free_input",
                message=message,
                client_turn_id=f"turn_latent_{revision}",
                base_revision=revision,
            )
        )
        saved = await session_store.load_session(root, started["session_id"])
        if revision == 0:
            assert saved["story_state"]["intent_streak"] == 1
        elif revision == 1:
            assert saved["story_state"]["intent_streak"] == 0
        elif revision == 2:
            assert saved["story_state"]["intent_streak"] == 1
        elif revision == 3:
            assert saved["story_state"]["intent_streak"] == 2

    assert [item["scenario_trace"]["progress_kind"] for item in results] == [
        "roleplay_response",
        "roleplay_response",
        "roleplay_response",
        "roleplay_response",
        "graph_progress",
    ]
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == THEATER_TEST_EXCHANGE_NODE_ID
    assert (
        saved["story_state"]["branch_commitment"]
        == "transition_review_public_exchange"
    )
    assert saved["story_state"]["intent_streak"] == 0
    assert {item["choice_id"] for item in results[-1]["suggestion_options"]} == {
        "choice_finish_contract_story",
    }
    # 内部图谱身份和拉回计数不经过 Projector，前端只能看到作者推荐按钮。
    serialized = json.dumps(results[-1], ensure_ascii=False)
    assert "intent_review_public_exchange" not in serialized
    assert "transition_review_public_exchange" not in serialized


@pytest.mark.asyncio
async def test_natural_language_match_regenerates_performance_from_target_node(
    monkeypatch, tmp_path
):
    """自然语言命中后必须立刻演目标节点，不能把旧节点台词延迟显示一轮。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    first_choice = next(
        item
        for item in started["suggestion_options"]
        if item["choice_mode"] == "dialogue"
    )
    routed_calls = []

    async def _fake_performance(**kwargs):
        """记录演绎调用，验证旧节点不再生成一份会被丢弃的台词。"""  # noqa: DOCSTRING_CJK
        if kwargs["node"]["node_id"] == THEATER_TEST_EXCHANGE_NODE_ID:
            routed_calls.append(
                (
                    kwargs["node"]["node_id"],
                    kwargs["progress_kind"],
                    kwargs["choice_options"],
                )
            )
            return {
                "narration": kwargs["callback"],
                "dialogue": "公开交换已经完成，我们可以继续记录结果。",
                "choice_rewrites": [],
            }
        return {
            "narration": kwargs["callback"],
            "dialogue": "测试牌的编号已经确认，可以继续下一步。",
            "choice_rewrites": [],
        }

    async def _fake_route(**_kwargs):
        """把复合自然表达映射到当前稳定 Choice。"""  # noqa: DOCSTRING_CJK
        return {
            "matched_choice_id": "choice_complete_public_exchange",
            "observed_intent_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_confirm_test_plan",
        base_revision=0,
    )
    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="完成公开交换并继续记录",
        client_turn_id="turn_exchange_by_natural_language",
        base_revision=progressed["state_revision"],
    )

    assert [(node_id, kind) for node_id, kind, _options in routed_calls] == [
        (THEATER_TEST_EXCHANGE_NODE_ID, "graph_progress"),
    ]
    assert [item["choice_id"] for item in routed_calls[0][2]] == [
        "choice_finish_contract_story",
    ]
    story = await story_loader.load_story_exact(THEATER_TEST_STORY_ID)
    target = story_graph.node_by_id(story, THEATER_TEST_EXCHANGE_NODE_ID)
    assert result["dialogue"]["text"] == target["scripted_dialogue"]
    assert result["scenario_trace"]["progress_kind"] == "graph_progress"
    assert result["suggestion_options"][0]["choice_id"] == "choice_finish_contract_story"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "message",
    ["完成公开交换", "完成公开交换。", "完成，公开交换", "完成公开交换！"],
)
async def test_authored_completion_advances_before_old_node_generation(
    monkeypatch, tmp_path, message
):
    """作者声明的完成表达必须直接演目标节点，不能先生成一次旧邀请。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    first_choice = next(
        item
        for item in started["suggestion_options"]
        if item["choice_mode"] == "dialogue"
    )
    calls = []

    async def _fake_performance(**kwargs):
        """记录实际演绎节点，确保确定性路由没有先请求旧节点。"""  # noqa: DOCSTRING_CJK
        calls.append((kwargs["node"]["node_id"], kwargs["progress_kind"]))
        return {
            "narration": kwargs["callback"],
            "dialogue": "公开交换已经完成，可以写入记录板。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_confirm_plan_before_authored_completion",
        base_revision=0,
    )
    calls.clear()

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=message,
        client_turn_id=f"turn_authored_exchange_{message}",
        base_revision=progressed["state_revision"],
    )

    assert calls == [(THEATER_TEST_EXCHANGE_NODE_ID, "graph_progress")]
    assert result["scenario_trace"] == {
        "progress_kind": "graph_progress",
        "action_label": message,
    }
    assert result["suggestion_options"][0]["choice_id"] == "choice_finish_contract_story"
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == THEATER_TEST_EXCHANGE_NODE_ID
    assert message not in saved["story_state"]["scene_notes"]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["先别完成公开交换", "为什么完成公开交换"])
async def test_authored_completion_does_not_consume_negation_or_question(
    monkeypatch, tmp_path, message
):
    """含否定或疑问的长句不是作者完成表达，必须保留为当前节点互动。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="糖糖", story_id=THEATER_TEST_STORY_ID
    )
    first_choice = next(
        item
        for item in started["suggestion_options"]
        if item["choice_mode"] == "dialogue"
    )
    calls = []

    async def _fake_performance(**kwargs):
        """明确返回未命中，隔离确定性匹配与模型自由路由。"""  # noqa: DOCSTRING_CJK
        calls.append((kwargs["node"]["node_id"], kwargs["progress_kind"]))
        return {
            "narration": kwargs["callback"]
            if kwargs["progress_kind"] == "graph_progress"
            else "",
            "dialogue": "糖糖先回答你眼前的问题喵。",
            "choice_rewrites": [],
            "matched_choice_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_confirm_plan_before_negated_completion",
        base_revision=0,
    )
    calls.clear()

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=message,
        client_turn_id=f"turn_hold_exchange_{message}",
        base_revision=progressed["state_revision"],
    )

    assert calls == [(THEATER_TEST_ANCHOR_NODE_ID, "roleplay_response")]
    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    saved = await session_store.load_session(root, started["session_id"])
    assert saved["story_state"]["current_node_id"] == THEATER_TEST_ANCHOR_NODE_ID
    assert saved["story_state"]["scene_notes"][-1] == message


@pytest.mark.asyncio
async def test_free_input_without_valid_model_match_stays_on_current_node(tmp_path):
    """模型不可用时即使玩家复述按钮也不得由服务端猜测推进。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", story_id=THEATER_TEST_STORY_ID
    )
    before = await session_store.load_session(root, started["session_id"])

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=started["suggestion_options"][0]["label"],
        client_turn_id="turn_model_unavailable_hold",
        base_revision=0,
    )

    after = await session_store.load_session(root, started["session_id"])
    assert result["scenario_trace"]["progress_kind"] == "roleplay_response"
    assert (
        after["story_state"]["current_node_id"]
        == before["story_state"]["current_node_id"]
    )


@pytest.mark.asyncio
async def test_natural_language_and_click_commit_same_author_state(
    monkeypatch, tmp_path
):
    """自然语言只是 Choice 的第二入口，提交后的作者权威状态必须与点击完全一致。"""  # noqa: DOCSTRING_CJK
    click_root = tmp_path / "click" / "theater"
    natural_root = tmp_path / "natural" / "theater"
    clicked_start = await runtime.start_session(
        click_root,
        lanlan_name="测试猫娘",
        story_id=THEATER_TEST_STORY_ID,
    )
    natural_start = await runtime.start_session(
        natural_root,
        lanlan_name="测试猫娘",
        story_id=THEATER_TEST_STORY_ID,
    )
    selected = clicked_start["suggestion_options"][0]

    async def _fake_performance(**kwargs):
        """点击和自然语言都只演绎已经由服务端提交的目标节点。"""  # noqa: DOCSTRING_CJK
        return {
            "narration": kwargs.get("callback") or "作者回调会在自然语言命中后补入。",
            "dialogue": "这一步由你决定喵。",
            "choice_rewrites": [],
        }

    async def _fake_route(**kwargs):
        """自然语言入口选择与点击相同的稳定 Choice。"""  # noqa: DOCSTRING_CJK
        return {
            "matched_choice_id": kwargs["choice_options"][0]["choice_id"],
            "observed_intent_id": "",
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    monkeypatch.setattr("services.theater.llm.route_free_input_async", _fake_route)
    await runtime.submit_input(
        click_root,
        session_id=clicked_start["session_id"],
        input_kind="choice",
        choice_id=selected["choice_id"],
        client_turn_id="turn_click_choice",
        base_revision=0,
    )
    await runtime.submit_input(
        natural_root,
        session_id=natural_start["session_id"],
        input_kind="free_input",
        message=selected["label"],
        client_turn_id="turn_natural_choice",
        base_revision=0,
    )

    clicked = await session_store.load_session(click_root, clicked_start["session_id"])
    natural = await session_store.load_session(
        natural_root, natural_start["session_id"]
    )
    assert natural["story_state"] == clicked["story_state"]


@pytest.mark.asyncio
async def test_free_dialogue_cannot_rewrite_author_choice_label(monkeypatch, tmp_path):
    """模型即使返回 Choice 改写，玩家仍只能看到并点击作者原文。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    started = await runtime.start_session(
        root, lanlan_name="测试猫娘", story_id=THEATER_TEST_STORY_ID
    )

    async def _fake_performance(**kwargs):
        """用当前约会剧情复现“自由追问后按钮应承接”的更新链。"""  # noqa: DOCSTRING_CJK
        if kwargs["progress_kind"] == "roleplay_response":
            current = kwargs["choice_options"][0]
            return {
                "narration": "她把测试记录折到只剩当前步骤的一面。",
                "dialogue": "真正想说的话，我希望你留到看着我的时候再决定喵。",
                "matched_choice_id": "",
                "choice_rewrites": [
                    {
                        "choice_id": current["choice_id"],
                        # 上下文化完整保留作者表达，只在同一对白内追加“保留真心话”的当前语境。
                        "label": "“好，正好我也打算出门，这就出发吧。真正想说的话留到你面前。”",
                    }
                ],
            }
        return {
            "narration": "清单只留下路线，背面的答案被重新折起。",
            "dialogue": "好，那我等你亲口告诉我喵。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr("services.theater.llm.generate_turn_async", _fake_performance)
    first_choice = next(
        item
        for item in started["suggestion_options"]
        if item["choice_mode"] == "action"
    )
    recognized = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=first_choice["choice_id"],
        client_turn_id="turn_keep_handmade_charm",
        base_revision=0,
    )
    static_choice_id = recognized["suggestion_options"][0]["choice_id"]
    authored_options = recognized["suggestion_options"]

    roleplay = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="那你希望我今天哪件事不要提前计划？",
        client_turn_id="turn_ask_what_to_leave_open",
        base_revision=1,
    )
    assert roleplay["suggestion_options"] == authored_options

    progressed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=static_choice_id,
        client_turn_id="turn_leave_words_unplanned",
        base_revision=2,
    )
    assert progressed["suggestion_options"][0]["choice_id"] != static_choice_id
    saved = await session_store.load_session(root, started["session_id"])
    story = await story_loader.load_story_exact(THEATER_TEST_STORY_ID)
    authored_node = story_graph.node_by_id(
        story, saved["story_state"]["current_node_id"]
    )
    assert progressed["dialogue"]["text"] == authored_node["scripted_dialogue"]
    assert "choice_label_overrides" not in saved["story_state"]


@pytest.mark.asyncio
async def test_story_graph_ignores_legacy_choice_label_override():
    """旧 Session 即使残留模型覆盖，也必须投影当前 Story 的作者 Choice 原文。"""  # noqa: DOCSTRING_CJK
    story = await story_loader.load_story_exact(THEATER_TEST_STORY_ID)
    state = rules.initial_state(
        story, initial_node_id=story_loader.initial_node_id(story)
    )
    rules.apply_node(story, state, story_graph.current_node(story, state))
    authored = story_graph.suggestion_options(story, state, lanlan_name="霜瞳")
    state["choice_label_overrides"] = {authored[0]["choice_id"]: "模型试图替换作者按钮"}

    assert story_graph.suggestion_options(story, state, lanlan_name="霜瞳") == authored


@pytest.mark.asyncio
async def test_legacy_session_returns_upgrade_result_without_discarding_active_index(
    tmp_path,
):
    """旧 Session 不得误读，但恢复接口必须明确提示升级且保留原索引。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    session_id = "theater_00000000-0000-0000-0000-000000000001"
    path = session_store.session_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    # 模拟瘦身前存档：保留合法 ID，但故意没有 schema_version。
    path.write_text(
        json.dumps({"session_id": session_id, "lanlan_name": "旧猫娘"}),
        encoding="utf-8",
    )
    await session_store.save_active_sessions(root, {"旧猫娘": session_id})
    session_store.reset_active_sessions_for_tests()

    restored = await runtime.get_active_state(root, lanlan_name="旧猫娘")
    assert restored == {
        "ok": False,
        "reason": "session_upgrade_required",
        "session_id": session_id,
    }
    assert await session_store.load_active_sessions(root) == {"旧猫娘": session_id}
    assert await session_store.load_session(root, session_id) is None

    # 普通开场请求不能把旧恢复入口静默覆盖；只有玩家看到提示后的显式替换才允许新开场。
    blocked_start = await runtime.start_session(
        root,
        lanlan_name="旧猫娘",
        client_start_id="start_without_consent",
    )
    assert blocked_start == {
        "ok": False,
        "reason": "session_upgrade_required",
        "session_id": session_id,
    }
    assert await session_store.load_active_sessions(root) == {"旧猫娘": session_id}

    replacement = await runtime.start_session(
        root,
        lanlan_name="旧猫娘",
        client_start_id="start_after_consent",
        replace_incompatible_session=True,
    )
    assert replacement["ok"] is True
    assert replacement["session_id"] != session_id
    assert session_store.session_path(root, session_id).is_file()
    assert await session_store.load_active_sessions(root) == {
        "旧猫娘": replacement["session_id"]
    }

    # 角色切换再次遇到旧索引时也只清理索引，不尝试迁移未知私有状态。
    await session_store.save_active_sessions(root, {"旧猫娘": session_id})
    session_store.reset_active_sessions_for_tests()
    cleared = await runtime.clear_character_session(root, lanlan_name="旧猫娘")
    assert cleared == {"ok": True, "cleared": True, "session_id": session_id}
    assert await session_store.load_active_sessions(root) == {}


@pytest.mark.asyncio
async def test_framework_story_completes_through_structured_runtime(tmp_path):
    """中性测试 Story 通过真实 Runtime 连续推进后必须正式落幕。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "theater"
    result = await runtime.start_session(
        root, lanlan_name="测试猫娘", story_id=THEATER_TEST_STORY_ID
    )
    path = [
        "choice_confirm_test_token",
        "choice_complete_public_exchange",
        "choice_finish_contract_story",
    ]
    for revision, choice_id in enumerate(path):
        assert choice_id in {
            option["choice_id"] for option in result["suggestion_options"]
        }
        result = await runtime.submit_input(
            root,
            session_id=result["session_id"],
            input_kind="choice",
            choice_id=choice_id,
            client_turn_id=f"turn_framework_contract_{revision}",
            base_revision=revision,
        )
        assert result["ok"] is True
    assert result["state_revision"] == len(path)
    assert result["ending"]["ending_id"] == "ending_contract_complete"
    assert result["can_resume"] is False
    assert result["phase"] == "ending"
    completed_session = await session_store.load_session(root, result["session_id"])
    assert completed_session["end_reason"] == "story_complete"


async def _prepare_active_handoff_session(root, *, story_id: str, lanlan_name: str):
    """构造一条已有公开事实、仍可继续的活动支线，供转交事务测试复用。"""  # noqa: DOCSTRING_CJK
    started = await runtime.start_session(
        root, lanlan_name=lanlan_name, story_id=story_id
    )
    story = await story_loader.load_story_exact(story_id)
    content_slots = story["world_contract"]["dynamic_content_slots"]
    # 恢复链现在会按当前 Story 重验槽位；测试夹具也必须使用作者真实声明的槽位。
    content_slot = content_slots[0]
    session = await session_store.load_session(root, started["session_id"])
    state = session["story_state"]
    anchor_node_id = str(state["current_node_id"])
    branch_id = f"branch_private_handoff_{story_id}"
    active_branch = branch_lifecycle.build_active_runtime_branch(
        {
            "origin_node_id": anchor_node_id,
            "seed_intent": "完成当前双人维护步骤",
            "turn_budget": 4,
            "allowed_new_facts": [
                {
                    "fact_type": "observable_action",
                    "fact_role": "fixture_step_completed",
                    "content_slot_id": "",
                }
            ],
            "beat_outline": [
                {
                    "beat_id": "beat_continue_fixture",
                    "player_choice_label": "继续完成眼前的维护步骤",
                    "exit_preparation": ["fixture_step_completed"],
                }
            ],
            "exit_candidates": [],
        },
        branch_id=branch_id,
        created_revision=0,
        return_anchor={"node_id": anchor_node_id, "goal_id": ""},
        max_nonprogress_turns=2,
    )
    active_branch["turns_used"] = 1
    state["active_runtime_branch"] = active_branch
    state["dynamic_intent"] = {
        "intent_key": f"intent_private_handoff_{story_id}",
        "intent_summary": "完成当前双人维护步骤",
        "origin_node_id": anchor_node_id,
        "streak": 2,
        "evidence_messages": ["先完成当前维护", "继续这一步"],
        "relation": "continue",
        "thread_state": "active",
    }
    committed_fact = branch_contracts.build_committed_branch_fact(
        {
            "goal_id": "",
            "fact_type": content_slot["allowed_fact_type"],
            "fact_role": "fixture_tool_prepared",
            "subject": "pair",
            "predicate": "prepared",
            "object": "shared_maintenance_tool",
            "content_slot_id": content_slot["slot_id"],
            "public_entity": {
                "kind": "prop",
                "label": "已准备的维护工具",
                "status": "available",
            },
        },
        branch_id=branch_id,
        fact_id=f"branch_fact_private_handoff_{story_id}",
        source_revision=0,
        public_entity_id=f"branch_entity_handoff_{story_id}",
    )
    state["branch_facts"] = [committed_fact]
    await session_store.save_session(root, session)
    return started, active_branch, committed_fact, anchor_node_id


@pytest.mark.asyncio
async def test_active_branch_continue_uses_branch_actor_and_dynamic_choice_skips_classifier(
    monkeypatch,
    tmp_path,
):
    """支线问题不得提交事实；动态行动按钮则携带动作焦点直接进入 Actor。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "continue_branch"
    started, _original_active, _fact, _anchor = await _prepare_active_handoff_session(
        root,
        story_id=THEATER_TEST_STORY_ID,
        lanlan_name="续演测试猫娘",
    )
    classifier_calls: list[dict] = []
    actor_calls: list[dict] = []

    async def _continue_branch(**kwargs):
        """明确把自由输入判为当前支线续演。"""  # noqa: DOCSTRING_CJK
        classifier_calls.append(kwargs)
        return {
            "classification": "continue_branch",
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": 0.95,
            "response_focus": {
                "focus_type": "question",
                "evidence_excerpt": "为什么必须先校准",
                "requires_state_change": False,
            },
            "route_delivery": "accepted",
        }

    async def _branch_actor(**kwargs):
        """故意每次都返回事实候选，验证服务端只接受已实施动作对应的一次。"""  # noqa: DOCSTRING_CJK
        actor_calls.append(kwargs)
        return {
            "narration": "双方仍在处理眼前尚未完成的维护。",
            "dialogue": "我先解释清楚，再按你真正实施的动作继续喵。",
            "fact_candidates": [
                {
                    "goal_id": "",
                    "fact_type": "observable_action",
                    "fact_role": "fixture_step_completed",
                    "subject": "pair",
                    "predicate": "completed_step",
                    "object": "shared_maintenance_step",
                    "content_slot_id": "",
                }
            ],
        }

    async def _unexpected_plan(**_kwargs):
        """旧支线续演和动态 Choice 都不得重新规划 Patch。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("active branch continuation must not call Planner")

    monkeypatch.setattr(
        turn_service.llm, "classify_active_branch_handoff_async", _continue_branch
    )
    monkeypatch.setattr(turn_service.llm, "generate_branch_turn_async", _branch_actor)
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch",
        _unexpected_plan,
    )

    continued = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="这一步为什么必须先校准？",
        client_turn_id="turn_continue_active_branch",
        base_revision=0,
    )
    after_continue = await session_store.load_session(root, started["session_id"])
    assert continued["ok"] is True
    assert len(classifier_calls) == 1
    assert len(actor_calls) == 1
    assert actor_calls[0]["response_focus"] == {
        "focus_type": "question",
        "evidence_excerpt": "为什么必须先校准",
        "requires_state_change": False,
    }
    # 即使 Actor 错给出合法形状的候选，提问焦点也不能变成已经发生的权威事实。
    assert len(after_continue["story_state"]["branch_facts"]) == 1
    question_causality = after_continue["turn_causality_records"][-1]
    assert question_causality["response_focus"]["focus_type"] == "question"
    assert question_causality["commit_summary"]["branch_facts_added"] == []
    assert after_continue["story_state"]["active_runtime_branch"]["turns_used"] == 2
    assert (
        after_continue["story_state"]["active_runtime_branch"]["nonprogress_turns"] == 1
    )
    assert after_continue["story_state"]["branch_history"] == []
    assert after_continue["story_state"]["pending_intent"] == {}

    dynamic_choice = continued["suggestion_options"][0]
    clicked = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="choice",
        choice_id=dynamic_choice["choice_id"],
        client_turn_id="turn_click_active_branch_choice",
        base_revision=1,
    )
    assert clicked["ok"] is True
    assert len(classifier_calls) == 1
    assert len(actor_calls) == 2
    assert actor_calls[1]["response_focus"] == {
        "focus_type": "action",
        "evidence_excerpt": dynamic_choice["label"],
        "requires_state_change": True,
    }
    after_click = await session_store.load_session(root, started["session_id"])
    assert len(after_click["story_state"]["branch_facts"]) == 2
    action_causality = after_click["turn_causality_records"][-1]
    assert action_causality["response_focus"] == {
        "focus_type": "action",
        "evidence_excerpt": dynamic_choice["label"],
        "requires_state_change": True,
    }
    added_branch_facts = action_causality["commit_summary"]["branch_facts_added"]
    assert len(added_branch_facts) == 1
    assert added_branch_facts[0]["fact_role"] == "fixture_step_completed"
    assert added_branch_facts[0]["source_revision"] == 2
    assert (
        after_click["story_state"]["active_runtime_branch"]["nonprogress_turns"]
        == 0
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "classification_result",
    [
        {
            "classification": "uncertain",
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": 0.44,
            "route_delivery": "accepted",
        },
        {
            "classification": "continue_branch",
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": 0.01,
            "route_delivery": "accepted",
        },
        {
            "classification": "uncertain",
            "intent_summary": "",
            "exit_evidence_excerpt": "",
            "next_evidence_excerpt": "",
            "confidence": 0.0,
            "route_delivery": "technical_degraded",
        },
        {
            "classification": "intent_handoff",
            "intent_summary": "检查备用电源",
            "exit_evidence_excerpt": "玩家没有说过的退出动作",
            "next_evidence_excerpt": "改去检查备用电源",
            "confidence": 0.98,
            "route_delivery": "accepted",
        },
        {
            "classification": "intent_handoff",
            "intent_summary": "检查备用电源",
            "exit_evidence_excerpt": "先停下当前维护",
            "next_evidence_excerpt": "玩家没有说过的新行动",
            "confidence": 0.98,
            "route_delivery": "accepted",
        },
    ],
)
async def test_unconfirmed_or_degraded_handoff_preserves_active_branch_without_budget_cost(
    monkeypatch,
    tmp_path,
    classification_result,
):
    """含糊、技术故障或任一伪造摘录都只能无事实降级，不能关闭或消耗旧支线。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / "unconfirmed_handoff"
    (
        started,
        original_active,
        committed_fact,
        _anchor,
    ) = await _prepare_active_handoff_session(
        root,
        story_id=THEATER_TEST_STORY_ID,
        lanlan_name="降级转交猫娘",
    )

    async def _classify(**_kwargs):
        """返回当前参数指定的含糊、故障或坏摘录结果。"""  # noqa: DOCSTRING_CJK
        return dict(classification_result)

    async def _unexpected_branch_actor(**_kwargs):
        """未能确认输入归属时不能让旧支线 Actor 提交候选事实。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("unconfirmed handoff must not call Branch Actor")

    async def _unexpected_plan(**_kwargs):
        """降级路径既不能续建旧 Patch，也不能规划新 Patch。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("unconfirmed handoff must not call Planner")

    monkeypatch.setattr(
        turn_service.llm, "classify_active_branch_handoff_async", _classify
    )
    monkeypatch.setattr(
        turn_service.llm, "generate_branch_turn_async", _unexpected_branch_actor
    )
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch",
        _unexpected_plan,
    )

    result = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message="先停下当前维护，改去检查备用电源",
        client_turn_id="turn_unconfirmed_handoff",
        base_revision=0,
    )
    saved = await session_store.load_session(root, started["session_id"])
    assert result["ok"] is True
    assert saved["state_revision"] == 1
    assert saved["story_state"]["active_runtime_branch"] == original_active
    assert saved["story_state"]["branch_facts"] == [committed_fact]
    assert saved["story_state"]["branch_history"] == []
    assert saved["story_state"]["pending_intent"] == {}
    assert saved["story_state"]["dynamic_intent"]["streak"] == 2
    serialized = json.dumps({"response": result, "session": saved}, ensure_ascii=False)
    assert "route_delivery" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "story_id",
        "handoff_message",
        "exit_excerpt",
        "next_excerpt",
        "intent_summary",
        "confirmation_message",
    ),
    [
        (
            THEATER_TEST_STORY_ID,
            "先停下整理旧日志，改去检查窗边的供暖阀",
            "先停下整理旧日志",
            "改去检查窗边的供暖阀",
            "检查窗边的供暖阀",
            "对，继续检查供暖阀",
        ),
        (
            THEATER_TEST_STORY_ID,
            "先停下校准信标，改去检查备用氧气",
            "先停下校准信标",
            "改去检查备用氧气",
            "检查备用氧气",
            "对，继续检查备用氧气",
        ),
    ],
)
async def test_active_branch_handoff_is_atomic_idempotent_recoverable_and_plans_next_turn(
    monkeypatch,
    tmp_path,
    story_id,
    handoff_message,
    exit_excerpt,
    next_excerpt,
    intent_summary,
    confirmation_message,
):
    """跨题材 handoff 原子保留旧事实，只建 Pending，并在恢复后的确认轮才进入 Planner。"""  # noqa: DOCSTRING_CJK
    root = tmp_path / story_id
    lanlan_name = f"转交测试猫娘-{story_id}"
    (
        started,
        original_active,
        committed_fact,
        anchor_node_id,
    ) = await _prepare_active_handoff_session(
        root,
        story_id=story_id,
        lanlan_name=lanlan_name,
    )
    classifier_calls: list[dict] = []
    ordinary_actor_calls: list[dict] = []

    async def _handoff_classifier(**kwargs):
        """返回两段均可由服务端在本轮原话中逐字核验的转交语义。"""  # noqa: DOCSTRING_CJK
        classifier_calls.append(kwargs)
        return {
            "classification": "intent_handoff",
            "intent_summary": intent_summary,
            "exit_evidence_excerpt": exit_excerpt,
            "next_evidence_excerpt": next_excerpt,
            "confidence": 0.97,
            "route_delivery": "accepted",
        }

    async def _unexpected_branch_actor(**_kwargs):
        """严格转交回合不能再让旧 Branch Actor 解释新行动。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("confirmed handoff must not call old Branch Actor")

    async def _unexpected_route(**_kwargs):
        """活动支线 handoff 回合只使用专用分类器，不进入普通 Router。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("handoff turn must not call ordinary Router")

    async def _unexpected_plan(**_kwargs):
        """转交回合只有第一条新证据，不能当轮创建第二条 Patch。"""  # noqa: DOCSTRING_CJK
        raise AssertionError("handoff turn must not call Planner")

    async def _ordinary_actor(**kwargs):
        """下一确认轮的普通 Actor 只能读取 History 脱敏召回。"""  # noqa: DOCSTRING_CJK
        ordinary_actor_calls.append(kwargs)
        recall_text = json.dumps(kwargs["completed_branch_recall"], ensure_ascii=False)
        assert "prepared" in recall_text
        assert committed_fact["fact_id"] not in recall_text
        assert committed_fact["branch_id"] not in recall_text
        return {
            "narration": "双方停下旧步骤，重新确认接下来要处理的事情。",
            "dialogue": "好，我们先把新想法说清楚再行动喵。",
            "choice_rewrites": [],
        }

    monkeypatch.setattr(
        turn_service.llm, "classify_active_branch_handoff_async", _handoff_classifier
    )
    monkeypatch.setattr(
        turn_service.llm, "generate_branch_turn_async", _unexpected_branch_actor
    )
    monkeypatch.setattr(turn_service.llm, "route_free_input_async", _unexpected_route)
    monkeypatch.setattr(turn_service.llm, "generate_turn_async", _ordinary_actor)
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch",
        _unexpected_plan,
    )

    handed_off = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=handoff_message,
        client_turn_id="turn_confirmed_handoff",
        base_revision=0,
    )
    saved = await session_store.load_session(root, started["session_id"])
    assert handed_off["ok"] is True
    assert saved["state_revision"] == 1
    assert len(classifier_calls) == 1
    assert ordinary_actor_calls == []
    assert "还没有开始" in handed_off["dialogue"]["text"]
    assert "确认清楚再行动" in handed_off["dialogue"]["text"]
    assert saved["story_state"]["active_runtime_branch"] == {}
    assert saved["story_state"]["current_node_id"] == anchor_node_id
    assert saved["story_state"]["branch_facts"] == [committed_fact]
    assert saved["story_state"]["completed_goal_ids"] == []
    assert saved["story_state"]["dynamic_intent"] == {}
    history = saved["story_state"]["branch_history"]
    assert history == [
        {
            "branch_id": original_active["branch_id"],
            "completed_goal_ids": [],
            "key_fact_ids": [committed_fact["fact_id"]],
            "exit_kind": "intent_handoff",
            "ended_revision": 1,
            "recap": "",
        }
    ]
    pending = saved["story_state"]["pending_intent"]
    assert pending["summary"] == intent_summary
    assert pending["evidence_excerpt"] == next_excerpt
    assert pending["source_node_id"] == anchor_node_id
    assert pending["target_node_id"] == anchor_node_id
    assert pending["target_scene_id"] == handed_off["scene"]["scene_id"]
    assert pending["created_revision"] == 1
    assert pending["expires_revision"] == 2
    assert not any(
        option["choice_id"].startswith("branch_choice_")
        for option in handed_off["suggestion_options"]
    )
    assert any(
        item["label"] == "已准备的维护工具"
        for item in handed_off["scenario_board"]["available_props"]
    )
    public_text = json.dumps(handed_off, ensure_ascii=False)
    for private_field in (
        "branch_id",
        "fact_id",
        "pending_intent",
        "intent_handoff",
        "return_anchor",
    ):
        assert private_field not in public_text

    replayed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=handoff_message,
        client_turn_id="turn_confirmed_handoff",
        base_revision=0,
    )
    after_replay = await session_store.load_session(root, started["session_id"])
    assert replayed == handed_off
    assert after_replay == saved
    assert len(classifier_calls) == 1
    assert ordinary_actor_calls == []

    session_store.reset_active_sessions_for_tests()
    restored = await runtime.get_active_state(root, lanlan_name=lanlan_name)
    after_restore = await session_store.load_session(root, started["session_id"])
    assert restored["ok"] is True
    assert restored["state_revision"] == 1
    assert restored["suggestion_options"] == handed_off["suggestion_options"]
    assert after_restore == saved
    assert ordinary_actor_calls == []

    async def _confirmed_pending_route(**kwargs):
        """恢复后的下一轮明确承接 Pending，才把两条玩家证据交给既有意图线程。"""  # noqa: DOCSTRING_CJK
        assert kwargs["state"]["pending_intent"]["evidence_excerpt"] == next_excerpt
        return {
            "route_kind": "free_intent",
            "matched_choice_id": "",
            "authored_intent_id": "",
            "free_intent": {
                "summary": intent_summary,
                "relation": "continue",
                "confidence": 0.96,
            },
            "residual_intent": {},
        }

    planner_calls: list[dict] = []

    async def _rejected_new_plan(**kwargs):
        """记录确认轮已经达到规划阈值，同时避免为状态测试构造第二份完整 Patch。"""  # noqa: DOCSTRING_CJK
        planner_calls.append(kwargs)
        assert kwargs["dynamic_intent"]["streak"] == 2
        return {"ok": False, "reason": "patch_invalid"}

    monkeypatch.setattr(
        turn_service.llm, "route_free_input_async", _confirmed_pending_route
    )
    monkeypatch.setattr(
        "services.theater.branch_planner.plan_validated_runtime_branch",
        _rejected_new_plan,
    )
    confirmed = await runtime.submit_input(
        root,
        session_id=started["session_id"],
        input_kind="free_input",
        message=confirmation_message,
        client_turn_id="turn_confirm_handoff_pending",
        base_revision=1,
    )
    after_confirmation = await session_store.load_session(root, started["session_id"])
    assert confirmed["ok"] is True
    assert len(planner_calls) == 1
    assert len(classifier_calls) == 1
    assert len(ordinary_actor_calls) == 1
    assert after_confirmation["story_state"]["pending_intent"] == {}
    dynamic_intent = after_confirmation["story_state"]["dynamic_intent"]
    assert dynamic_intent["streak"] == 2
    assert dynamic_intent["evidence_messages"] == [next_excerpt, confirmation_message]
    assert dynamic_intent["origin_node_id"] == anchor_node_id
