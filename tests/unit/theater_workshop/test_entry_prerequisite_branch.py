"""Author-selected entrance facts gate routes without turning all goals into locks."""
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from services.theater_workshop.host import InProcessPackageGateway
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, _validate_idea_outline
from services.theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchError
from .numeric_v2_fixture import numeric_v2_setup
from .test_branch_completion_contract import _case
from .test_numeric_v2_branch import _path_result
from .test_numeric_v2_generation import _idea_outline
from .test_sdk_lifecycle import opened


FIELD = "entry_prerequisite_fact_ids"


def _author_case(kind, *, declared=True):
    if kind == "mainline":
        outline = _idea_outline(3)
        scene = outline["mainline_chapters"][0]
        scene["completion_facts"].append({
            "id": "access_allowed", "description": "保管员已经允许两人进入档案室。",
            "value_type": "bool", "target_value": True, "visibility": "public",
        })
        if declared:
            scene["exit_plan"][FIELD] = ["access_allowed"]
        return outline, scene, scene["exit_plan"]
    project, service, plan, result = _case()
    if declared:
        result["transitions"][1][FIELD] = ["handoff_allowed"]
    return (project, service, plan, result), result["scenes"][0], result["transitions"][1]


def _project(kind, author):
    if kind == "mainline":
        story = NumericV2Generator()._project_story(
            title="入口前提", original_idea="明确许可后进入档案室。",
            setup=numeric_v2_setup(), outline=author, tone=["克制"],
        )
        return story, "mainline_01", "access_allowed"
    project, service, plan, result = author
    draft = service.finish_path(plan, result)
    story, _, _ = service.build_story(project, draft)
    return story, draft["scene_ids"][0], "handoff_allowed"


@pytest.mark.parametrize("kind", ["mainline", "branch"])
def test_projection_only_locks_author_selected_prerequisites(kind):
    author, scene, exit_plan = _author_case(kind)
    author_data = author if kind == "mainline" else (author[0], author[2], author[3])
    before = deepcopy(author_data)
    story, source_id, fact_id = _project(kind, author)
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    node = next(node for node in compiled.story["nodes"] if node["id"] == source_id)
    assert node["route_gates"][0]["conditions"] == {"all": [{
        "type": "fact_equals", "key": f"scene:{source_id}:{fact_id}", "value": True,
    }]}
    assert node["route_gates"][0]["transition_contract"]["trigger_fact_ids"] == exit_plan["trigger_fact_ids"]
    assert len(node["completion_contract"]["all"]) == len(exit_plan["trigger_fact_ids"])
    assert author_data == before
    assert FIELD not in json.dumps(story, ensure_ascii=False)  # Author-only input.
    if kind == "mainline":
        assert not _validate_idea_outline(author, minimum=3, maximum=6, scene_expected_turns_target=8)
    engine = NumericV2Engine.from_mapping(story)
    # A malformed/legacy ledger value must not satisfy a boolean permission.
    assert engine.preview_route(source_id, story["initial_state"]["metrics"],
                                facts={f"scene:{source_id}:{fact_id}": {"value": 1}}) is None


@pytest.mark.parametrize("kind", ["mainline", "branch"])
@pytest.mark.parametrize("permission", [None, False, True])
def test_runtime_requires_selected_permission_but_not_just_a_completion_or_polite_input(kind, permission):
    author, _, _ = _author_case(kind)
    story, source_id, fact_id = _project(kind, author)
    NumericV2Compiler(InProcessPackageGateway()).compile(story)
    engine = NumericV2Engine.from_mapping(story)
    session = replace(engine.create_session(
        session_id=f"entry_{kind}", catgirl_binding=_binding(), opening_performance=_opening(),
    ), current_node_id=source_id)
    operations = () if permission is None else ({
        "op": "set", "key": f"scene:{source_id}:{fact_id}",
        "value": permission, "visibility": "public",
    },)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("move", 0, "谢谢，现在去刚才公开的地点。"), (),
        scene_complete=True, transition_intent="initiate", fact_operations=operations,
    )
    assert (outcome.session.current_node_id != source_id) is (permission is True)
    assert session.revision == 0


