"""验证 Numeric v2 内部压测执行器的输入策略、报告与失败原子性。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from scripts import run_numeric_v2_stress


def test_numeric_v2_stress_parser_accepts_fixed_baseline_selection():
    # 固定入口必须能被命令行解析，避免阶段 A 依赖手工复制 story_id。
    parser = run_numeric_v2_stress._build_parser()

    args = parser.parse_args(["--baseline"])

    assert args.baseline is True
    assert args.all is False
    assert args.story_id is None


def test_numeric_v2_stress_parser_accepts_isolated_package_root(tmp_path):
    # 新生成包的压测必须能脱离正式安装目录，避免测试过程覆盖用户剧本。
    parser = run_numeric_v2_stress._build_parser()

    args = parser.parse_args(["--story-id", "story_test", "--package-root", str(tmp_path)])

    assert args.package_root == tmp_path


def test_numeric_v2_stress_parser_accepts_chat_only_strategy():
    parser = run_numeric_v2_stress._build_parser()

    args = parser.parse_args(["--story-id", "story_test", "--strategy", "chat"])

    assert args.strategy == "chat"


def test_numeric_v2_stress_baseline_selection_is_stable_and_reports_title_drift():
    # 标题变化只记录为报告诊断，不改变固定 story_id 的执行顺序。
    installed = {
        story_id: {"title": run_numeric_v2_stress.BASELINE_EXPECTED_TITLES[story_id]}
        for story_id in run_numeric_v2_stress.BASELINE_STORY_IDS
    }
    drifted_story_id = run_numeric_v2_stress.BASELINE_STORY_IDS[-1]
    installed[drifted_story_id] = {"title": "改稿后的标题"}
    args = run_numeric_v2_stress._build_parser().parse_args(["--baseline"])

    story_ids, selection = run_numeric_v2_stress._resolve_story_selection(args, installed)

    assert story_ids == list(run_numeric_v2_stress.BASELINE_STORY_IDS)
    assert selection["mode"] == "baseline"
    assert selection["manifest"] == run_numeric_v2_stress.BASELINE_MANIFEST
    assert selection["title_mismatches"] == [{
        "story_id": drifted_story_id,
        "expected_title": run_numeric_v2_stress.BASELINE_EXPECTED_TITLES[drifted_story_id],
        "actual_title": "改稿后的标题",
    }]


def test_numeric_v2_stress_baseline_selection_reports_missing_focus_package():
    # 缺少固定样本时必须明确失败，不能静默压测不完整的基线。
    installed = {
        story_id: {"title": run_numeric_v2_stress.BASELINE_EXPECTED_TITLES[story_id]}
        for story_id in run_numeric_v2_stress.BASELINE_STORY_IDS[:-1]
    }
    args = run_numeric_v2_stress._build_parser().parse_args(["--baseline"])

    with pytest.raises(ValueError, match="numeric_baseline_story_not_found:"):
        run_numeric_v2_stress._resolve_story_selection(args, installed)


def test_numeric_v2_stress_mixed_strategy_uses_recommendation_periodically():
    suggestions = ["“沿着主线继续。”", "先检查眼前线索。"]

    assert run_numeric_v2_stress.choose_player_input(
        strategy="recommended",
        attempt_index=0,
        suggestions=suggestions,
    ) == (suggestions[0], "recommended")
    assert run_numeric_v2_stress.choose_player_input(
        strategy="mixed",
        attempt_index=1,
        suggestions=suggestions,
    )[1] == "freeform"
    assert run_numeric_v2_stress.choose_player_input(
        strategy="mixed",
        attempt_index=3,
        suggestions=suggestions,
    )[1] == "recommended"
    assert run_numeric_v2_stress.choose_player_input(
        strategy="mixed",
        attempt_index=4,
        suggestions=suggestions,
    )[1] == "freeform"


def test_numeric_v2_stress_uses_visible_order_without_internal_metadata():
    # 推荐不再携带 advance/explore 标签，压测器必须按前端可见顺序消费。
    candidates = ["先看看窗外。", "我愿意把共同约定确认下来。", "这件事先到这里。"]

    assert run_numeric_v2_stress.choose_player_input(
        strategy="recommended",
        attempt_index=0,
        suggestions=candidates,
    ) == ("先看看窗外。", "recommended")


def test_numeric_v2_stress_uses_first_slot_when_no_advance_metadata():
    candidates = ["我先帮你腾出安全的观察空间。", "我们暂时留在这里。"]

    assert run_numeric_v2_stress.choose_player_input(
        strategy="recommended",
        attempt_index=7,
        suggestions=candidates,
    ) == ("我先帮你腾出安全的观察空间。", "recommended")


def test_numeric_v2_stress_freeform_uses_latest_visible_context():
    """Generate free input from the latest public performance instead of cycling fixed phrases."""

    player_input, source = run_numeric_v2_stress.choose_player_input(
        strategy="freeform",
        attempt_index=1,
        suggestions=[],
        last_performance={
            "performance": "（望向门外）医疗站的蓝色信号还在闪烁，你要现在出发吗？",
        },
        node_title="废墟苏醒",
    )

    assert source == "freeform"
    assert "第一个办法" in player_input
    assert player_input not in run_numeric_v2_stress.FREEFORM_INPUTS
    assert "冒险尝试" not in player_input


def test_numeric_v2_stress_freeform_does_not_quote_catgirl_action_or_echo_question():
    # 自由输入应回应可见事实，不应把猫娘动作或她抛回玩家的问题整段复制成玩家话术。
    player_input, source = run_numeric_v2_stress.choose_player_input(
        strategy="freeform",
        attempt_index=1,
        suggestions=[],
        last_performance={
            "performance": "（猫耳压低，目光望向门外）医疗站的蓝色信号还在闪烁，你要现在出发吗？",
        },
    )

    assert source == "freeform"
    assert "猫耳压低" not in player_input
    assert "你要现在出发吗" not in player_input
    assert "第一个办法" in player_input
    assert "不急着下结论" not in player_input
    assert "你要现在出发吗" not in player_input


def test_numeric_v2_stress_freeform_injects_low_frequency_off_topic_input():
    # 长程轨迹必须偶尔覆盖跑偏恢复，但跑偏不能改变推荐与自由输入的总体比例。
    player_input, source = run_numeric_v2_stress.choose_player_input(
        strategy="freeform",
        attempt_index=5,
        suggestions=[],
        last_performance={"performance": "信号灯在雾里亮了一下。"},
    )

    assert source == "freeform"
    assert player_input == run_numeric_v2_stress.CONTEXTUAL_OFF_TOPIC_INPUTS[0]

    rotated = [
        run_numeric_v2_stress.choose_player_input(
            strategy="freeform",
            attempt_index=index,
            suggestions=[],
            last_performance={"performance": "信号灯在雾里亮了一下。"},
        )[0]
        for index in (5, 22, 39)
    ]
    assert len(set(rotated)) == 3


def test_numeric_v2_stress_freeform_ignores_generic_closing_anchor():
    # 不能把“晚安/好啦”当作剧情事实继续追问，否则压测会人为制造休息循环。
    player_input, source = run_numeric_v2_stress.choose_player_input(
        strategy="freeform",
        attempt_index=1,
        suggestions=[],
        last_performance={"performance": "（闭上眼睛）晚安……"},
        node_title="雨夜工作室",
    )

    assert source == "freeform"
    assert "晚安" not in player_input
    assert "雨夜工作室" in player_input


def test_numeric_v2_stress_freeform_avoids_repeated_test_meta_language():
    """上下文自由输入不能循环使用记录、复核和测试式追问。"""  # noqa: DOCSTRING_CJK

    generated = [
        run_numeric_v2_stress.choose_player_input(
            strategy="freeform",
            attempt_index=index,
            suggestions=[],
            last_performance={"performance": "（看向门外）雨已经停了，街角亮起一盏灯。"},
            node_title="雨夜工作室",
        )[0]
        for index in range(10)
        if index != 5
    ]

    assert all("我记下" not in item for item in generated)
    assert all("关键细节" not in item for item in generated)
    assert len(set(generated)) == len(generated)


def test_numeric_v2_stress_dynamic_player_only_receives_visible_history():
    """动态玩家不能借压测器偷看隐藏节点、数值或作者方向。"""  # noqa: DOCSTRING_CJK

    messages = run_numeric_v2_stress._dynamic_player_messages(
        latest_performance={
            "performance": "（望向门外）医疗站的蓝色信号还在闪烁。",
            "suggested_inputs": ["隐藏推荐不应进入自由输入模型。"],
            "internal_metric": 42,
        },
        recent_turns=[{
            "player_input": "我先看看门口。",
            "performance": {"performance": "（点头）门框还算稳固。"},
            "to_node_id": "hidden_node",
            "metrics": {"trust": 50},
        }],
        off_topic_turn=False,
    )

    payload = json.loads(messages[1].content)
    encoded = messages[1].content
    assert payload["latest_visible_performance"].endswith("蓝色信号还在闪烁。")
    assert payload["recent_visible_turns"] == [{
        "player_input": "我先看看门口。",
        "actor_reply": "点头\n门框还算稳固。",
    }]
    assert "hidden_node" not in encoded
    assert "trust" not in encoded
    assert "隐藏推荐" not in encoded
    assert "不要连续主动发起未经铺垫的亲密互动" in messages[0].content
    # 完整历史取代三轮窗口，客观事实的授权来源仍然只限于玩家可见演绎。
    assert "所有事实、能力、行动与结果都必须由已提交的可见演绎支持" in messages[0].content
    assert "玩家没有可假定的库存、工具、特殊能力或专业知识" in messages[0].content
    assert "不能替环境、角色或 NPC 决定结果" in messages[0].content
    assert "未知结果只能请求观察并等待演绎交付" in messages[0].content
    assert "自然把这项行动完整做完" in messages[0].content
    assert "不要再重复不改变结果的等价子步骤" in messages[0].content


def test_numeric_v2_stress_chat_strategy_stays_in_visible_scene():
    """Pending offers must not make a chat-only player click suggestions or advance the story."""

    player_input, source = run_numeric_v2_stress.choose_player_input(
        strategy="chat",
        attempt_index=2,
        suggestions=["（点头）我们现在出发。"],
        route_status="transition_offered",
        last_performance={"performance": "（看向窗外）雨还没有停。"},
    )
    messages = run_numeric_v2_stress._dynamic_player_messages(
        latest_performance={"performance": "（看向窗外）雨还没有停。"},
        recent_turns=[],
        off_topic_turn=False,
        chat_only=True,
    )

    assert source == "freeform"
    assert player_input != "（点头）我们现在出发。"
    assert "只进行当前场景内的自然闲聊" in messages[0].content
    assert "不写括号动作" in messages[0].content
    assert "不得执行推荐动作或接受转场" in messages[0].content
    assert "假设和玩笑不能写成已经确认的事实" in messages[0].content

    rewrite_messages = run_numeric_v2_stress._chat_player_rewrite_messages(
        latest_performance={"performance": "（看向窗外）雨还没有停。"},
        candidate="那我们现在就穿过雨幕出发吧。",
        transition_pending=True,
    )
    rewrite_payload = json.loads(rewrite_messages[1].content)

    assert rewrite_payload["candidate"] == "那我们现在就穿过雨幕出发吧。"
    assert rewrite_payload["transition_pending"] is True
    assert "只保留口头闲聊" in rewrite_messages[0].content
    assert "只能询问猫娘不改变客观事实的主观回应" in rewrite_messages[0].content
    assert "我还没决定要不要继续" in rewrite_messages[0].content
    assert "仍要直接回应 latest_visible_performance" in rewrite_messages[0].content


def test_numeric_v2_stress_grounded_rewrite_only_uses_visible_facts():
    """Ground ordinary free input a second time so the dynamic player cannot invent inventory or external results."""

    messages = run_numeric_v2_stress._grounded_player_rewrite_messages(
        latest_performance={"performance": "（看向熄灭的终端）来源还不知道。"},
        recent_turns=[{
            "player_input": "我先看看周围。",
            "performance": {"performance": "（点头）这里只能确认终端已经熄灭。"},
        }],
        candidate="（拿出未出现的工具）我已经查到来源了。",
    )
    payload = json.loads(messages[1].content)

    assert payload["candidate"] == "（拿出未出现的工具）我已经查到来源了。"
    assert "所有客观事实必须能从 recent_visible_turns" in messages[0].content
    assert "未明确出现的库存、工具、能力、专业知识、名称、编号" in messages[0].content
    assert "改成自然提问或保守尝试" in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("stage,chat_only,expected_calls", [
    ("_dynamic_player_messages", False, 0),
    ("_grounded_player_rewrite_messages", False, 1),
    ("_chat_player_rewrite_messages", True, 1),
])
async def test_dynamic_player_budget_failure_counts_only_started_calls(monkeypatch, stage, chat_only, expected_calls):
    """All three packing paths can exceed budget; failed local preparation must not count as a provider call."""

    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def ainvoke(self, messages):
            calls.append(messages)
            return SimpleNamespace(content='{"player_input":"我继续听。"}')

    async def create_client(*_args, **_kwargs):
        return Client()

    def over_budget(**_kwargs):
        raise ValueError("dynamic_player_context_over_budget")

    monkeypatch.setattr(run_numeric_v2_stress, "create_chat_llm_async", create_client)
    monkeypatch.setattr(run_numeric_v2_stress, stage, over_budget)
    player = run_numeric_v2_stress._DynamicPlayerGenerator(SimpleNamespace(
        get_model_api_config=lambda _name: {"model": "test", "base_url": "https://example.invalid"}))
    with pytest.raises(ValueError, match="dynamic_player_context_over_budget"):
        await player.generate(latest_performance={"performance": "继续说吧。"}, recent_turns=[],
            off_topic_turn=False, chat_only=chat_only)
    assert player.provider_call_count == len(calls) == expected_calls


def test_numeric_v2_stress_dynamic_player_parses_one_strict_input():
    assert run_numeric_v2_stress._parse_dynamic_player_input(
        '{"player_input":"（我指向闪烁的信号）我先进去救人，你留在门口接应。"}'
    ) == "（我指向闪烁的信号）我先进去救人，你留在门口接应。"

    with pytest.raises(ValueError, match="dynamic_player_fields_invalid"):
        run_numeric_v2_stress._parse_dynamic_player_input(
            '{"player_input":"继续。","hidden_goal":"去下一幕"}'
        )


def test_numeric_v2_stress_player_and_reviewer_keep_early_visible_facts():
    """Keep handoffs older than three turns available to player generation and grounding rather than rewriting them as forgetful questions."""

    turns = [{
        "player_input": "我把借来的针放回木盒，归还给你。",
        "performance": {"performance": "针已归还，放在木盒里。"},
        "metrics": {"hidden_trust": 50},
    }] + [{
        "player_input": f"我询问第 {index} 项安排。",
        "performance": {"performance": f"我们讨论第 {index} 项安排。"},
    } for index in range(6)]
    latest = turns[-1]["performance"]
    generated = run_numeric_v2_stress._dynamic_player_messages(
        latest_performance=latest, recent_turns=turns, off_topic_turn=False,
    )
    reviewed = run_numeric_v2_stress._grounded_player_rewrite_messages(
        latest_performance=latest, recent_turns=turns, candidate="针还在木盒里吧？",
    )

    for messages in (generated, reviewed):
        visible = json.loads(messages[1].content)["recent_visible_turns"]
        assert len(visible) == len(turns)
        assert visible[0]["player_input"] == turns[0]["player_input"]
        assert visible[0]["actor_reply"] == "针已归还，放在木盒里。"
        assert "hidden_trust" not in messages[1].content
    assert json.loads(generated[1].content)["recent_visible_turns"] == json.loads(
        reviewed[1].content
    )["recent_visible_turns"]


def test_numeric_v2_stress_player_over_budget_stops_without_truncation(monkeypatch):
    """Report excess context as a simulator failure instead of silently dropping earlier facts."""

    monkeypatch.setattr(run_numeric_v2_stress, "DYNAMIC_PLAYER_MAX_INPUT_TOKENS", 1, raising=False)
    with pytest.raises(ValueError, match="dynamic_player_context_over_budget"):
        run_numeric_v2_stress._dynamic_player_messages(
            latest_performance={"performance": "借来的针已经归还。"},
            recent_turns=[], off_topic_turn=False,
        )


@pytest.mark.asyncio
async def test_numeric_v2_stress_fork_player_reads_committed_history_and_stops_on_failure(monkeypatch):
    """Resume from actual Session history; player-generation failure must not fall back to suggestions or fixed phrases."""

    session = SimpleNamespace(
        session_id="stress_fork", revision=8, status="active", current_node_id="start",
        catgirl_binding={"character_id": "character_test"},
        opening_performance={"performance": "针最初借给了玩家。"},
        performance_history=({
            "input_text": "我把针归还到木盒里。",
            "performance": "针已收回木盒。",
            "suggested_inputs": ["未选推荐不构成事实。"],
            "to_node_id": "hidden_node",
        },),
    )
    stored = SimpleNamespace(session=session, ledger_events=())
    received = []

    class FakePlayer:
        provider_call_count = 1

        def __init__(self, _config):
            pass

        async def generate(self, **kwargs):
            received.extend(kwargs["recent_turns"])
            raise ValueError("dynamic_player_failed")

    async def forbidden_turn(**_kwargs):
        pytest.fail("模拟玩家失败后不应调用正式回合工作流")

    monkeypatch.setattr(run_numeric_v2_stress, "_DynamicPlayerGenerator", FakePlayer)
    monkeypatch.setattr(run_numeric_v2_stress, "execute_numeric_v2_turn", forbidden_turn)
    _, trace = await run_numeric_v2_stress._run_trace(
        runtime=SimpleNamespace(), config_manager=SimpleNamespace(), current=stored,
        attempts=2, strategy="freeform", trace_name="fork",
        packing_handler=run_numeric_v2_stress._PackingLogHandler(),
        max_errors=1, dynamic_player_enabled=True,
    )

    assert received[0]["performance"] == session.opening_performance
    assert received[1]["player_input"] == "我把针归还到木盒里。"
    assert trace["committed_turns"] == 0
    assert trace["errors"] == []
    assert len(trace["player_input_generation_errors"]) == 1
    assert trace["stop_reason"] == "player_input_generation_failed"


def test_numeric_v2_stress_keeps_visible_order_in_transition_offer_state():
    assert run_numeric_v2_stress.choose_player_input(
        strategy="recommended",
        attempt_index=8,
        suggestions=["先留在这里。", "好，我们去医疗站。"],
        route_status="transition_offered",
    ) == ("先留在这里。", "recommended")


def test_numeric_v2_stress_accepts_visible_offer_before_soft_pacing_window():
    """Actor 已公开提议后必须下一轮测试接受，不能再被推荐回合门槛挡住。"""  # noqa: DOCSTRING_CJK

    assert run_numeric_v2_stress.choose_player_input(
        strategy="freeform",
        attempt_index=2,
        suggestions=["（我点头）好，就按这个安排。", "（我摇头）先等等。"],
        route_status="transition_offered",
    ) == ("（我点头）好，就按这个安排。", "recommended")


def test_numeric_v2_stress_uses_advance_only_after_transition_is_offered():
    assert run_numeric_v2_stress.choose_player_input(
        strategy="mixed",
        attempt_index=8,
        suggestions=["先留在这里。", "好，我们去医疗站。"],
        route_status="transition_offered",
    ) == ("先留在这里。", "recommended")


def test_numeric_v2_stress_pending_transition_uses_first_visible_option():
    """待确认转场固定点击第一条可执行推荐，避免压测器轮换出额外停留。"""  # noqa: DOCSTRING_CJK

    player_input, source = run_numeric_v2_stress.choose_player_input(
        strategy="recommended",
        attempt_index=8,
        suggestions=[
            "（我走进医疗站）里面有人吗？",
            "（我观察四周）这里安全吗？",
            "（我先坐下）我们再等等。",
        ],
        route_status="transition_offered",
    )

    assert source == "recommended"
    assert player_input == "（我走进医疗站）里面有人吗？"


def test_numeric_v2_stress_marks_missing_closing_advance_as_fallback():
    assert run_numeric_v2_stress.choose_player_input(
        strategy="recommended",
        attempt_index=8,
        suggestions=[],
        route_status="transition_offered",
    ) == (
        run_numeric_v2_stress.TRANSITION_ACCEPT_INPUT,
        "transition_acceptance_fallback",
    )


def test_numeric_v2_stress_accepts_two_or_three_suggestions_only():
    for count in (2, 3):
        trace = {"quality_errors": []}
        performance = {
            "suggested_inputs": [f"选项{index}" for index in range(count)]
        }
        run_numeric_v2_stress._record_suggestion_quality(
            trace,
            performance,
            attempt=1,
            base_revision=0,
            route_status="scene_incomplete",
        )
        assert trace["quality_errors"] == []

    trace = {"quality_errors": []}
    run_numeric_v2_stress._record_suggestion_quality(
        trace,
        {"suggested_inputs": ["一", "二", "三", "四"]},
        attempt=1,
        base_revision=0,
        route_status="scene_incomplete",
    )
    assert trace["quality_errors"][0]["error_code"] == "excessive_player_suggestions"


def test_numeric_v2_stress_reports_scene_and_transition_stalls_from_runtime_state():
    trace = {
        "quality_errors": [],
        "quality_warnings": [],
        "turns": [
            {
                "attempt": index,
                "revision": index,
                "from_node_id": "scene",
                "to_node_id": "scene",
                "route_changed": False,
                "route_status": "scene_incomplete",
                "node_turn_count": index,
                "recommended_turns": 3,
            }
            for index in range(1, 6)
        ] + [
            {
                "attempt": index,
                "revision": index,
                "from_node_id": "closing",
                "to_node_id": "closing",
                "route_changed": index == 6,
                "route_status": "transition_offered",
                "transition_offered": True,
                "node_turn_count": index - 5,
                "recommended_turns": 2,
            }
            for index in range(6, 9)
        ],
    }

    run_numeric_v2_stress._record_structural_stalls(trace)

    assert [item["error_code"] for item in trace["quality_warnings"]] == [
        "stalled_scene",
    ]
    assert [item["error_code"] for item in trace["quality_errors"]] == [
        "stalled_transition",
    ]


def test_numeric_v2_stress_chat_strategy_does_not_report_expected_scene_hold():
    trace = {
        "quality_errors": [],
        "quality_warnings": [],
        "turns": [
            {
                "attempt": index,
                "revision": index,
                "from_node_id": "scene",
                "to_node_id": "scene",
                "route_changed": False,
                "route_status": "transition_offered",
                "transition_offered": True,
                "node_turn_count": index,
                "recommended_turns": 3,
            }
            for index in range(1, 8)
        ],
    }

    run_numeric_v2_stress._record_structural_stalls(
        trace,
        expected_scene_hold=True,
    )

    assert trace["quality_errors"] == []
    assert trace["quality_warnings"] == []


def test_numeric_v2_stress_report_is_written_atomically(tmp_path):
    target = tmp_path / "nested" / "report.json"

    run_numeric_v2_stress._atomic_write_json(
        target,
        {"schema": run_numeric_v2_stress.REPORT_SCHEMA, "中文": "可复核"},
    )

    assert json.loads(target.read_text(encoding="utf-8")) == {
        "schema": run_numeric_v2_stress.REPORT_SCHEMA,
        "中文": "可复核",
    }
    assert list(target.parent.glob("*.tmp")) == []


def test_numeric_v2_stress_summary_marks_turn_and_isolation_failures():
    summary = run_numeric_v2_stress.summarize_stories([
        {
            "primary_trace": {
                "committed_turns": 3,
                "turns": [{
                    "workflow_diagnostics": {
                        "evaluator_degraded": False,
                        "interaction_intent": "chat",
                        "actor_generation_attempts": 1,
                        "actor_provider_calls": 2,
                        "actor_suggestion_fill_attempts": 1,
                        "actor_suggestion_fill_provider_calls": 1,
                        "actor_suggestion_fill_reasons": {
                            "invalid_or_missing": 1,
                        },
                        "actor_base_suggestion_parse_counts": {
                            "mixed_shape_invalid": 2,
                        },
                        "transition_judge_calls": 2,
                        "transition_judge_degraded": False,
                        "transition_ownership_retries": 1,
                        "transition_author_boundary_retries": 2,
                        "transition_offer_retries": 1,
                        "semantic_rewrite_attempts": 1,
                        "phantom_transition_flags_cleared": 1,
                        "unsafe_suggestions_removed": 1,
                        "route_suggestion_reviews": 1,
                    },
                }],
                "errors": [{
                    "code": "timeout",
                    "workflow_diagnostics": {
                        "evaluator_degraded": True,
                        "interaction_intent": "scene_action",
                        "actor_generation_attempts": 2,
                        "actor_provider_calls": 3,
                        "actor_suggestion_fill_attempts": 2,
                        "actor_suggestion_fill_provider_calls": 2,
                        "actor_suggestion_fill_reasons": {
                            "invalid_or_missing": 2,
                        },
                        "actor_base_suggestion_parse_counts": {
                            "accepted_items": 3,
                        },
                        "transition_judge_calls": 1,
                        "transition_judge_degraded": True,
                        "transition_author_boundary_retries": 1,
                        "semantic_rewrite_attempts": 1,
                        "phantom_transition_flags_cleared": 2,
                        "unsafe_suggestions_removed": 2,
                        "route_suggestion_reviews": 2,
                    },
                }],
                "quality_errors": [
                    {"error_code": "manual_quality_failure"},
                ],
            },
            "fork_trace": {"committed_turns": 1, "errors": []},
            "fork": {"created": True, "active_slot_unchanged": False},
        },
        {"fatal_error": {"code": "opening_failed"}},
    ])

    assert summary == {
        "story_count": 2,
        "committed_turns": 4,
        "fatal_count": 1,
        "turn_error_count": 1,
        "quality_error_count": 1,
        "quality_warning_count": 0,
        "isolation_failure_count": 1,
        "evaluator_degraded_count": 1,
        "interaction_intent_counts": {
            "chat": 1,
            "scene_action": 1,
        },
        "actor_generation_attempts": 3,
        "actor_provider_calls": 5,
        "actor_suggestion_fill_attempts": 3,
        "actor_suggestion_fill_provider_calls": 3,
        "actor_suggestion_fill_reasons": {
            "invalid_or_missing": 3,
        },
        "actor_base_suggestion_parse_counts": {
            "mixed_shape_invalid": 2,
            "accepted_items": 3,
        },
        "transition_ownership_retries": 1,
        "transition_scene_boundary_retries": 0,
        "transition_author_boundary_retries": 3,
        "transition_offer_retries": 1,
        "semantic_rewrite_attempts": 2,
        "phantom_transition_flags_cleared": 3,
        "unsafe_suggestions_removed": 3,
        "route_suggestion_reviews": 3,
        "transition_judge_calls": 3,
        "transition_judge_degraded_count": 1,
        "dynamic_player_provider_calls": 0,
        "dynamic_player_error_count": 0,
    }


@pytest.mark.asyncio
async def test_numeric_v2_stress_failed_turn_confirms_revision_rollback(monkeypatch):
    session = SimpleNamespace(
        session_id="stress_atomic",
        revision=0,
        status="active",
        current_node_id="start",
        catgirl_binding={"character_id": "character_test"},
        performance_history=(),
        opening_performance={"suggested_inputs": []},
    )
    stored = SimpleNamespace(session=session)

    class FakeRuntime:
        async def restore_session(self, session_id):
            assert session_id == "stress_atomic"
            return stored

    async def fail_turn(**_kwargs):
        raise RuntimeError("model_failed")

    monkeypatch.setattr(
        run_numeric_v2_stress,
        "execute_numeric_v2_turn",
        fail_turn,
    )

    _, trace = await run_numeric_v2_stress._run_trace(
        runtime=FakeRuntime(),
        config_manager=SimpleNamespace(),
        current=stored,
        attempts=1,
        strategy="freeform",
        trace_name="primary",
        packing_handler=run_numeric_v2_stress._PackingLogHandler(),
        max_errors=1,
    )

    assert trace["committed_turns"] == 0
    assert trace["errors"][0]["error_code"] == "model_failed"
    assert trace["errors"][0]["atomic_rollback"] is True
    assert trace["quality_errors"] == [{
        "attempt": 1,
        "base_revision": 0,
        "route_status": "",
        "error_code": "missing_player_suggestions",
    }]


@pytest.mark.asyncio
async def test_numeric_v2_stress_reports_a_single_suggestion_as_quality_error(monkeypatch):
    session = SimpleNamespace(
        session_id="stress_two_suggestions",
        revision=0,
        status="active",
        current_node_id="start",
        catgirl_binding={"character_id": "character_test"},
        performance_history=(),
        opening_performance={
            "suggested_inputs": ["观察当前环境。"],
        },
    )
    stored = SimpleNamespace(session=session, ledger_events=())

    class FakeRuntime:
        async def restore_session(self, _session_id):
            return stored

    async def fail_turn(**_kwargs):
        raise RuntimeError("stop_after_quality_check")

    monkeypatch.setattr(run_numeric_v2_stress, "execute_numeric_v2_turn", fail_turn)

    _, trace = await run_numeric_v2_stress._run_trace(
        runtime=FakeRuntime(),
        config_manager=SimpleNamespace(),
        current=stored,
        attempts=1,
        strategy="recommended",
        trace_name="primary",
        packing_handler=run_numeric_v2_stress._PackingLogHandler(),
        max_errors=1,
    )

    assert trace["quality_errors"] == [{
        "attempt": 1,
        "base_revision": 0,
        "route_status": "",
        "error_code": "insufficient_player_suggestions",
        "suggestion_count": 1,
    }]


@pytest.mark.asyncio
async def test_numeric_v2_stress_checks_final_committed_suggestions(monkeypatch):
    session = SimpleNamespace(
        session_id="stress_final_suggestions",
        revision=0,
        status="active",
        current_node_id="start",
        catgirl_binding={"character_id": "character_test"},
        performance_history=(),
        opening_performance={
            "suggested_inputs": ["向前查看。", "留在原地。"],
        },
    )
    stored = SimpleNamespace(session=session, ledger_events=())
    next_session = SimpleNamespace(
        **{
            **session.__dict__,
            "revision": 1,
            "node_turn_count": 1,
            "metrics": {},
        }
    )
    next_stored = SimpleNamespace(
        session=next_session,
        ledger_events=({"route_status": "scene_incomplete"},),
    )

    async def commit_turn(**_kwargs):
        return SimpleNamespace(
            stored=next_stored,
            performance={"suggested_inputs": []},
            outcome=SimpleNamespace(
                ledger_event={
                    "from_node_id": "start",
                    "to_node_id": "start",
                    "metric_changes": [],
                },
                route_status="scene_incomplete",
            ),
        )

    monkeypatch.setattr(run_numeric_v2_stress, "execute_numeric_v2_turn", commit_turn)

    _, trace = await run_numeric_v2_stress._run_trace(
        runtime=SimpleNamespace(),
        config_manager=SimpleNamespace(),
        current=stored,
        attempts=1,
        strategy="recommended",
        trace_name="primary",
        packing_handler=run_numeric_v2_stress._PackingLogHandler(),
        max_errors=1,
    )

    assert trace["quality_errors"] == [{
        "attempt": 2,
        "base_revision": 1,
        "route_status": "scene_incomplete",
        "error_code": "missing_player_suggestions",
    }]
