import io
import os
import tarfile
from pathlib import Path

import pytest

from scripts import unpack_builtin_live2d


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL = "yui-test"


def _required_members(model: str = MODEL) -> dict[str, bytes]:
    return {
        f"{model}/{name.format(model=model)}": b"data"
        for name in unpack_builtin_live2d.REQUIRED_FILES
    }


def _write_archive(assets_root: Path, members: dict[str, bytes], model: str = MODEL) -> Path:
    assets_root.mkdir(parents=True, exist_ok=True)
    archive_path = assets_root / f"{model}.tar.gz"
    with tarfile.open(archive_path, "w:gz") as archive:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return archive_path


def test_production_live2d_archives_pass_member_checks(tmp_path):
    # Only inspect the member list: the extraction path itself is covered by
    # the synthetic archives below, and the real archives are ~60 MB unpacked.
    for model in unpack_builtin_live2d.MODELS:
        with tarfile.open(PROJECT_ROOT / "assets" / f"{model}.tar.gz", "r:gz") as archive:
            members = unpack_builtin_live2d._safe_members(archive, model)
        names = {member.name for member in members if member.isfile()}
        for required in unpack_builtin_live2d.REQUIRED_FILES:
            assert f"{model}/{required.format(model=model)}" in names


def test_models_match_builtin_live2d_model_names():
    from config.character_defaults import BUILTIN_LIVE2D_MODEL_NAMES

    assert sorted(unpack_builtin_live2d.MODELS) == sorted(BUILTIN_LIVE2D_MODEL_NAMES)


def test_unpack_skips_up_to_date_model_and_refreshes_newer_archive(tmp_path):
    assets_root = tmp_path / "assets"
    static_root = tmp_path / "static"
    archive_path = _write_archive(assets_root, _required_members())
    target = unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)
    sentinel = target / "sentinel.txt"
    sentinel.write_text("kept", encoding="utf-8")
    marker = target / unpack_builtin_live2d.COMPLETE_MARKER

    os.utime(archive_path, (marker.stat().st_mtime - 10,) * 2)
    unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)
    assert sentinel.is_file()

    os.utime(archive_path, (marker.stat().st_mtime + 10,) * 2)
    unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)
    assert not sentinel.exists()
    assert unpack_builtin_live2d._missing_files(target, MODEL) == []


def test_unpack_reextracts_when_required_file_is_missing(tmp_path):
    assets_root = tmp_path / "assets"
    static_root = tmp_path / "static"
    archive_path = _write_archive(assets_root, _required_members())
    target = unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)
    marker = target / unpack_builtin_live2d.COMPLETE_MARKER
    os.utime(archive_path, (marker.stat().st_mtime - 10,) * 2)
    (target / f"{MODEL}.moc3").unlink()

    unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)

    assert (target / f"{MODEL}.moc3").is_file()


@pytest.mark.parametrize(
    "bad_name",
    ["../escape.txt", f"{MODEL}/../escape.txt", "other-model/file.txt", "/abs.txt"],
)
def test_unpack_rejects_paths_outside_model_dir(tmp_path, bad_name):
    members = _required_members()
    members[bad_name] = b"escape"
    assets_root = _write_archive(tmp_path / "assets", members).parent

    with pytest.raises(ValueError, match="unsafe archive path"):
        unpack_builtin_live2d.unpack_model(MODEL, assets_root, tmp_path / "static")

    assert not (tmp_path / "escape.txt").exists()
    assert not (tmp_path / "static" / MODEL).exists()
    assert list((tmp_path / "static").iterdir()) == []


def test_unpack_rejects_links(tmp_path):
    assets_root = tmp_path / "assets"
    archive_path = _write_archive(assets_root, _required_members())
    with tarfile.open(archive_path, "r:gz") as source:
        members = [(info, source.extractfile(info).read()) for info in source.getmembers()]
    with tarfile.open(archive_path, "w:gz") as archive:
        for info, data in members:
            archive.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo(f"{MODEL}/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../outside"
        archive.addfile(link)

    with pytest.raises(ValueError, match="unsupported archive member type"):
        unpack_builtin_live2d.unpack_model(MODEL, assets_root, tmp_path / "static")


def test_incomplete_archive_keeps_previous_model(tmp_path):
    members = _required_members()
    del members[f"{MODEL}/{MODEL}.vtube.json"]
    assets_root = _write_archive(tmp_path / "assets", members).parent
    static_root = tmp_path / "static"
    target = static_root / MODEL
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous", encoding="utf-8")

    with pytest.raises(ValueError, match=f"{MODEL}.vtube.json"):
        unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)

    assert (target / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert sorted(path.name for path in static_root.iterdir()) == [MODEL]


def test_publish_failure_restores_previous_model(monkeypatch, tmp_path):
    assets_root = _write_archive(tmp_path / "assets", _required_members()).parent
    static_root = tmp_path / "static"
    target = static_root / MODEL
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous", encoding="utf-8")

    original_rename = Path.rename

    def fail_new_model_publish(path: Path, destination: Path):
        if path.parent.name.startswith(f".{MODEL}.extract-") and Path(destination) == target:
            raise OSError("injected publish failure")
        return original_rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_new_model_publish)

    with pytest.raises(OSError, match="injected publish failure"):
        unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)

    assert (target / "previous.txt").read_text(encoding="utf-8") == "previous"
    assert sorted(path.name for path in static_root.iterdir()) == [MODEL]


def test_backup_cleanup_failure_does_not_fail_unpack(monkeypatch, tmp_path, capsys):
    assets_root = _write_archive(tmp_path / "assets", _required_members()).parent
    static_root = tmp_path / "static"
    target = static_root / MODEL
    target.mkdir(parents=True)
    (target / "previous.txt").write_text("previous", encoding="utf-8")

    original_rmtree = unpack_builtin_live2d.shutil.rmtree

    def locked_backup_rmtree(path, *args, **kwargs):
        # Simulate a file held open in the old copy: rmtree raises, or with
        # ignore_errors=True returns without removing it.
        if Path(path).name.startswith(f".{MODEL}.backup-"):
            if kwargs.get("ignore_errors"):
                return None
            raise PermissionError("file in use")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(unpack_builtin_live2d.shutil, "rmtree", locked_backup_rmtree)

    result = unpack_builtin_live2d.unpack_model(MODEL, assets_root, static_root)

    assert result == target
    assert unpack_builtin_live2d._missing_files(target, MODEL) == []
    assert not (target / "previous.txt").exists()
    leftovers = list(static_root.glob(f".{MODEL}.backup-*"))
    assert len(leftovers) == 1
    assert "please delete it manually" in capsys.readouterr().out


def test_missing_archive_is_an_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="archive missing"):
        unpack_builtin_live2d.unpack_model(MODEL, tmp_path / "assets", tmp_path / "static")


def test_setup_win7_unpacks_builtin_models():
    setup_script = (PROJECT_ROOT / "setup_win7.bat").read_text(encoding="utf-8")

    assert "scripts\\unpack_builtin_pngtuber.py" in setup_script
    assert "scripts\\unpack_builtin_live2d.py" in setup_script
