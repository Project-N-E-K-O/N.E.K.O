"""Keep the bundled neon story's audit dependencies and player continuity valid."""

import json
from pathlib import Path

from services.theater.numeric_v2_actor import _acting_context, _beat_for_actor
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_runtime import NumericV2Engine
from services.theater_workshop.sdk.generation.facts import (
    build_fact_sources,
    fact_review_tasks,
    fact_task_context,
)
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor


STORY = Path(__file__).resolve().parents[2] / "assets/theater/story_neon_rebuild_ea2e45dab7.json"


def _story():
    return json.loads(STORY.read_text(encoding="utf-8"))


def test_neon_all_fact_review_tasks_resolve_their_actual_goal_dependencies():
    story = _story()
    context = NumericV2QualityAssessor._assessment_context(
        story, {}, {"mainline_node_ids": [f"mainline_{i:02d}" for i in range(1, 8)]},
    )
    marked, _ = build_fact_sources(context)
    tasks = fact_review_tasks(context)
    assert {task.get("node_id", task.get("source_node_id")) for task in tasks} == {
        node["id"] for node in story["nodes"]
    }
    for task in tasks:
        # Compile alone does not resolve author goal references. Exercise the
        # exact dependency expansion used before each SDK fact-review request.
        scoped, _ = fact_task_context(context, marked, task)
        assert scoped


def test_neon_broadcast_actor_projection_keeps_a_conscious_players_agency():
    story = _story()
    engine = NumericV2Engine.from_mapping(story)
    source = engine.nodes["mainline_06"]
    target = engine.nodes["node_branch_40147c5078"]
    route = next(gate for gate in source["route_gates"] if gate["target_node_id"] == target["id"])
    # Reputation selects this route; it does not establish unconsciousness.
    assert all(condition["type"] == "metric_compare" for condition in route["conditions"]["all"])
    cast = NumericV2CastProjection.from_story(story, player_name="测试玩家", catgirl_name="测试角色")
    context = _acting_context(
        engine, cast, source, story["initial_state"]["metrics"], "谨慎但能自主判断。", target=target,
    )
    visible = json.dumps({
        "state": context["target_character_state"],
        "direction": _beat_for_actor(cast, target["story_beat"]),
        "exit": target["route_gates"][0]["transition_contract"],
    }, ensure_ascii=False)
    assert "重伤昏迷" not in visible
    assert "不能参与本幕决策" not in visible
    assert "在苏醒后" not in visible
