import importlib.util
import io
import json
import threading
import time
from pathlib import Path

import pytest

from services import pngtuber_downloads as downloads

spec = importlib.util.spec_from_file_location(
    "download_pack_fixture", Path(__file__).with_name("test_unpack_builtin_pngtuber.py")
)
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)


def make_manager(tmp_path, *, published=True):
    packs = tmp_path / "packs"
    package = fixture._multipart(packs)
    row = {
        "id": "test",
        "name": "Test",
        "version": "v1",
        "published": published,
        "release_url": "https://github.com/UnityMer/N.E.K.O/releases/download/test-v1",
        "package": package,
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"version": 1, "models": [row]}))
    calls = []

    def open_url(request, **kwargs):
        name = request.full_url.rsplit("/", 1)[-1]
        calls.append(name)
        return io.BytesIO((packs / name).read_bytes())

    return (
        downloads.PNGTuberDownloads(catalog, tmp_path / "installed", open_url=open_url),
        row,
        calls,
        open_url,
    )


def wait(manager):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        status = manager.status("test")
        if status["state"] not in downloads._ACTIVE:
            return status
        time.sleep(0.005)
    raise AssertionError("download worker did not settle")


def test_download_installs_exact_bytes_and_removes_download_cache(tmp_path):
    manager, row, calls, _ = make_manager(tmp_path)
    assert manager.start("test")
    status = wait(manager)
    assert status["state"] == "complete", status
    assert status["installed"] and not status["update_available"]
    assert (
        manager.output_root / manager.installation_folder(row) / "layers/layer.png"
    ).read_bytes() == b"png"
    assert calls == [p["archive"] for p in row["package"]["archives"]]
    assert not list(tmp_path.glob(".pngtuber-download-*"))
    assert manager.start("test")
    assert wait(manager)["state"] == "complete"


@pytest.mark.parametrize("failure", ["checksum", "oversize", "truncated", "network"])
def test_failure_keeps_old_install_and_retry_succeeds(tmp_path, failure):
    manager, row, _, healthy = make_manager(tmp_path)
    target = manager.output_root / manager.installation_folder(row)
    target.mkdir(parents=True)
    (target / "old.txt").write_text("old")

    def broken(request, **kwargs):
        data = healthy(request, **kwargs).read()
        if failure == "network":
            raise OSError("offline")
        return io.BytesIO(
            data + b"extra"
            if failure == "oversize"
            else data[:-1]
            if failure == "truncated"
            else b"x" * len(data)
        )

    manager._open_url = broken
    manager.start("test")
    assert wait(manager)["state"] == "failed"
    assert (target / "old.txt").read_text() == "old"
    assert not list(tmp_path.glob(".pngtuber-download-*"))
    manager._open_url = healthy
    manager.start("test")
    assert wait(manager)["state"] == "complete"


def test_cancel_settles_without_install_and_deduplicates_start(tmp_path):
    manager, _, calls, healthy = make_manager(tmp_path)
    started, release = threading.Event(), threading.Event()

    def blocked(request, **kwargs):
        started.set()
        assert release.wait(3)
        return healthy(request, **kwargs)

    manager._open_url = blocked
    manager.start("test")
    assert started.wait(3)
    assert manager.start("test") is False
    assert manager.cancel("test") is True
    release.set()
    assert wait(manager)["state"] == "cancelled"
    assert not list(manager.output_root.iterdir())
    assert not list(tmp_path.glob(".pngtuber-download-*"))


def test_install_stage_cannot_be_cancelled(tmp_path, monkeypatch):
    manager, _, _, _ = make_manager(tmp_path)
    actual = downloads.unpack_model

    def install(*args):
        assert manager.status("test")["state"] == "installing"
        assert manager.cancel("test") is False
        return actual(*args)

    monkeypatch.setattr(downloads, "unpack_model", install)
    manager.start("test")
    assert wait(manager)["state"] == "complete"


def test_only_published_catalog_ids_and_pinned_release_urls_are_accepted(tmp_path):
    manager, row, calls, _ = make_manager(tmp_path, published=False)
    with pytest.raises(ValueError, match="published"):
        manager.start("test")
    with pytest.raises(ValueError, match="Unknown"):
        manager.start("https://evil.test/file.zip")
    for url in [
        "http://github.com/u/r/releases/download/v1",
        "https://evil.test/u/r/releases/download/v1",
        "https://github.com/u/r/releases/download/latest",
    ]:
        with pytest.raises(ValueError, match="pinned"):
            manager._release_url({**row, "release_url": url})
    assert calls == []


