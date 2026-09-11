from __future__ import annotations
from contextlib import nullcontext
from theater_workshop.host import InProcessPackageGateway
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler

import pytest

from theater_workshop.sdk.numeric_v2_project_store import (
    NumericV2ProjectError,
    NumericV2ProjectStore,
    NumericV2RevisionConflictError,
)
from .numeric_v2_fixture import numeric_v2_setup, numeric_v2_story


@pytest.mark.parametrize("known", [False, True])
def test_metric_edit_preserves_initial_name_disclosure(tmp_path, known):
    compiler = NumericV2Compiler(InProcessPackageGateway())
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=compiler)
    story = numeric_v2_story()
    story["initial_state"]["player_address_known"] = known
    project = store.import_story(story)
    setup = project["setup"]
    setup["metrics"][0]["initial"] += 1
    updated = store.update(project["project_id"], base_revision=project["revision"],
                           changes={"setup": setup})
    assert updated["story"]["initial_state"]["player_address_known"] is known
    assert updated["story"]["initial_state"]["metrics"][setup["metrics"][0]["id"]] == setup["metrics"][0]["initial"]
    compiler.compile(updated["story"])


def test_numeric_v2_project_persists_and_derives_status(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    updated = store.update(
        project["project_id"],
        base_revision=project["revision"],
        changes={"title": "清河晚风", "setup": numeric_v2_setup()},
    )
    generated = store.finish_generation(
        project["project_id"],
        base_revision=updated["revision"],
        story=numeric_v2_story(),
    )

    reopened = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway())).get(project["project_id"])
    assert generated["revision"] == 3
    assert reopened["title"] == "清河晚风"
    assert reopened["status"] == "editing"
    assert reopened["generation_state"] == "succeeded"


