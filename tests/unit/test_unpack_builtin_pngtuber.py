import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from scripts import unpack_builtin_pngtuber


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _write_valid_pack(packs_root: Path, folder: str = "yui-test") -> dict:
    packs_root.mkdir(parents=True, exist_ok=True)
    members = {
        "idle.png": b"png",
        "layers/layer.png": b"png",
        "metadata.pngtube-remix.json": json.dumps(
            {"layers": [{"image": "layers/layer.png"}]}
        ).encode(),
        "model.json": json.dumps(
            {
                "model_type": "pngtuber",
                "pngtuber": {
                    "idle_image": "idle.png",
                    "talking_image": "idle.png",
                    "layered_metadata": "metadata.pngtube-remix.json",
                },
            }
        ).encode(),
    }
    archive_path = packs_root / f"{folder}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return {
        "folder": folder,
        "archive": archive_path.name,
        "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        "file_count": len(members),
        "unpacked_size": sum(len(data) for data in members.values()),
    }


def test_production_pngtuber_packs_unpack_for_static_serving(tmp_path):
    manifest = json.loads(
        (PROJECT_ROOT / "frontend" / "pngtuber-packs" / "manifest.json").read_text(
            encoding="utf-8"
        )
    )
    output_root = tmp_path / "static" / "pngtuber"

    for model in manifest["models"]:
        target = unpack_builtin_pngtuber.unpack_model(
            model,
            PROJECT_ROOT / "frontend" / "pngtuber-packs",
            output_root,
        )
        assert (target / "model.json").is_file()
        config = json.loads((target / "model.json").read_text(encoding="utf-8"))[
            "pngtuber"
        ]
        assert (target / config["layered_metadata"]).is_file()
        assert (target / unpack_builtin_pngtuber.READY_MARKER).read_text(
            encoding="utf-8"
        ) == model["archive_sha256"]

    assert sorted(path.name for path in output_root.iterdir()) == [
        "yui-lolita",
        "yui-origin",
        "yui-sister",
    ]


def test_unpack_builtin_pngtuber_rejects_path_traversal(tmp_path):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    archive_path = packs_root / "unsafe.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("../escape.png", b"escape")
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    model = {
        "folder": "unsafe",
        "archive": archive_path.name,
        "archive_sha256": digest,
        "file_count": 1,
        "unpacked_size": len(b"escape"),
    }

    with pytest.raises(ValueError, match="unsafe archive path"):
        unpack_builtin_pngtuber.unpack_model(
            model,
            packs_root,
            tmp_path / "static" / "pngtuber",
        )

    assert not (tmp_path / "static" / "escape.png").exists()


@pytest.mark.parametrize("folder", ["bad\\outside", "bad\\", "bad/"])
def test_unpack_builtin_pngtuber_rejects_non_posix_folder(folder, tmp_path):
    model = {
        "folder": folder,
        "archive": "missing.zip",
        "archive_sha256": "0" * 64,
    }

    with pytest.raises(ValueError, match="invalid built-in PNGTuber folder"):
        unpack_builtin_pngtuber.unpack_model(
            model,
            tmp_path / "packs",
            tmp_path / "static" / "pngtuber",
        )