@pytest.mark.parametrize("kind", ["mainline", "branch"])
@pytest.mark.parametrize("intent", ["unclear", "accept"])
def test_permission_is_not_itself_player_authorization_or_a_pending_invitation(kind, intent):
    author, _, _ = _author_case(kind)
    story, source_id, fact_id = _project(kind, author)
    engine = NumericV2Engine.from_mapping(story)
    session = replace(engine.create_session(
        session_id="no_auto_move", catgirl_binding=_binding(), opening_performance=_opening(),
    ), current_node_id=source_id)
    outcome = engine.resolve_turn(
        session, TurnRequestV2("ack", 0, "我明白了。"), (),
        scene_complete=True, transition_intent=intent, fact_operations=({
            "op": "set", "key": f"scene:{source_id}:{fact_id}",
            "value": True, "visibility": "public",
        },),
    )
    assert outcome.session.current_node_id == source_id


@pytest.mark.parametrize("kind", ["mainline", "branch"])
def test_missing_and_explicit_empty_prerequisites_preserve_ordinary_movement(kind):
    author, _, exit_plan = _author_case(kind, declared=False)
    before, node_id, _ = _project(kind, author)
    exit_plan[FIELD] = []
    after, other_id, _ = _project(kind, author)
    # Branch IDs are generated independently; compare only the route conditions.
    for story, source_id in ((before, node_id), (after, other_id)):
        engine = NumericV2Engine.from_mapping(story)
        session = replace(engine.create_session(
            session_id="ordinary_shelter", catgirl_binding=_binding(), opening_performance=_opening(),
        ), current_node_id=source_id)
        assert engine.nodes[source_id]["route_gates"][0]["conditions"] == {"all": []}
        assert engine.completion_contract_satisfied(session) is False
        outcome = engine.resolve_turn(
            session, TurnRequestV2("shelter", 0, "我们先去刚才说的棚下避雨。"), (),
            transition_intent="initiate",
        )
        assert outcome.session.current_node_id != source_id
    if kind == "mainline":
        assert after == before


@pytest.mark.parametrize("kind", ["mainline", "branch"])
@pytest.mark.parametrize("invalid", [None, "access_allowed", [1], ["unknown"], ["same", "same"]])
def test_invalid_entrance_reference_is_rejected_before_application(kind, invalid):
    author, _, exit_plan = _author_case(kind)
    if invalid == ["same", "same"]:
        key = "access_allowed" if kind == "mainline" else "handoff_allowed"
        invalid = [key, key]
    exit_plan[FIELD] = invalid
    if kind == "mainline":
        issues = _validate_idea_outline(author, minimum=3, maximum=6, scene_expected_turns_target=8)
        assert any(row["path"] == f"mainline_chapters[0].exit_plan.{FIELD}" for row in issues)
    else:
        _, service, plan, result = author
        with pytest.raises(NumericV2BranchError) as caught:
            service.finish_path(plan, result)
        assert caught.value.details["path"] == f"transitions[1].{FIELD}"


@pytest.mark.parametrize("kind", ["mainline", "branch"])
def test_display_completion_cannot_be_disguised_as_typed_route_permission(kind):
    author, scene, exit_plan = _author_case(kind)
    scene["fixed_narrations"] = [{
        "id": "notice", "text": "档案馆开放告示。", "trigger": {"type": "entry"},
        "after": [], "required_before_exit": False,
    }]
    scene["completion_facts"].append({
        "id": "notice_displayed", "description": "告示已经展示。",
        "value_type": "bool", "target_value": True, "visibility": "public",
        "fixed_narration_id": "notice",
    })
    exit_plan[FIELD] = ["notice_displayed"]
    if kind == "mainline":
        issues = _validate_idea_outline(author, minimum=3, maximum=6, scene_expected_turns_target=8)
        assert any(row["path"] == f"mainline_chapters[0].exit_plan.{FIELD}" for row in issues)
    else:
        _, service, plan, result = author
        with pytest.raises(NumericV2BranchError):
            service.finish_path(plan, result)


def test_branch_cannot_add_prerequisites_to_the_existing_source_contract():
    author, _, _ = _author_case("branch")
    _, service, plan, result = author
    result["transitions"][0][FIELD] = ["handoff_allowed"]
    with pytest.raises(NumericV2BranchError) as caught:
        service.finish_path(plan, result)
    assert caught.value.details["path"] == f"transitions[0].{FIELD}"


def test_empty_prerequisites_do_not_upgrade_a_legacy_branch_draft():
    project, service, plan, _ = _case()
    legacy = _path_result(plan)
    legacy["transitions"][1][FIELD] = []
    draft = service.finish_path(plan, legacy)
    story, _, _ = service.build_story(project, draft)
    node = next(node for node in story["nodes"] if node["id"] == draft["scene_ids"][0])
    assert node["route_gates"][0]["conditions"] == {"all": []}
    assert node["completion_contract"]["all"] == [{
        "key": f"scene:{node['id']}:branch_complete", "equals": True,
    }]


