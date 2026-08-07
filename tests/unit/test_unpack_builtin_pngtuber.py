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
        assert (target / "metadata.pngtube-remix.json").is_file()
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