def test_publish_failure_restores_previous_pngtuber_model(monkeypatch, tmp_path):
    packs_root = tmp_path / "packs"
    output_root = tmp_path / "static" / "pngtuber"
    model = _write_valid_pack(packs_root)
    target_dir = output_root / model["folder"]
    target_dir.mkdir(parents=True)
    (target_dir / "previous.txt").write_text("previous", encoding="utf-8")

    original_rename = Path.rename

    def fail_new_model_publish(path: Path, target: Path):
        if path.name.endswith(".tmp") and Path(target) == target_dir:
            raise OSError("injected publish failure")
        return original_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_new_model_publish)

    with pytest.raises(OSError, match="injected publish failure"):
        unpack_builtin_pngtuber.unpack_model(model, packs_root, output_root)

    assert (target_dir / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert not (output_root / ".yui-test.backup").exists()
    assert not list(output_root.glob(".yui-test.*.tmp"))


def test_next_build_restores_leftover_pngtuber_backup(tmp_path):
    packs_root = tmp_path / "packs"
    packs_root.mkdir()
    output_root = tmp_path / "static" / "pngtuber"
    backup_dir = output_root / ".yui-test.backup"
    backup_dir.mkdir(parents=True)
    (backup_dir / "previous.txt").write_text("previous", encoding="utf-8")
    model = {
        "folder": "yui-test",
        "archive": "missing.zip",
        "archive_sha256": "0" * 64,
        "file_count": 1,
        "unpacked_size": 1,
    }

    with pytest.raises(ValueError, match="archive missing"):
        unpack_builtin_pngtuber.unpack_model(model, packs_root, output_root)

    target_dir = output_root / "yui-test"
    assert (target_dir / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert not backup_dir.exists()


def test_frontend_build_scripts_unpack_builtin_pngtuber_models():
    shell_build = (PROJECT_ROOT / "build_frontend.sh").read_text(encoding="utf-8")
    batch_build = (PROJECT_ROOT / "build_frontend.bat").read_text(encoding="utf-8")

    assert "scripts/unpack_builtin_pngtuber.py" in shell_build
    assert "scripts\\unpack_builtin_pngtuber.py" in batch_build


def _multipart(packs, *, duplicate=False):
    model = _write_valid_pack(packs)
    with zipfile.ZipFile(packs / model["archive"]) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    groups = [members[:2], members[2:]]
    if duplicate:
        groups[1].append(members[0])
    parts = []
    for index, group in enumerate(groups):
        name = f"part-{index}.zip"
        path = packs / name
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
            for key, value in group:
                archive.writestr(key, value)
        parts.append(
            {
                "archive": name,
                "archive_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "archive_size": path.stat().st_size,
                "file_count": len(group),
                "unpacked_size": sum(len(value) for _, value in group),
            }
        )
    model.update(
        archive=parts[0]["archive"],
        archives=parts,
        archive_sha256=unpack_builtin_pngtuber.parts_fingerprint(parts),
        file_count=sum(p["file_count"] for p in parts),
        unpacked_size=sum(p["unpacked_size"] for p in parts),
    )
    return model


def test_multipart_unpack_is_complete_and_idempotent(tmp_path, capsys):
    packs, output = tmp_path / "packs", tmp_path / "out"
    model = _multipart(packs)
    target = unpack_builtin_pngtuber.unpack_model(model, packs, output)
    before = (target / "model.json").stat().st_mtime_ns
    unpack_builtin_pngtuber.unpack_model(model, packs, output)
    assert (target / "layers/layer.png").read_bytes() == b"png"
    assert (target / "model.json").stat().st_mtime_ns == before
    assert "up to date, skip" in capsys.readouterr().out


@pytest.mark.parametrize(
    "failure",
    ["missing", "checksum", "fingerprint", "duplicate", "part-path", "limit", "total"],
)
def test_multipart_rejection_keeps_existing_model(tmp_path, monkeypatch, failure):
    packs, output = tmp_path / "packs", tmp_path / "out"
    model = _multipart(packs, duplicate=failure == "duplicate")
    target = output / model["folder"]
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous")
    if failure == "missing":
        (packs / model["archives"][1]["archive"]).unlink()
    elif failure == "checksum":
        (packs / model["archives"][1]["archive"]).write_bytes(b"damaged")
    elif failure == "fingerprint":
        model["archives"][1]["archive_sha256"] = "0" * 64
    elif failure == "part-path":
        model["archives"][1]["archive"] = "../outside.zip"
        model["archive_sha256"] = unpack_builtin_pngtuber.parts_fingerprint(
            model["archives"]
        )
    elif failure == "limit":
        monkeypatch.setattr(unpack_builtin_pngtuber, "MAX_ARCHIVE_FILES", 1)
    elif failure == "total":
        monkeypatch.setattr(unpack_builtin_pngtuber, "MAX_COMBINED_SIZE", 1)
    with pytest.raises(ValueError):
        unpack_builtin_pngtuber.unpack_model(model, packs, output)
    assert (target / "previous.txt").read_text() == "previous"
    assert not list(output.glob("*.tmp"))


def test_tracked_frames_and_mouth_plates_must_exist(tmp_path):
    model = _write_valid_pack(tmp_path / "packs")
    target = unpack_builtin_pngtuber.unpack_model(
        model, tmp_path / "packs", tmp_path / "out"
    )
    metadata = target / "metadata.pngtube-remix.json"
    value = json.loads(metadata.read_text())
    value["tracked_animations"] = {
        "0": {
            "frames": [{"image": "idle.png", "mouth_patch": {"image": "missing.png"}}]
        }
    }
    metadata.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="missing tracked image"):
        unpack_builtin_pngtuber._validate_package(target)


def test_multipart_publish_failure_rolls_back(monkeypatch, tmp_path):
    model = _multipart(tmp_path / "packs")
    target = tmp_path / "out" / model["folder"]
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous")
    rename = Path.rename

    def fail_publish(path, destination):
        if path.name.endswith(".tmp"):
            raise OSError("publish failed")
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        unpack_builtin_pngtuber.unpack_model(
            model, tmp_path / "packs", tmp_path / "out"
        )
    assert (target / "previous.txt").read_text() == "previous"


@pytest.mark.parametrize("with_manifest", [False, True])
def test_legacy_single_archive_accepts_uppercase_hash_without_mutating_manifest(tmp_path, with_manifest):
    packs_root = tmp_path / "packs"
    entry = _write_valid_pack(packs_root)
    entry["archive_sha256"] = entry["archive_sha256"].upper()
    before = dict(entry)
    if with_manifest:
        (packs_root / "manifest.json").write_text(json.dumps({"version": 1, "models": [entry]}))
    target = unpack_builtin_pngtuber.unpack_model(entry, packs_root, tmp_path / "runtime")
    assert (target / unpack_builtin_pngtuber.READY_MARKER).read_text() == entry["archive_sha256"].lower()
    assert entry == before
    assert unpack_builtin_pngtuber.unpack_model(entry, packs_root, tmp_path / "runtime") == target


@pytest.mark.parametrize("name", ["clips/COM\u00b9.webp", "clips/LPT\u00b2.png", "clips/body.png:meta", "clips/CON.png", "NUL", "clips/com1.webp", "clips/LPT9.txt", "clips/aux .png", "clips/CONIN$", "clips/frame.png.", "clips/frame.png ", "clips/bad?.png", "clips/bad\x00.png"])
def test_nonportable_windows_member_path_is_rejected(name):
    assert unpack_builtin_pngtuber._safe_relative_path(name) is None


@pytest.mark.parametrize("name", ["clips/cone.png", "clips/com10.webp", "clips/frame-1.png", "clips/a.b.png"])
def test_portable_names_resembling_devices_remain_accepted(name):
    assert str(unpack_builtin_pngtuber._safe_relative_path(name)) == name


def test_nonportable_archive_member_is_rejected_before_install_and_keeps_old_model(tmp_path):
    packs_root = tmp_path / "packs"
    old = _write_valid_pack(packs_root)
    output = tmp_path / "runtime"
    target = unpack_builtin_pngtuber.unpack_model(old, packs_root, output)
    previous = (target / "model.json").read_bytes()
    archive_path = packs_root / old["archive"]
    with zipfile.ZipFile(archive_path, "a") as archive:
        archive.writestr("clips/frame.png:meta", b"bad")
    candidate = {**old, "archive_sha256": hashlib.sha256(archive_path.read_bytes()).hexdigest(), "file_count": old["file_count"] + 1, "unpacked_size": old["unpacked_size"] + 3}
    with pytest.raises(ValueError, match="unsafe archive path"):
        unpack_builtin_pngtuber.unpack_model(candidate, packs_root, output)
    assert (target / "model.json").read_bytes() == previous
    assert not (output / ".yui-test.backup").exists()


@pytest.mark.parametrize("rehash", [False, True])
def test_multipart_uppercase_hashes_are_accepted_without_mutating_manifest(tmp_path, rehash):
    import copy
    packs, output = tmp_path / "packs", tmp_path / "out"
    model = _multipart(packs)
    for part in model["archives"]:
        part["archive_sha256"] = part["archive_sha256"].upper()
    if rehash:
        model["archive_sha256"] = unpack_builtin_pngtuber.parts_fingerprint(model["archives"])
    model["archive_sha256"] = model["archive_sha256"].upper()
    before = copy.deepcopy(model)
    target = unpack_builtin_pngtuber.unpack_model(model, packs, output)
    unpack_builtin_pngtuber.unpack_model(model, packs, output)
    assert model == before
    assert (target / "layers/layer.png").read_bytes() == b"png"
