"""验证统一只读 Fact View 的权威来源、消费者与兼容边界。"""  # noqa: DOCSTRING_CJK

from pathlib import Path

import pytest

from services.theater import fact_view, llm, rules, story_graph, story_loader
from tests.utils.theater_story_fixture import THEATER_TEST_STORY_PATH
from utils.file_utils import atomic_write_json_async, read_json_async


_STATIC_FACT = {
    "type": "fact",
    "subject": "archive",
    "predicate": "contains",
    "object": "sealed_record",
}
_PROJECTED_FACT = {
    "subject": "archive_gate",
    "predicate": "access_state",
    "object": "verified",
}


def _story_with_projection() -> dict:
    """构造与具体内置剧情无关的最小作者 Goal 合同。"""  # noqa: DOCSTRING_CJK
    return {
        "narrative_goals": [
            {
                "goal_id": "goal_verify_archive",
                "completion_fact_projections": [dict(_PROJECTED_FACT)],
            }
        ]
    }


def test_fact_view_stably_merges_static_facts_and_completed_goal_projections():
    """视图按作者顺序稳定去重，并返回副本而不是修改 Session。"""  # noqa: DOCSTRING_CJK
    story = _story_with_projection()
    story["narrative_goals"][0]["completion_fact_projections"].insert(
        0,
        {key: _STATIC_FACT[key] for key in ("subject", "predicate", "object")},
    )
    # 即使测试或未来调用方绕过 Loader，读取层也会防御性丢弃携带支线身份的投影。
    story["narrative_goals"][0]["completion_fact_projections"].append(
        dict(_PROJECTED_FACT, branch_id="branch_injected")
    )
    state = {
        "narrative_facts": [dict(_STATIC_FACT)],
        "completed_goal_ids": ["goal_verify_archive"],
        # 原始支线三元组来自模型候选，只能服务当前支线，不能进入统一权威视图。
        "branch_facts": [
            {
                "fact_id": "fact_private",
                "branch_id": "branch_private",
                "subject": "security_door",
                "predicate": "state",
                "object": "unlocked",
            }
        ],
    }

    facts = fact_view.authoritative_facts(story, state)

    assert facts == [_STATIC_FACT, _PROJECTED_FACT]
    assert facts[0] is not state["narrative_facts"][0]
    facts[0]["object"] = "changed_only_in_view"
    assert state["narrative_facts"][0]["object"] == "sealed_record"


def test_fact_view_ignores_unfinished_unknown_goals_and_raw_branch_facts():
    """只有完成的作者 Goal 可以投影；未知 ID 与模型原始事实都没有晋升路径。"""  # noqa: DOCSTRING_CJK
    story = _story_with_projection()
    state = {
        "narrative_facts": [dict(_STATIC_FACT)],
        "completed_goal_ids": ["goal_unknown"],
        "branch_facts": [dict(_PROJECTED_FACT)],
    }

    assert fact_view.authoritative_facts(story, state) == [_STATIC_FACT]


def test_static_and_dynamic_goal_completion_share_the_same_fact_view():
    """静态节点与动态支线只要完成同一 Goal，就得到相同的作者事实投影。"""  # noqa: DOCSTRING_CJK
    story = _story_with_projection()
    completing_node = {
        "node_id": "node_archive_verified",
        "completes_goal_ids": ["goal_verify_archive"],
        "state_diff": {},
    }
    static_state = rules.initial_state(story, initial_node_id="node_start")
    rules.apply_node(story, static_state, completing_node)
    dynamic_state = rules.initial_state(story, initial_node_id="node_start")
    dynamic_state["completed_goal_ids"] = ["goal_verify_archive"]

    assert fact_view.authoritative_facts(story, static_state) == [_PROJECTED_FACT]
    assert fact_view.authoritative_facts(
        story, static_state
    ) == fact_view.authoritative_facts(story, dynamic_state)
    # 投影是只读派生结果，不应被重复写入原始静态事实。
    assert static_state["narrative_facts"] == dynamic_state["narrative_facts"] == []


def test_completed_goal_projection_controls_static_node_preconditions():
    """静态节点门禁读取作者投影，同时拒绝只存在于原始支线事实中的伪证据。"""  # noqa: DOCSTRING_CJK
    story = _story_with_projection()
    story.update(
        {
            "narrative_nodes": [
                {"node_id": "node_start"},
                {
                    "node_id": "node_verified_route",
                    "preconditions": {"required_facts": [dict(_PROJECTED_FACT)]},
                },
                {
                    "node_id": "node_unverified_route",
                    "preconditions": {"forbidden_facts": [dict(_PROJECTED_FACT)]},
                },
            ],
            "edges": [
                {"from_node": "node_start", "to_node": "node_verified_route"},
                {"from_node": "node_start", "to_node": "node_unverified_route"},
            ],
        }
    )
    completed_state = {
        "current_node_id": "node_start",
        "completed_node_ids": [],
        "completed_goal_ids": ["goal_verify_archive"],
        "narrative_facts": [],
    }
    raw_branch_only_state = {
        "current_node_id": "node_start",
        "completed_node_ids": [],
        "completed_goal_ids": [],
        "narrative_facts": [],
        "branch_facts": [dict(_PROJECTED_FACT)],
    }

    assert [
        node["node_id"]
        for _, node in story_graph.outgoing_nodes(story, completed_state)
    ] == ["node_verified_route"]
    assert [
        node["node_id"]
        for _, node in story_graph.outgoing_nodes(story, raw_branch_only_state)
    ] == ["node_unverified_route"]


