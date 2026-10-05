"""Import commits and publish identity checks keep their narrow responsibilities."""
from copy import deepcopy

import pytest

from theater_workshop.host import InProcessPackageGateway
from theater_workshop.sdk import PackageError
from theater_workshop.sdk import numeric_v2 as compiler_module
from theater_workshop.sdk import numeric_v2_project_store as store_module

from .numeric_v2_fixture import numeric_v2_story
from .test_sdk_lifecycle import opened


def test_story_import_commits_only_the_complete_project(opened, monkeypatch):
    sdk = opened[0].sdk
    source = numeric_v2_story()
    writes = []
    write = sdk._store._write

    def observe(project):
        writes.append(deepcopy(project))
        write(project)

    monkeypatch.setattr(sdk._store, "_write", observe)
    project = sdk.import_story(source)
    compiled = InProcessPackageGateway().compile(source)
    assert len(writes) == 1
    assert project["revision"] == 2
    assert project["story"] == compiled.story
    assert project["compile_result"]["package_hash"] == compiled.package_hash
    assert project["compile_result"]["revision"] == 2
    assert project["neko_validation"] is None
    assert project["install_result"] is None


def test_story_import_write_failure_leaves_no_empty_project(opened, monkeypatch):
    sdk = opened[0].sdk
    write = sdk._store._write

    def fail_final(project):
        if project.get("story") is not None:
            raise OSError("injected_final_import_failure")
        write(project)

    monkeypatch.setattr(sdk._store, "_write", fail_final)
    with pytest.raises(OSError, match="injected_final_import_failure"):
        sdk.import_story(numeric_v2_story())
    assert sdk.list_projects() == []
    assert list(sdk.root.iterdir()) == []


def test_story_import_preserves_package_bytes_separate_from_editor_projection(opened):
    sdk = opened[0].sdk
    story = numeric_v2_story()
    story["metric_schema"]["trust"]["initial"] = 7
    story["initial_state"]["metrics"]["trust"] = 7
    original = InProcessPackageGateway().compile(story)
    project = sdk.import_story(story)
    assert project["title"] == story["meta"]["title"]
    assert project["story"] == original.story
    assert project["revision"] == 2
    assert project["compile_result"]["package_hash"] == original.package_hash
    sdk.validate(project["project_id"], base_revision=2)
    assert sdk.export(project["project_id"], base_revision=2).json_bytes == original.json_bytes


@pytest.mark.parametrize("case", ["title_whitespace", "initial_mismatch"])
def test_invalid_story_import_is_rejected_before_project_creation(opened, case):
    sdk = opened[0].sdk
    story = numeric_v2_story()
    if case == "title_whitespace":
        story["meta"]["title"] = "  原包标题  "
    else:
        story["initial_state"]["metrics"]["trust"] = 11
    with pytest.raises(PackageError):
        sdk.import_story(story)
    assert list(sdk.root.iterdir()) == []


@pytest.mark.parametrize("phase", ["dump", "flush", "fsync", "replace"])
def test_story_import_io_failure_removes_temporary_and_project_files(opened, monkeypatch, phase):
    sdk = opened[0].sdk

    def fail(*args, **kwargs):
        raise OSError("injected_import_io_failure")

    if phase == "dump":
        monkeypatch.setattr(store_module.json, "dump", fail)
    elif phase == "flush":
        create_temporary = store_module.tempfile.NamedTemporaryFile

        def temporary(*args, **kwargs):
            file = create_temporary(*args, **kwargs)
            file.flush = fail
            return file

        monkeypatch.setattr(store_module.tempfile, "NamedTemporaryFile", temporary)
    else:
        monkeypatch.setattr(store_module.os, phase, fail)
    with pytest.raises(OSError, match="injected_import_io_failure"):
        sdk.import_story(numeric_v2_story())
    assert list(sdk.root.iterdir()) == []


def test_story_import_projection_failure_leaves_no_project(opened, monkeypatch):
    sdk = opened[0].sdk

    def fail_projection(metrics):
        raise ValueError("injected_projection_failure")

    monkeypatch.setattr(store_module, "normalize_metric_drafts", fail_projection)
    with pytest.raises(ValueError, match="injected_projection_failure"):
        sdk.import_story(numeric_v2_story())
    assert sdk.list_projects() == []
    assert list(sdk.root.iterdir()) == []


def test_publish_identity_checks_do_not_repeat_author_analysis(opened, monkeypatch):
    sdk = opened[0].sdk
    source = numeric_v2_story()
    expected = sdk._compiler.compile(source)
    project = sdk.import_story(source)
    pid, rev = project["project_id"], project["revision"]
    calls = []
    analyze = compiler_module.analyze_numeric_v2_story

    def counted(story):
        calls.append(story)
        return analyze(story)

    monkeypatch.setattr(compiler_module, "analyze_numeric_v2_story", counted)
    compiled = sdk.compile(pid, base_revision=rev)
    assert len(calls) == 1
    assert compiled["json_bytes"] == expected.json_bytes
    assert compiled["project"]["compile_result"]["warnings"] == sdk._compile_receipt(expected)["warnings"]
    calls.clear()
    sdk.validate(pid, base_revision=rev)
    candidate = sdk.export(pid, base_revision=rev)
    sdk._install_candidate(candidate, lambda story: {"package_hash": candidate.package_hash})
    project = sdk.update_project(pid, base_revision=rev, changes={"editor": {"node_positions": {}}})
    assert project["compile_result"]["revision"] == project["revision"]
    assert project["neko_validation"]["revision"] == project["revision"]
    assert project["install_result"]["revision"] == project["revision"]
    assert calls == []
