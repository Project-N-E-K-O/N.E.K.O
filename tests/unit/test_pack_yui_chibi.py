"""Small real-package fixtures and publication fault injection for YUI builds."""
import base64
import hashlib
import json
import os
from pathlib import Path

import pytest

from scripts import pack_yui_chibi as pack
from scripts import pngtuber_publish as publish
from scripts import unpack_builtin_pngtuber as unpack


PNG_PLATE = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=")

def source(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "body.png").write_bytes(b"body")
    (root / "plate.png").write_bytes(PNG_PLATE)
    frame = {"time_ms": 0, "duration_ms": 100, "image": "body.png", "x": 1, "y": 2,
             "mouth_patch": {"image": "plate.png"}}
    track = {"duration_ms": 100, "playback": "loop", "mouth_crop": [0, 0, 2, 2], "frames": [frame]}
    metadata = {"state_count": 1, "state_catalog": [{"state_index": 0, "name": "idle"}],
                "reviewed_actions": {"version": 1, "groups": {"idle": [0]}},
                "tracked_animations": {"0": track}, "layers": [{"image": "body.png"}]}
    (root / "metadata.actions.json").write_text(json.dumps(metadata))
    (root / "model.json").write_text(json.dumps({"model_type": "pngtuber", "pngtuber": {}}))
    return root