@pytest.mark.parametrize("permission,ready,ended", [
    (False, True, False), (True, False, False), (True, True, True),
])
def test_terminal_prerequisites_preserve_natural_ending_authorization(permission, ready, ended):
    author, _, _ = _author_case("mainline")
    last = author["mainline_chapters"][-1]
    definition = deepcopy(author["mainline_chapters"][0]["completion_facts"][-1])
    last["completion_facts"].append(definition)
    last["exit_plan"][FIELD] = [definition["id"]]
    story, _, _ = _project("mainline", author)
    engine = NumericV2Engine.from_mapping(story)
    node_id = "mainline_03"
    session = replace(engine.create_session(
        session_id="natural_end", catgirl_binding=_binding(), opening_performance=_opening(),
    ), current_node_id=node_id)
    # Natural ending compares the pre-turn route, so record the permission in
    # an ordinary turn first; this preserves the existing authorization timing.
    session = engine.resolve_turn(
        session, TurnRequestV2("permission", 0, "许可结果已公开。"), (),
        fact_operations=({"op": "set", "key": f"scene:{node_id}:{definition['id']}",
                          "value": permission, "visibility": "public"},),
    ).session
    outcome = engine.resolve_turn(
        session, TurnRequestV2("end", 1, "已经交付完毕。"), (),
        scene_complete=True, natural_ending_ready=ready,
    )
    assert (outcome.session.status == "ended") is ended


def test_invalid_saved_branch_prerequisite_does_not_partially_apply(opened):
    sdk = opened[0].sdk
    original, _, _, _ = _case()
    created = sdk.create_project()
    project = sdk._store.finish_generation(
        created["project_id"], base_revision=created["revision"],
        story=original["story"], mainline_node_ids=original["authoring"]["mainline_node_ids"],
    )
    project, service, plan, result = _case(project)
    result["transitions"][1][FIELD] = ["handoff_allowed"]
    draft = service.finish_path(plan, result)
    draft["transitions"][1][FIELD] = ["unknown"]
    sdk._store.save_branch_draft(project["project_id"], base_revision=project["revision"], draft=draft)
    path = sdk.root / f"{project['project_id']}.json"
    before = path.read_bytes()
    with pytest.raises(NumericV2BranchError):
        sdk.apply_branch(project["project_id"], draft["draft_id"], base_revision=project["revision"])
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["mainline", "branch"])
@pytest.mark.parametrize("value_type,value,wrong", [
    ("string", "许可有效", "许可撤回"), ("int", 2, True),
])
def test_prerequisites_reuse_declared_string_and_integer_targets(kind, value_type, value, wrong):
    author, scene, exit_plan = _author_case(kind)
    key = exit_plan[FIELD][0]
    definition = next(fact for fact in scene["completion_facts"] if fact["id"] == key)
    definition.update(value_type=value_type, target_value=value, visibility="story")
    story, source_id, fact_id = _project(kind, author)
    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    engine = NumericV2Engine.from_mapping(compiled.story)
    assert engine.preview_route(source_id, story["initial_state"]["metrics"],
                                facts={f"scene:{source_id}:{fact_id}": {"value": value}})
    assert engine.preview_route(source_id, story["initial_state"]["metrics"],
                                facts={f"scene:{source_id}:{fact_id}": {"value": wrong}}) is None


@pytest.mark.parametrize("kind", ["mainline", "branch"])
def test_sdk_compile_validate_export_keeps_only_existing_route_protocol(opened, kind):
    sdk = opened[0].sdk
    author, _, _ = _author_case(kind)
    story, source_id, fact_id = _project(kind, author)
    project = sdk.import_story(story)
    sdk.compile(project["project_id"], base_revision=project["revision"])
    sdk.validate(project["project_id"], base_revision=project["revision"])
    candidate = sdk.export(project["project_id"], base_revision=project["revision"])
    exported = json.loads(candidate.json_bytes)
    source = next(node for node in exported["nodes"] if node["id"] == source_id)
    assert source["route_gates"][0]["conditions"] == {"all": [{
        "type": "fact_equals", "key": f"scene:{source_id}:{fact_id}", "value": True,
    }]}
    assert exported == NumericV2Compiler(InProcessPackageGateway()).compile(story).story