def test_completed_goal_projection_controls_legacy_ending_selection():
    """传统静态结局可消费作者投影，但不能消费未经投影的模型事实。"""  # noqa: DOCSTRING_CJK
    relay_fact = {
        "subject": "orbital_relay",
        "predicate": "repair_state",
        "object": "restored",
    }
    story = {
        "narrative_goals": [
            {
                "goal_id": "goal_restore_relay",
                "completion_fact_projections": [dict(relay_fact)],
            }
        ]
    }
    story["ending_attractors"] = [
        {
            "id": "ending_signal_restored",
            "required_facts": [dict(relay_fact)],
        }
    ]
    node = {
        "node_id": "node_ending_default",
        "node_type": "ending",
        "ending_id": "ending_default",
    }
    completed = {
        "narrative_facts": [],
        "completed_goal_ids": ["goal_restore_relay"],
        "clue_ids": [],
    }
    raw_branch_only = {
        "narrative_facts": [],
        "completed_goal_ids": [],
        "branch_facts": [dict(relay_fact)],
        "clue_ids": [],
    }

    assert (
        rules.ending_for_state(story, completed, node, has_outgoing=False)["ending_id"]
        == "ending_signal_restored"
    )
    assert (
        rules.ending_for_state(story, raw_branch_only, node, has_outgoing=False)[
            "ending_id"
        ]
        == "ending_default"
    )


def test_llm_consumers_share_authoritative_facts_without_branch_identity():
    """Router、Planner 与 Actor 读取同一投影，且看不到支线身份和模型原始事实。"""  # noqa: DOCSTRING_CJK
    story = _story_with_projection()
    story["runtime_guardrails"] = {
        "conditional_output_guards": [
            {
                "until_fact": dict(_PROJECTED_FACT),
                "forbidden_phrases": ["仍在等待验证"],
            }
        ]
    }
    state = {
        "narrative_facts": [dict(_STATIC_FACT)],
        "completed_goal_ids": ["goal_verify_archive"],
        "branch_facts": [
            {
                "fact_id": "fact_private",
                "branch_id": "branch_private",
                "subject": "private_subject",
                "predicate": "private_predicate",
                "object": "private_object",
            }
        ],
    }

    public = llm._public_state(story, state)

    assert public["已确认事实"] == [_STATIC_FACT, _PROJECTED_FACT]
    assert "private_subject" not in str(public)
    assert "branch_private" not in str(public)
    assert llm._active_story_forbidden_phrases(story, state) == []


@pytest.mark.asyncio
async def test_story_loader_accepts_optional_completion_fact_projection(tmp_path: Path):
    """旧 v2.5 合同可选增加作者投影，加载结果保持原三元组内容。"""  # noqa: DOCSTRING_CJK
    payload = await read_json_async(THEATER_TEST_STORY_PATH)
    payload["narrative_goals"][0]["completion_fact_projections"] = [
        dict(_PROJECTED_FACT)
    ]
    await atomic_write_json_async(tmp_path / "story.json", payload)

    loaded = await story_loader.load_story_exact(str(payload["id"]), story_dir=tmp_path)

    assert loaded["narrative_goals"][0]["completion_fact_projections"] == [
        _PROJECTED_FACT
    ]


@pytest.mark.asyncio
async def test_story_loader_rejects_unsafe_or_duplicate_completion_fact_projection(
    tmp_path: Path,
):
    """作者投影拒绝附加身份字段与同一 Goal 内的重复三元组。"""  # noqa: DOCSTRING_CJK
    source = THEATER_TEST_STORY_PATH
    invalid_cases = [
        [dict(_PROJECTED_FACT, branch_id="branch_private")],
        [dict(_PROJECTED_FACT), dict(_PROJECTED_FACT)],
    ]

    for index, projections in enumerate(invalid_cases):
        case_dir = tmp_path / str(index)
        payload = await read_json_async(source)
        payload["narrative_goals"][0]["completion_fact_projections"] = projections
        await atomic_write_json_async(case_dir / "story.json", payload)
        with pytest.raises(ValueError, match="completion fact projection"):
            await story_loader.list_stories(story_dir=case_dir)