def packs(tmp_path):
    root = tmp_path / "packs"
    root.mkdir()
    (root / "manifest.json").write_text(json.dumps({"version": 1, "models": [{"folder": "other", "untouched": True}]}))
    return root


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def test_build_and_unpack_roundtrip_keeps_images_and_other_models(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    before = snapshot(src)
    entry = pack.build(src, dest)
    target = unpack.unpack_model(entry, dest, tmp_path / "runtime")
    assert (target / "clips/idle/frames/0000.png").read_bytes() == b"body"
    assert (target / "clips/idle/mouths/0000.png").read_bytes() == PNG_PLATE
    assert json.loads((dest / "manifest.json").read_text())["models"][0] == {"folder": "other", "untouched": True}
    assert snapshot(src) == before
    again = pack.build(src, dest)
    assert again == entry


@pytest.mark.parametrize("limit", ["MAX_COMBINED_FILES", "MAX_COMBINED_SIZE"])
def test_over_limit_build_does_not_publish_any_files(tmp_path, monkeypatch, limit):
    src, dest = source(tmp_path), packs(tmp_path)
    before = snapshot(dest)
    monkeypatch.setattr(pack, limit, 1)
    with pytest.raises(ValueError, match="combined archive limits"):
        pack.build(src, dest)
    assert snapshot(dest) == before


@pytest.mark.parametrize("failure", ["zip", "manifest.json", "model.json", "reviewed-resource-manifest.json"])
def test_publication_failure_restores_all_old_files(tmp_path, monkeypatch, failure):
    src, dest = source(tmp_path), packs(tmp_path)
    pack.build(src, dest)
    before = snapshot(dest)
    (src / "body.png").write_bytes(b"new pixels")
    original = publish.os.replace
    fired = False

    def fail_once(a, b):
        nonlocal fired
        matches = str(b).endswith(".zip") if failure == "zip" else Path(b).name == failure
        if not fired and matches and "new" in Path(a).parts:
            fired = True
            raise OSError("injected publication failure")
        return original(a, b)

    monkeypatch.setattr(publish.os, "replace", fail_once)
    with pytest.raises(OSError, match="injected"):
        pack.build(src, dest)
    assert fired
    assert snapshot(dest) == before


def test_smaller_generation_removes_old_parts_after_success(tmp_path, monkeypatch):
    src, dest = source(tmp_path), packs(tmp_path)
    monkeypatch.setattr(pack, "PART_FILES", 1)
    old = pack.build(src, dest)
    assert len(old["archives"]) > 1
    monkeypatch.setattr(pack, "PART_FILES", 600)
    new = pack.build(src, dest)
    assert {p.name for p in dest.glob("yui-chibi-*.zip")} == {row["archive"] for row in new["archives"]}


def test_interrupted_publication_journal_is_recovered_idempotently(tmp_path):
    root = packs(tmp_path)
    before = (root / "manifest.json").read_bytes()
    tx = root / ".yui-chibi-publish"
    (tx / "old").mkdir(parents=True)
    (tx / "old/manifest.json").write_bytes(before)
    (tx / "journal.json").write_text(json.dumps([{"path": "manifest.json", "existed": True}, {"path": "yui-chibi-001.zip", "existed": False}]))
    (root / "manifest.json").write_text("partial")
    (root / "yui-chibi-001.zip").write_bytes(b"partial")
    publish.recover_publication(root)
    publish.recover_publication(root)
    assert (root / "manifest.json").read_bytes() == before
    assert not (root / "yui-chibi-001.zip").exists()


def test_simultaneous_publish_is_rejected_without_deleting_a_live_transaction(tmp_path):
    root = packs(tmp_path)
    with publish.publication_lock(root, "publish"):
        with pytest.raises(OSError):
            with publish.publication_lock(root, "publish"):
                pytest.fail("second lock acquired")


def test_source_output_overlap_rejected(tmp_path):
    src = source(tmp_path)
    with pytest.raises(ValueError, match="overlap"):
        pack.build(src, src / "packages")


@pytest.mark.parametrize("mutation", ["group", "catalog", "duration", "position", "missing"])
def test_invalid_reviewed_metadata_never_publishes(tmp_path, mutation):
    src, dest = source(tmp_path), packs(tmp_path)
    path = src / "metadata.actions.json"
    metadata = json.loads(path.read_text())
    if mutation == "group": metadata["reviewed_actions"]["groups"]["happy"] = [99]
    if mutation == "catalog": metadata["state_count"] = 2
    if mutation == "duration": metadata["tracked_animations"]["0"]["duration_ms"] = 101
    if mutation == "position": metadata["tracked_animations"]["0"]["frames"][0]["x"] = "bad"
    if mutation == "missing": metadata["tracked_animations"]["0"]["frames"][0]["image"] = "missing.png"
    path.write_text(json.dumps(metadata))
    before = snapshot(dest)
    with pytest.raises((ValueError, KeyError)):
        pack.build(src, dest)
    assert snapshot(dest) == before


def test_backup_cleanup_failure_does_not_fail_a_successful_install(tmp_path, monkeypatch):
    src, dest = source(tmp_path), packs(tmp_path)
    old = pack.build(src, dest)
    runtime = tmp_path / "runtime"
    unpack.unpack_model(old, dest, runtime)
    (src / "body.png").write_bytes(b"updated")
    new = pack.build(src, dest)
    original = unpack.shutil.rmtree
    def occupied(path, *args, **kwargs):
        if str(path).endswith(".backup"): raise PermissionError("occupied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(unpack.shutil, "rmtree", occupied)
    with pytest.warns(RuntimeWarning, match="cleanup deferred"):
        target = unpack.unpack_model(new, dest, runtime)
    assert (target / "clips/idle/frames/0000.png").read_bytes() == b"updated"


def test_only_old_owned_temp_directories_are_cleaned(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    entry = pack.build(src, dest)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    stale = runtime / (".yui-chibi." + "a" * 32 + ".tmp")
    recent = runtime / (".yui-chibi." + "b" * 32 + ".tmp")
    foreign = runtime / ".yui-chibi.user.tmp"
    for p in (stale, recent, foreign): p.mkdir()
    os.utime(stale, (1, 1))
    unpack.unpack_model(entry, dest, runtime)
    assert not stale.exists()
    assert recent.exists() and foreign.exists()


def test_production_pack_assets_stay_identical_to_the_reviewed_manifest():
    root = Path(__file__).resolve().parents[2] / "frontend/pngtuber-packs"
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    entry = next(m for m in manifest["models"] if m["folder"] == "yui-chibi")
    for part in entry["archives"]:
        assert hashlib.sha256((root / part["archive"]).read_bytes()).hexdigest() == part["archive_sha256"]


def test_distribution_verifier_requires_new_model_and_key_assets(tmp_path):
    from scripts import check_nuitka_dist
    required = [(rel, name) for rel, name in check_nuitka_dist._REQUIRED_ASSETS if rel == "static/pngtuber/yui-chibi"]
    assert {name for _, name in required} == {"model.json", "metadata.actions.json", "idle.png"}
    for rel, name in required:
        assert check_nuitka_dist._check_asset(tmp_path, rel, name)
        file = tmp_path / rel / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(b"asset")
        assert check_nuitka_dist._check_asset(tmp_path, rel, name) is None
    shell = (Path(__file__).resolve().parents[2] / "scripts/verify_frontend_build.sh").read_text()
    assert "yui-lolita yui-origin yui-sister yui-chibi" in shell


def test_installer_recovers_interrupted_publication_before_reading_parts(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    entry = pack.build(src, dest)
    name = entry["archive"]
    archive = dest / name
    original = archive.read_bytes()
    tx = dest / ".yui-chibi-publish"
    (tx / "old").mkdir(parents=True)
    (tx / "old" / name).write_bytes(original)
    (tx / "journal.json").write_text(json.dumps([{"path": name, "existed": True}]))
    archive.write_bytes(b"interrupted")
    target = unpack.unpack_model(entry, dest, tmp_path / "runtime")
    assert (target / "clips/idle/frames/0000.png").read_bytes() == b"body"
    assert archive.read_bytes() == original


def interrupted_generation(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    (dest / "manifest.json").write_text(json.dumps({"version": 1, "models": []}))
    old = pack.build(src, dest)
    original = snapshot(dest)
    (src / "body.png").write_bytes(b"new-generation")
    new = pack.build(src, dest)
    tx = dest / ".yui-chibi-publish"
    entries = []
    for name, data in original.items():
        backup = tx / "old" / name
        backup.parent.mkdir(parents=True, exist_ok=True)
        backup.write_bytes(data)
        entries.append({"path": name, "existed": True})
    (tx / "journal.json").write_text(json.dumps(entries))
    return dest, old, new


def test_main_recovers_then_reads_and_consumes_manifest_under_one_lock(tmp_path, monkeypatch):
    dest, old, new = interrupted_generation(tmp_path)
    assert old["archive_sha256"] != new["archive_sha256"]
    monkeypatch.setattr(unpack, "PACKS_ROOT", dest)
    monkeypatch.setattr(unpack, "MANIFEST_PATH", dest / "manifest.json")
    runtime = tmp_path / "runtime"
    monkeypatch.setattr(unpack, "OUTPUT_ROOT", runtime)
    original_read = unpack._read_json
    checked = []
    def read_locked(path):
        if path == dest / "manifest.json":
            with pytest.raises(OSError):
                with publish.publication_lock(dest, "publish"):
                    pytest.fail("manifest read outside lock")
            assert not (dest / ".yui-chibi-publish/journal.json").exists()
            checked.append(True)
        return original_read(path)
    monkeypatch.setattr(unpack, "_read_json", read_locked)
    unpack.main()
    assert checked
    target = runtime / "yui-chibi"
    assert (target / "clips/idle/frames/0000.png").read_bytes() == b"body"
    assert (target / unpack.READY_MARKER).read_text() == old["archive_sha256"]


def test_public_installer_rejects_stale_entry_after_manifest_rollback(tmp_path):
    dest, old, stale = interrupted_generation(tmp_path)
    with pytest.raises(ValueError, match="does not match current"):
        unpack.unpack_model(stale, dest, tmp_path / "runtime")
    assert not (tmp_path / "runtime/yui-chibi").exists()
    target = unpack.unpack_model(old, dest, tmp_path / "runtime")
    assert (target / unpack.READY_MARKER).read_text() == old["archive_sha256"]
    assert (target / "clips/idle/frames/0000.png").read_bytes() == b"body"


def test_recovery_cleanup_interruption_does_not_replay_deleted_backups(tmp_path, monkeypatch):
    dest, old, _ = interrupted_generation(tmp_path)
    original = publish.shutil.rmtree
    def interrupted(path, *args, **kwargs):
        path = Path(path)
        if path.name == ".yui-chibi-publish":
            assert not (path / "journal.json").exists()
            original(path / "old")
            raise OSError("interrupted cleanup")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(publish.shutil, "rmtree", interrupted)
    with pytest.raises(OSError, match="interrupted cleanup"):
        publish.recover_publication(dest)
    monkeypatch.setattr(publish.shutil, "rmtree", original)
    publish.recover_publication(dest)
    model = json.loads((dest / "manifest.json").read_text())["models"][0]
    assert model == old


def test_journal_publication_failure_never_changes_published_outputs(tmp_path, monkeypatch):
    src, dest = source(tmp_path), packs(tmp_path)
    pack.build(src, dest)
    before = snapshot(dest)
    (src / "body.png").write_bytes(b"new")
    original = publish.os.replace
    def fail_journal(a, b):
        if Path(b).name == "journal.json":
            assert json.loads(Path(a).read_text())
            raise OSError("journal publication failed")
        return original(a, b)
    monkeypatch.setattr(publish.os, "replace", fail_journal)
    with pytest.raises(OSError, match="journal publication"):
        pack.build(src, dest)
    assert snapshot(dest) == before


def test_partial_unpublished_journal_is_discarded_without_touching_targets(tmp_path):
    dest = packs(tmp_path)
    before = (dest / "manifest.json").read_bytes()
    tx = dest / ".yui-chibi-publish"
    tx.mkdir()
    (tx / "journal.tmp").write_text('[{"path":')
    publish.recover_publication(dest)
    assert (dest / "manifest.json").read_bytes() == before
    assert not tx.exists()


def test_corrupt_installed_null_track_is_rebuilt_from_valid_archive(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    entry = pack.build(src, dest)
    target = unpack.unpack_model(entry, dest, tmp_path / "runtime")
    path = target / "metadata.actions.json"
    metadata = json.loads(path.read_text())
    metadata["tracked_animations"]["0"] = None
    path.write_text(json.dumps(metadata))
    unpack.unpack_model(entry, dest, tmp_path / "runtime")
    assert json.loads(path.read_text())["tracked_animations"]["0"]["playback"] == "loop"


def test_build_removes_authoring_notes_without_modifying_the_mother_source(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    path = src / "model.json"
    model = json.loads(path.read_text())
    model["_authoring"] = {"notes": "historical production log"}
    path.write_text(json.dumps(model))
    entry = pack.build(src, dest)
    target = unpack.unpack_model(entry, dest, tmp_path / "runtime")
    assert "_authoring" not in json.loads((target / "model.json").read_text())
    assert "_authoring" not in json.loads((dest / "yui-chibi-source/model.json").read_text())
    assert json.loads(path.read_text())["_authoring"] == model["_authoring"]


@pytest.mark.parametrize("extension", ["webp", "jpg", "gif"])
def test_non_png_mouth_patch_rejected_before_publication(tmp_path, extension):
    src, dest = source(tmp_path), packs(tmp_path)
    metadata_path = src / "metadata.actions.json"
    metadata = json.loads(metadata_path.read_text())
    metadata["tracked_animations"]["0"]["frames"][0]["mouth_patch"]["image"] = f"plate.{extension}"
    (src / f"plate.{extension}").write_bytes(b"not png")
    metadata_path.write_text(json.dumps(metadata))
    before = snapshot(dest)
    with pytest.raises(ValueError, match="mouth patch must be PNG"):
        pack.build(src, dest)
    assert snapshot(dest) == before


def test_disguised_non_png_patch_rejected_without_publishing(tmp_path):
    src, dest = source(tmp_path), packs(tmp_path)
    (src / "plate.png").write_bytes(b"RIFF webp data")
    before = snapshot(dest)
    with pytest.raises(ValueError, match="must contain PNG data"):
        pack.build(src, dest)
    assert snapshot(dest) == before


def test_publication_lock_waits_for_release_and_times_out_without_entering(tmp_path):
    import threading
    acquired, release = threading.Event(), threading.Event()
    errors = []
    def holder():
        try:
            with publish.publication_lock(tmp_path, "concurrent"):
                acquired.set()
                release.wait(3)
        except Exception as error:
            errors.append(error)
            acquired.set()
    thread = threading.Thread(target=holder)
    thread.start()
    assert acquired.wait(2)
    try:
        with pytest.raises(TimeoutError):
            with publish.publication_lock(tmp_path, "concurrent", timeout=.1):
                pytest.fail("entered held lock")
        timer = threading.Timer(.1, release.set)
        timer.start()
        try:
            with publish.publication_lock(tmp_path, "concurrent", timeout=2):
                assert release.is_set()
        finally:
            timer.join()
    finally:
        release.set()
        thread.join()
    assert not errors


def test_publication_lock_file_does_not_grow_or_require_writable_resource_root(tmp_path, monkeypatch):
    lock_root = tmp_path / "locks"
    lock_root.mkdir()
    monkeypatch.setattr(publish.tempfile, "gettempdir", lambda: str(lock_root))
    resource_root = tmp_path / "absent-resources"
    for _ in range(3):
        with publish.publication_lock(resource_root, "publish"):
            pass
    assert not resource_root.exists()
    assert [path.stat().st_size for path in lock_root.iterdir()] == [1]


@pytest.mark.parametrize("name", ["idle", "happy"])
def test_duplicate_action_names_rejected_before_overwriting_or_publication(tmp_path, name):
    import copy
    src, dest = source(tmp_path), packs(tmp_path)
    path = src / "metadata.actions.json"
    metadata = json.loads(path.read_text())
    metadata["state_count"] = 2
    metadata["state_catalog"] = [{"state_index": 0, "name": name}, {"state_index": 1, "name": name}]
    metadata["tracked_animations"]["1"] = copy.deepcopy(metadata["tracked_animations"]["0"])
    metadata["reviewed_actions"]["groups"]["idle"] = [0, 1]
    path.write_text(json.dumps(metadata))
    before, source_before = snapshot(dest), snapshot(src)
    with pytest.raises(ValueError, match="Duplicate reviewed action name"):
        pack.build(src, dest)
    assert snapshot(dest) == before
    assert snapshot(src) == source_before
