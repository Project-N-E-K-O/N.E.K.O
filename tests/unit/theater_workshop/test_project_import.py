"""Import complete author snapshots without touching their source or trusting receipts."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import re

import pytest
from pydantic import ValidationError

from theater_workshop.host import open_workshop
from theater_workshop.sdk import NumericV2ProjectError, WorkshopError
from utils.cloudsave_runtime import MaintenanceModeError

from .test_sdk_lifecycle import TestConfig, fixed_model, opened as opened, ready_to_publish, setup_project
from .test_numeric_v2_names import NAMES, named_outline


@pytest.fixture
def author_snapshot(tmp_path):
    source = open_workshop(TestConfig(tmp_path / "source"), model_call=fixed_model)
    try:
        project = ready_to_publish(source.sdk)
        snapshot = json.loads((source.sdk.root / f'{project["project_id"]}.json').read_bytes())
        snapshot["authoring"]["quality_assessment"] = {
            "scope": "full_story_simple", "stale": True, "issues": [{"id": "retained-evidence"}],
        }
        snapshot["authoring"]["branch_drafts"] = {
            "draft_saved": {"draft_id": "draft_saved", "status": "stale", "base_revision": 1},
        }
        snapshot["install_result"] = {"success": True, "revision": project["revision"]}
        return snapshot
    finally:
        source.sdk.close()


@pytest.mark.asyncio
async def test_allocate_ids_uses_public_host_without_editing_project(opened):
    host, _ = opened
    project = await host.call("create_project")
    ids = [await host.call("allocate_id", project["project_id"], kind=kind)
           for kind in ("node", "route", "ending")]
    assert all(re.fullmatch(rf"{kind}_[0-9a-f]{{10}}", row["id"])
               for kind, row in zip(("node", "route", "ending"), ids))
    assert await host.call("get_project", project["project_id"]) == project
    with pytest.raises(ValidationError):
        await host.call("allocate_id", project["project_id"], kind="unknown")
    with pytest.raises(NumericV2ProjectError):
        await host.call("allocate_id", "project_missing", kind="node")


@pytest.mark.asyncio
async def test_complete_import_preserves_author_data_and_requires_fresh_publish(opened, author_snapshot):
    host, config = opened
    original = deepcopy(author_snapshot)
    imported = await host.call("import_project", author_snapshot)
    pid, rev = imported["project_id"], imported["revision"]
    saved = json.loads((host.sdk.root / f"{pid}.json").read_bytes())
    receipts = {key: original[key] for key in ("compile_result", "neko_validation", "install_result")}
    assert saved["imported_publish_receipts"] == receipts
    for key, value in original.items():
        assert saved[key] == (None if key in receipts else value)
    assert author_snapshot == original
    assert imported["authoring"]["branch_drafts"] == original["authoring"]["branch_drafts"]
    with pytest.raises(WorkshopError, match="current_compile_required"):
        await host.call("export", pid, base_revision=rev)
    await host.call("compile", pid, base_revision=rev)
    with pytest.raises(WorkshopError, match="current_neko_validation_required"):
        await host.call("export", pid, base_revision=rev)
    await host.call("validate", pid, base_revision=rev)
    exported = await host.call("export", pid, base_revision=rev)
    assert exported.package_hash == original["compile_result"]["package_hash"]
    await host.close()
    reopened = open_workshop(config, model_call=fixed_model)
    try:
        current = reopened.sdk.get_project(pid)
        assert current["imported_publish_receipts"] == receipts
        assert current["revision"] == rev
        edited = reopened.sdk.update_project(pid, base_revision=rev, changes={"title": "继续编辑"})
        assert edited["revision"] == rev + 1
    finally:
        reopened.sdk.close()


def test_import_running_checkpoint_can_resume_with_original_names(opened):
    host, config = opened
    setup = setup_project(host.sdk)
    # A full persisted snapshot is required; the public view intentionally hides the candidate.
    snapshot = json.loads((host.sdk.root / f'{setup["project_id"]}.json').read_bytes())
    snapshot["project_id"] = "project_resume"
    snapshot["generation_state"] = "running"
    checkpoint = {"candidate": named_outline(), "issues": [], "cast_names": NAMES}
    snapshot["_generation_checkpoint"] = checkpoint
    imported = host.sdk.import_project(snapshot)
    assert imported["generation_state"] == "interrupted"
    assert host.sdk._store.generation_checkpoint(imported["project_id"]) == checkpoint
    config.names = {"player_name": "新昵称", "catgirl_name": "新角色"}
    generated = host.sdk.generate(imported["project_id"], base_revision=imported["revision"])
    assert generated["project"]["story"]["intro"]["player_name"] == NAMES["player_name"]
    assert generated["project"]["story"]["intro"]["catgirl_name"] == NAMES["catgirl_name"]
    assert "usage" not in generated  # The saved candidate is complete; importing never calls a model.


@pytest.mark.parametrize("damage", ["revision", "path", "missing_checkpoint", "public_view",
                                    "authoring", "branches", "setup", "non_json", "nan", "unicode", "story_shape"])
def test_invalid_import_does_not_leave_partial_project(opened, author_snapshot, damage):
    sdk = opened[0].sdk
    bad = deepcopy(author_snapshot)
    if damage == "revision": bad["revision"] = True
    elif damage == "path": bad["project_id"] = "project_../../outside"
    elif damage == "missing_checkpoint": del bad["_generation_checkpoint"]
    elif damage == "public_view": bad["status"] = "verified"
    elif damage == "authoring": bad["authoring"] = []
    elif damage == "branches": bad["authoring"]["branch_drafts"] = []
    elif damage == "setup": bad["setup"] = {}
    elif damage == "non_json": bad["generation_error"] = {"value": object()}
    elif damage == "nan": bad["editor"]["node_positions"] = {"start": {"x": float("nan"), "y": 1}}
    elif damage == "unicode": bad["title"] = "\ud800"
    else: bad["story"]["nodes"] = "not nodes"
    before = {p.name: p.read_bytes() for p in sdk.root.glob("*")}
    with pytest.raises((ValidationError, NumericV2ProjectError)):
        sdk.import_project(bad)
    assert {p.name: p.read_bytes() for p in sdk.root.glob("*")} == before


def test_duplicate_and_concurrent_imports_never_overwrite(opened, author_snapshot):
    sdk = opened[0].sdk
    def importing(_):
        try:
            return sdk.import_project(author_snapshot)
        except NumericV2ProjectError as error:
            assert str(error) == "project_already_exists"
            return None
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(importing, range(2)))
    assert sum(item is not None for item in results) == 1
    pid = author_snapshot["project_id"]
    path = sdk.root / f"{pid}.json"
    before = path.read_bytes()
    with pytest.raises(NumericV2ProjectError, match="project_already_exists"):
        sdk.import_project(author_snapshot)
    assert path.read_bytes() == before


@pytest.mark.parametrize("blocked", ["maintenance", "root"])
def test_import_uses_host_write_protection(opened, author_snapshot, blocked, tmp_path):
    host, config = opened
    before = list(host.sdk.root.glob("*"))
    if blocked == "maintenance":
        config.mode = "maintenance_readonly"
        error = MaintenanceModeError
    else:
        config.app_docs_dir = tmp_path / "switched"
        error = WorkshopError
    with pytest.raises(error):
        host.sdk.import_project(author_snapshot)
    assert list(host.sdk.root.glob("*")) == before
    assert not (tmp_path / "switched").exists()


@pytest.mark.parametrize("operation", ["replace", "fsync"])
def test_io_failure_leaves_no_imported_project(opened, author_snapshot, monkeypatch, operation):
    def fail_replace(*args):
        raise OSError("injected import write failure")
    monkeypatch.setattr(f"theater_workshop.sdk.numeric_v2_project_store.os.{operation}", fail_replace)
    with pytest.raises(OSError, match="injected import"):
        opened[0].sdk.import_project(author_snapshot)
    assert list(opened[0].sdk.root.glob("*")) == []


def test_unfinished_story_can_import_and_remain_editable(opened, author_snapshot):
    author_snapshot["story"]["nodes"][0]["route_gates"] = []
    project = opened[0].sdk.import_project(author_snapshot)
    assert project["story"] == author_snapshot["story"]
    assert project["status"] == "editing"
