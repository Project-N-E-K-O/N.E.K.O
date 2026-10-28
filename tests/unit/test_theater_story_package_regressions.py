"""Story route regressions use test-only fixtures; runtime installs no bundled stories."""

import json
from pathlib import Path

import pytest

from scripts.check_nuitka_dist import _REQUIRED_ASSETS
from services.theater.numeric_v2_registry import NumericV2PackageRegistry
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, validate_fact_candidates
from tests.unit.test_theater_numeric_v2_runtime import _binding


_STORY_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures/theater_stories"


def test_empty_registry_stays_empty_and_maintenance_preserves_imported_packages(tmp_path):
    from services.theater.numeric_v2_maintenance import maintain_numeric_v2_storage_once

    root = tmp_path / "theater"
    registry = NumericV2PackageRegistry(root / "numeric_v2/packages")
    ids = {_binding()["catgirl_name"]: _binding()["character_id"]}
    maintain_numeric_v2_storage_once(root, registry, character_ids_by_name=ids)
    assert registry.list_packages() == []
    fixture = json.loads((_STORY_FIXTURES / "story_2fa2c3823a41.json").read_text(encoding="utf-8"))
    registry.import_package(fixture)
    path = registry.package_path(fixture["meta"]["story_id"])
    before = path.read_bytes()
    maintain_numeric_v2_storage_once(root, registry, character_ids_by_name=ids)
    assert path.read_bytes() == before
    assert len(registry.list_packages()) == 1


def test_story_regression_fixtures_are_not_runtime_assets():
    repo = Path(__file__).resolve().parents[2]
    assert not (repo / "services/theater/default_numeric_v2_packages").exists()
    assert not any("default_numeric_v2_packages" in directory for directory, _ in _REQUIRED_ASSETS)


@pytest.mark.asyncio
async def test_wuxia_station_release_gates_initiation_after_restore_and_fork(tmp_path):
    registry = NumericV2PackageRegistry(_STORY_FIXTURES)
    engine = registry.load_engine("story_a400cd1c2783")
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="wuxia_station", catgirl_binding=_binding(),
        opening_performance={"performance": "官道先到驿站，山径要过窄口。", "suggested_inputs": []},
    )

    def arrival(outcome):
        return {
            "segments": [
                {"phase": "source_response", "performance": "好，出发。"},
                {"phase": "transition_bridge", "scene_narration": outcome.transition_contract["bridge_scene_narration"]},
                {"phase": "target_opening", "scene_narration": engine.nodes[outcome.session.current_node_id]["story_beat"]["opening_scene"],
                 "performance": "我们先看清眼前的情况。"},
            ],
            "suggested_inputs": [], "transition_delivered": True,
            "visible_node_id": outcome.session.current_node_id,
        }

    outcome = runtime.prepare_turn(
        current, TurnRequestV2("choose_official", 0, "我选官道，现在出发。"), (),
        transition_intent="initiate", fact_operations=(
            {"op": "set", "key": "scene:mainline_01:route_decided", "value": "官道", "visibility": "public"},
            {"op": "set", "key": "scene:mainline_01:departure_confirmed", "value": True, "visibility": "public"},
        ),
    )
    current = await runtime.commit_turn(outcome, arrival(outcome))
    assert current.session.current_node_id == "mainline_02"

    conditional_departure = "若查验已经通过，我收好文书。就按你说的继续前行，去前面的猎户棚避风。"
    outcome = runtime.prepare_turn(
        current, TurnRequestV2("unverified_departure", 1, conditional_departure), (),
        transition_intent="initiate", scene_complete=True,
    )
    assert outcome.session.current_node_id == "mainline_02"
    assert outcome.route_status == "conditions_blocked"
    current = await runtime.commit_turn(outcome, {"performance": "管事还在核对，我们等他的结论。", "suggested_inputs": []})
    restored = await NumericV2Runtime(engine, tmp_path).restore_session(current.session.session_id)
    blocked_fork = await runtime.fork_session_for_test(
        current.session.session_id, session_id="wuxia_station_blocked_fork", through_revision=2,
    )
    for stored in (restored, blocked_fork):
        blocked = runtime.prepare_turn(
            stored, TurnRequestV2("still_unverified", 2, conditional_departure), (), transition_intent="initiate",
        )
        assert blocked.session.current_node_id == "mainline_02"
        assert blocked.route_status == "conditions_blocked"

    release = "管事对光核验完文书，点头将它交还：章印无误，准许通行。"
    operations, _ = validate_fact_candidates(
        [{"op": "set", "key": "scene:mainline_02:station_passed", "value": True,
          "visibility": "public", "confidence": "confirmed", "subject": "管事",
          "action": "核验并认可", "object": "通行文书", "result": "准许通行",
          "evidence": [{"source": "actor_performance", "quote": release}]}],
        fact_contract={"facts": engine.fact_contract}, evidence_sources={"actor_performance": release},
    )
    outcome = runtime.prepare_turn(current, TurnRequestV2("ask_result", 2, "管事，核验结果如何？"), ())
    outcome = engine.finalize_fact_operations(current.session, outcome, operations=operations)
    current = await runtime.commit_turn(outcome, {"scene_narration": release, "performance": "可以走了。", "suggested_inputs": []})
    restored = await NumericV2Runtime(engine, tmp_path).restore_session(current.session.session_id)
    released_fork = await runtime.fork_session_for_test(
        current.session.session_id, session_id="wuxia_station_released_fork", through_revision=3,
    )
    for stored in (restored, released_fork):
        outcome = runtime.prepare_turn(
            stored, TurnRequestV2("leave_after_release", 3, "既然管事放行，我们去猎户棚。"), (), transition_intent="initiate",
        )
        assert outcome.session.current_node_id == "mainline_03"
        committed = await runtime.commit_turn(outcome, arrival(outcome))
        assert await NumericV2Runtime(engine, tmp_path).restore_session(committed.session.session_id) == committed