def test_numeric_v2_generation_atomically_saves_generated_setup_and_story(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    setup = numeric_v2_setup()
    setup["brief"] = "作者原始创作想法"
    current = store.update(
        project["project_id"],
        base_revision=project["revision"],
        changes={"title": "清河晚风", "setup": setup},
    )
    generated_setup = dict(setup)
    generated_setup.update({
        "brief": "模型生成的主线大纲",
        "relationship": "带着旧日误会的儿时邻居",
        "tone": ["克制", "温柔"],
    })

    generated = store.finish_generation(
        project["project_id"],
        base_revision=current["revision"],
        story=numeric_v2_story(),
        setup=generated_setup,
    )

    assert generated["setup"]["brief"] == "模型生成的主线大纲"
    assert generated["setup"]["relationship"] == "带着旧日误会的儿时邻居"
    assert generated["story"] == numeric_v2_story()
    assert generated["revision"] == current["revision"] + 1


def test_numeric_v2_generation_persists_pacing_diagnostics_as_authoring_metadata(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    diagnostics = {
        "status": "warning",
        "soft_limit": 8,
        "scenes": [{"chapter_index": 1, "estimated_turns": 10, "warning_codes": ["scene_expected_turns_exceed_8"]}],
    }

    generated = store.finish_generation(
        project["project_id"],
        base_revision=project["revision"],
        story=numeric_v2_story(),
        pacing_diagnostics=diagnostics,
    )

    assert generated["authoring"]["pacing_diagnostics"] == diagnostics
    assert "pacing_diagnostics" not in generated["story"]


def test_numeric_v2_generation_normalizes_key_prop_chapter_to_stable_node_id(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()

    generated = store.finish_generation(
        project["project_id"],
        base_revision=project["revision"],
        story=numeric_v2_story(),
        mainline_node_ids=["start"],
        key_props=[{
            "id": "dated_old_letter",
            "name": "写有日期的旧信",
            "purpose": "核对离开时间",
            "states": [{
                "chapter_index": 1,
                "owner": "catgirl",
                "state": "由女主保管",
            }],
        }],
    )

    assert generated["authoring"]["key_props"][0]["states"] == [{
        "node_id": "start",
        "owner": "catgirl",
        "state": "由女主保管",
    }]


def test_numeric_v2_quality_assessment_is_explicit_authoring_metadata_and_becomes_stale_after_story_edit(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    assessment = {
        "scope": "full_story_simple",
        "content_sha256": "a" * 64,
        "overall_score": 84.0,
        "scores": {"plot": {"score": 84.0, "summary": "因果完整"}},
        "strengths": ["因果完整"],
        "issues": [],
        "passed": True,
        "stale": False,
    }
    generated = store.finish_generation(
        project["project_id"],
        base_revision=project["revision"],
        story=numeric_v2_story(),
    )

    assert generated["authoring"]["quality_assessment"] is None
    assessed = store.record_quality_assessment(
        project["project_id"],
        assessment,
        base_revision=generated["revision"],
    )

    assert assessed["authoring"]["quality_assessment"]["overall_score"] == 84.0
    assert assessed["authoring"]["quality_assessment"]["assessed_revision"] == generated["revision"]
    assert "quality_assessment" not in generated["story"]

    story = assessed["story"]
    story["nodes"][0]["story_beat"]["summary"] = "作者修改后的摘要"
    edited = store.update(
        project["project_id"],
        base_revision=assessed["revision"],
        changes={"story": story},
    )

    assert edited["authoring"]["quality_assessment"]["stale"] is True


def test_numeric_v2_project_revision_conflict_preserves_server_project(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    current = store.update(
        project["project_id"],
        base_revision=project["revision"],
        changes={"title": "服务端版本"},
    )

    with pytest.raises(NumericV2RevisionConflictError) as caught:
        store.update(
            project["project_id"],
            base_revision=project["revision"],
            changes={"title": "过期版本"},
        )

    assert caught.value.project["revision"] == current["revision"]
    assert caught.value.project["title"] == "服务端版本"


def test_numeric_v2_failed_generation_keeps_existing_story(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    imported = store.import_story(numeric_v2_story())
    store.begin_generation(imported["project_id"], base_revision=imported["revision"])
    failed = store.fail_generation(
        imported["project_id"],
        base_revision=imported["revision"],
        error={"code": "invalid_model_json"},
    )

    assert failed["story"] == numeric_v2_story()
    assert failed["generation_state"] == "failed"
    assert failed["generation_error"]["code"] == "invalid_model_json"


def test_numeric_v2_import_preserves_declared_relationship_effect(tmp_path):
    story = numeric_v2_story()
    story["metric_schema"]["trust"]["relationship_effect"] = "positive"

    imported = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway())).import_story(story)

    assert imported["setup"]["metrics"][0]["relationship_effect"] == "positive"


def test_numeric_v2_generation_checkpoint_is_private_resumable_and_cleared_by_setup_edit(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.create()
    candidate = {"mainline_chapters": [{"title": "已生成章节"}]}
    checkpoint = {
        "candidate": candidate,
        "issues": [{"path": "ending.summary", "message": "必须填写非空文本。"}],
    }
    store.begin_generation(project["project_id"], base_revision=project["revision"])
    failed = store.fail_generation(
        project["project_id"],
        base_revision=project["revision"],
        error={"code": "invalid_mainline_generation"},
        checkpoint=checkpoint,
    )

    assert failed["generation_checkpoint"] == {
        "available": True,
        "remaining_issue_count": 1,
    }
    assert "_generation_checkpoint" not in failed
    assert store.generation_checkpoint(project["project_id"])["candidate"] == candidate

    updated_setup = dict(failed["setup"])
    updated_setup["brief"] = "修改后的创作想法"
    edited = store.update(
        project["project_id"],
        base_revision=failed["revision"],
        changes={"setup": updated_setup},
    )

    assert edited["generation_checkpoint"] is None
    assert store.generation_checkpoint(project["project_id"]) is None


def test_numeric_v2_editor_positions_persist_without_invalidating_package(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.import_story(numeric_v2_story())
    compiled = store.record_compile(
        project["project_id"],
        {"success": True, "package_hash": InProcessPackageGateway().compile(project["story"]).package_hash, "warnings": []},
        base_revision=project["revision"],
    )

    updated = store.update(
        project["project_id"],
        base_revision=compiled["revision"],
        changes={"editor": {"node_positions": {"opening": {"x": 128, "y": 256}}}},
    )

    assert updated["editor"]["node_positions"]["opening"] == {"x": 128.0, "y": 256.0}
    assert updated["status"] == "compiled"
    assert updated["compile_result"]["revision"] == updated["revision"]


def test_mainline_order_is_author_only_and_must_follow_existing_routes(tmp_path):
    store = NumericV2ProjectStore(tmp_path, transaction=nullcontext, compiler=NumericV2Compiler(InProcessPackageGateway()))
    project = store.import_story(numeric_v2_story())
    original_story = project["story"]

    updated = store.set_mainline_order(
        project["project_id"],
        base_revision=project["revision"],
        node_ids=["start"],
    )

    assert updated["revision"] == project["revision"] + 1
    assert updated["authoring"]["mainline_node_ids"] == ["start"]
    assert updated["story"] == original_story
    with pytest.raises(NumericV2ProjectError, match="mainline_order_invalid"):
        store.set_mainline_order(
            project["project_id"],
            base_revision=updated["revision"],
            node_ids=["ending_stay"],
        )
