"""Runtime compatibility is separate from compilation and real-model rehearsal."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from services.theater.numeric_v2_options import aload_theater_module_options, default_options
from services.theater_workshop.sdk import NumericV2RevisionConflictError, WorkshopError

from .numeric_v2_fixture import numeric_v2_story
from .test_sdk_lifecycle import opened


def _piece(piece_id, *, trigger="condition", required=False, after=()):
    return {
        "id": piece_id, "text": "信上写着：愿你一路平安。",
        "trigger": ({"type": "entry"} if trigger == "entry" else
                    {"type": "condition", "condition": "玩家实际打开信封。"}),
        "after": list(after), "required_before_exit": required,
    }


def _publishable(sdk, pieces=(), *, completion=None):
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["fixed_narrations"] = list(pieces)
    if completion:
        story["nodes"][0]["completion_contract"] = {
            "all": [{"fixed_narration_id": completion}]}
    project = sdk.import_story(story)
    sdk.validate(project["project_id"], base_revision=project["revision"])
    return project


def _assess(sdk, project, **overrides):
    return sdk.assess_runtime_readiness(
        project["project_id"], base_revision=project["revision"],
        runtime_options={**default_options(), "review": False, **overrides})


@pytest.mark.parametrize("required,completion", [(True, None), (False, "letter")])
def test_required_condition_is_blocked_without_review_but_still_exportable(opened, required, completion):
    sdk = opened[0].sdk
    project = _publishable(sdk, [_piece("letter", required=required)], completion=completion)
    candidate = sdk.export(project["project_id"], base_revision=project["revision"])
    snapshot = (sdk.root / f"{project['project_id']}.json").read_bytes()
    report = _assess(sdk, project)

    assert report["status"] == "blocked"
    assert report["rehearsal"] == "not_run"
    assert report["package_hash"] == candidate.package_hash
    assert report["revision"] == project["revision"]
    issue, = report["issues"]
    assert issue["severity"] == "blocking"
    assert issue["node_id"] == "start"
    assert issue["narration_id"] == "letter"
    assert issue["dependency_paths"]
    assert report["runtime_options"] == {**default_options(), "review": False}
    assert _assess(sdk, project, review=True)["status"] == "rehearsal_required"
    assert sdk.export(project["project_id"], base_revision=project["revision"]) == candidate
    assert (sdk.root / f"{project['project_id']}.json").read_bytes() == snapshot


def test_required_condition_dependency_chain_is_reported(opened):
    sdk = opened[0].sdk
    project = _publishable(sdk, [
        _piece("first"), _piece("second", after=["first"]),
        _piece("last", required=True, after=["second"]),
    ])
    report = _assess(sdk, project)
    assert {row["narration_id"] for row in report["issues"]} == {"first", "second", "last"}
    first = next(row for row in report["issues"] if row["narration_id"] == "first")
    assert first["severity"] == "blocking"
    assert first["dependency_paths"] == [[
        "nodes[0].story_beat.fixed_narrations[2].required_before_exit",
        "nodes[0].story_beat.fixed_narrations[2]",
        "nodes[0].story_beat.fixed_narrations[2].after[0]",
        "nodes[0].story_beat.fixed_narrations[1]",
        "nodes[0].story_beat.fixed_narrations[1].after[0]",
        "nodes[0].story_beat.fixed_narrations[0]",
    ]]


def test_optional_condition_warns_and_entry_needs_no_review(opened):
    sdk = opened[0].sdk
    project = _publishable(sdk, [
        _piece("entry", trigger="entry", required=True),
        _piece("optional", after=["entry"]),
    ])
    report = _assess(sdk, project)
    assert report["status"] == "rehearsal_required"
    issue, = report["issues"]
    assert issue["narration_id"] == "optional"
    assert issue["severity"] == "warning"
    assert issue["dependency_paths"] == []


def test_readiness_binds_options_and_rejects_outdated_project(opened):
    sdk = opened[0].sdk
    project = _publishable(sdk)
    before = _assess(sdk, project)
    assert before["profile_hash"] != _assess(sdk, project, review=True)["profile_hash"]
    assert before == _assess(sdk, project)
    story = deepcopy(project["story"])
    story["nodes"][0]["story_beat"]["summary"] += "雨声渐渐停了。"
    changed = sdk.update_project(project["project_id"], base_revision=project["revision"],
                                 changes={"story": story})
    with pytest.raises(NumericV2RevisionConflictError):
        _assess(sdk, project)
    with pytest.raises(WorkshopError, match="current_compile_required"):
        _assess(sdk, changed)
    sdk.compile(changed["project_id"], base_revision=changed["revision"])
    with pytest.raises(WorkshopError, match="current_neko_validation_required"):
        _assess(sdk, changed)
    sdk.validate(changed["project_id"], base_revision=changed["revision"])
    assert _assess(sdk, changed)["package_hash"] != before["package_hash"]


def test_edit_during_diagnostics_rejects_old_report(opened, monkeypatch):
    from services.theater_workshop.sdk import workshop as workshop_module

    sdk = opened[0].sdk
    project = _publishable(sdk)
    assess = workshop_module.assess_candidate_readiness

    def edit_after_snapshot(candidate, options):
        report = assess(candidate, options)
        sdk.update_project(project["project_id"], base_revision=project["revision"],
                           changes={"title": "正在修订的旧信"})
        return report

    monkeypatch.setattr(workshop_module, "assess_candidate_readiness", edit_after_snapshot)
    with pytest.raises(NumericV2RevisionConflictError):
        _assess(sdk, project)


def test_target_options_are_snapshotted_before_candidate_loading(opened, monkeypatch):
    sdk = opened[0].sdk
    project = _publishable(sdk, [_piece("letter", required=True)])
    options = {**default_options(), "review": False}
    expected = _assess(sdk, project)
    publish = sdk._publish_candidate

    def mutate_callers_options(*args, **kwargs):
        options["review"] = "false"
        return publish(*args, **kwargs)

    monkeypatch.setattr(sdk, "_publish_candidate", mutate_callers_options)
    report = sdk.assess_runtime_readiness(project["project_id"], base_revision=project["revision"],
                                          runtime_options=options)
    assert options["review"] == "false"
    assert report == expected
    assert report["status"] == "blocked"
    assert report["runtime_options"]["review"] is False


@pytest.mark.parametrize("options", [{}, {"review": 1}, {"review": False, 1: True}, None])
def test_standalone_sdk_requires_explicit_boolean_profile(opened, options):
    sdk = opened[0].sdk
    project = _publishable(sdk)
    with pytest.raises(WorkshopError, match="runtime_options_invalid"):
        sdk.assess_runtime_readiness(project["project_id"], base_revision=project["revision"],
                                     runtime_options=options)


@pytest.mark.asyncio
async def test_host_uses_canonical_defaults_and_explicit_overrides(opened, monkeypatch):
    monkeypatch.setattr("services.theater.numeric_v2_options.MODEL_REVIEWS_ENABLED", False)
    host = opened[0]
    project = _publishable(host.sdk, [_piece("letter", required=True)])
    arguments = {"base_revision": project["revision"]}
    report = await host.call("assess_runtime_readiness", project["project_id"], **arguments)
    assert report["runtime_options"] == await aload_theater_module_options()
    assert report["status"] == "blocked"
    enabled = await host.call("assess_runtime_readiness", project["project_id"],
                              runtime_options={"review": True}, **arguments)
    assert enabled["status"] == "rehearsal_required"
    async def audits_enabled():
        return {**report["runtime_options"], "review": True}

    monkeypatch.setattr("services.theater.numeric_v2_options.aload_theater_module_options",
                        audits_enabled)
    assert (await host.call("assess_runtime_readiness", project["project_id"],
                            **arguments))["status"] == "rehearsal_required"


@pytest.mark.asyncio
@pytest.mark.parametrize("options", [{"unknown": True}, {"review": 1}, []], ids=['options0', 'options1', 'options3'])
async def test_host_rejects_invalid_target_options(opened, options):
    host = opened[0]
    project = _publishable(host.sdk)
    with pytest.raises(WorkshopError, match="runtime_options_invalid"):
        await host.call("assess_runtime_readiness", project["project_id"],
                        base_revision=project["revision"], runtime_options=options)


def test_story_fixtures_have_no_required_unavailable_narration(opened):
    import json

    sdk = opened[0].sdk
    root = Path(__file__).resolve().parents[2] / "fixtures/theater_stories"
    paths = sorted(root.glob("*.json"))
    assert len(paths) == 3
    for path in paths:
        project = sdk.import_story(json.loads(path.read_bytes()))
        sdk.validate(project["project_id"], base_revision=project["revision"])
        report = _assess(sdk, project)
        assert report["status"] == "rehearsal_required", path.name
        assert report["rehearsal"] == "not_run"