def test_untrusted_redirect_is_rejected():
    handler = downloads._SafeRedirect()
    for url in ["http://github.com/file", "https://evil.test/file"]:
        with pytest.raises(ValueError, match="Untrusted"):
            handler.redirect_request(None, None, 302, "redirect", {}, url)


@pytest.mark.asyncio
async def test_optional_endpoints_start_poll_cancel_and_reject_unknown(
    monkeypatch, tmp_path
):
    from main_routers import pngtuber_router as router

    manager, _, _, _ = make_manager(tmp_path, published=False)
    monkeypatch.setattr(router, "_optional_manager", lambda: manager)
    monkeypatch.setattr(
        router, "_validate_local_mutation_request", lambda *args, **kwargs: None
    )
    listed = await router.optional_pngtuber_models()
    assert listed["models"][0]["id"] == "test"
    assert (await router.download_optional_pngtuber("test", None)).status_code == 400
    assert (await router.optional_pngtuber_status("unknown")).status_code == 400
    assert (await router.cancel_optional_pngtuber("test", None)).status_code == 409


@pytest.mark.asyncio
async def test_downloaded_optional_model_takes_precedence_over_old_local_builtin(
    monkeypatch, tmp_path
):
    from types import SimpleNamespace
    from main_routers import pngtuber_router as router

    manager, row, _, _ = make_manager(tmp_path)
    folder = row["package"]["folder"]
    root = tmp_path / "project"
    data = {
        "model_type": "pngtuber",
        "name": "Test",
        "pngtuber": {"idle_image": "idle.png"},
    }
    for directory in [
        root / "static/pngtuber" / folder,
        manager.output_root / manager.installation_folder(row),
    ]:
        directory.mkdir(parents=True)
        (directory / "model.json").write_text(json.dumps(data))
    (
        manager.output_root / manager.installation_folder(row) / ".builtin-pack-sha256"
    ).write_text(row["package"]["archive_sha256"])
    monkeypatch.setattr(router, "_optional_manager", lambda: manager)
    monkeypatch.setattr(
        router,
        "get_config_manager",
        lambda: SimpleNamespace(
            project_root=root,
            pngtuber_dir=manager.output_root,
            ensure_pngtuber_directory=lambda: None,
        ),
    )
    response = await router.get_pngtuber_models()
    models = json.loads(response.body)["models"]
    assert len(models) == 1 and models[0]["location"] == "user"
    assert models[0]["pngtuber"]["idle_image"].startswith("/user_pngtuber/")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint", ["download_optional_pngtuber", "cancel_optional_pngtuber"]
)
async def test_optional_mutations_reject_unverified_requests_before_touching_jobs(
    monkeypatch, endpoint
):
    from fastapi.responses import JSONResponse
    from main_routers import pngtuber_router as router

    rejected = JSONResponse(
        status_code=403,
        content={"success": False, "error_code": "csrf_validation_failed"},
    )
    monkeypatch.setattr(
        router, "_validate_local_mutation_request", lambda *args, **kwargs: rejected
    )
    monkeypatch.setattr(
        router,
        "_optional_manager",
        lambda: pytest.fail("Rejected request accessed download jobs"),
    )
    assert await getattr(router, endpoint)("test", None) is rejected


def test_resource_versions_use_distinct_paths_and_old_version_remains_readable(
    tmp_path,
):
    import copy

    manager, row, _, _ = make_manager(tmp_path)
    older = copy.deepcopy(row)
    older["package"]["archive_sha256"] = "a" * 64
    old = manager.output_root / manager.installation_folder(older)
    old.mkdir(parents=True)
    (old / "keep.png").write_bytes(b"active renderer still reads these bytes")
    assert manager.installation_folder(older) != manager.installation_folder(row)
    manager.start("test")
    assert wait(manager)["state"] == "complete"
    assert (old / "keep.png").read_bytes() == b"active renderer still reads these bytes"


@pytest.mark.parametrize("delete_during_read", [False, True])
def test_status_uses_one_marker_read_and_treats_concurrent_deletion_as_uninstalled(
    tmp_path, monkeypatch, delete_during_read
):
    manager, row, _, _ = make_manager(tmp_path)
    marker = (
        manager.output_root / manager.installation_folder(row) / ".builtin-pack-sha256"
    )
    marker.parent.mkdir(parents=True)
    marker.write_text(row["package"]["archive_sha256"])
    manager._jobs["test"] = {"state": "complete"}
    original = Path.read_text
    reads = []

    def read(path, *args, **kwargs):
        if path == marker:
            reads.append(path)
            if delete_during_read:
                marker.unlink()
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    result = manager.status("test")
    assert len(reads) == 1
    assert result["installed"] is (not delete_during_read)
    assert result["update_available"] is False
    assert result["state"] == ("idle" if delete_during_read else "complete")
