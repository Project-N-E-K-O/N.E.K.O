"""Pinned model installation does not extract arbitrary archive paths."""

import hashlib
import io
import tarfile

import pytest

from scripts import provision_wake_word_model as provisioner
from main_logic.voice_identity_service import wake_word_bundle as model_bundle


def make_archive(tmp_path, *, symlink=False):
    archive = tmp_path / "model.tar.bz2"
    with tarfile.open(archive, "w:bz2") as bundle:
        for index, name in enumerate(provisioner.ASSETS):
            entry = tarfile.TarInfo(f"{provisioner.MODEL_NAME}/{name}")
            if symlink and index == 0:
                entry.type, entry.linkname = tarfile.SYMTYPE, "../../outside"
                bundle.addfile(entry)
            else:
                entry.size = 4
                bundle.addfile(entry, io.BytesIO(b"test"))
        extra = tarfile.TarInfo("../../outside")
        extra.size = 4
        bundle.addfile(extra, io.BytesIO(b"bad!"))
    return archive


def test_pinned_archive_installs_only_expected_assets(tmp_path, monkeypatch):
    archive = make_archive(tmp_path)
    monkeypatch.setattr(model_bundle, "MODEL_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    monkeypatch.setattr(provisioner, "_validate_bundle", lambda _path: None)
    target = tmp_path / "installed"
    provisioner.provision(target, archive)
    installed = model_bundle.resolve_cached_model_dir(target)
    assert installed is not None
    assert {p.name for p in installed.iterdir()} == {*provisioner.ASSETS, "bundle.json"}
    assert all((installed / name).read_bytes() == b"test" for name in provisioner.ASSETS)
    assert not (tmp_path / "outside").exists()


def test_corrupt_archive_cannot_replace_assets(tmp_path):
    archive = make_archive(tmp_path)
    target = tmp_path / "installed"
    with pytest.raises(model_bundle.WakeWordBundleError, match="resource_archive_invalid"):
        provisioner.provision(target, archive)
    assert not (target / "current.json").exists()


def test_expected_name_symlink_is_rejected(tmp_path, monkeypatch):
    archive = make_archive(tmp_path, symlink=True)
    monkeypatch.setattr(model_bundle, "MODEL_SHA256", hashlib.sha256(archive.read_bytes()).hexdigest())
    with pytest.raises(model_bundle.WakeWordBundleError, match="resource_archive_invalid"):
        provisioner.provision(tmp_path / "installed", archive)
