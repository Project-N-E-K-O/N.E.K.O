"""Real ASGI resource access: unfinished installs, pinned handles and range behavior."""

import os
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from app.model_workshop_static import (
    ReadOnlyWorkshopStaticFiles,
    _PinnedModelFileResponse,
)
from services.model_workshop.paths import WorkshopError
from services.model_workshop import runtime
from tests.unit.test_model_workshop_install import (
    bundle_path,
    config as config,
    service as service,
    install,
    fail_registration,
    journal,
)


def test_cloud_data_reparse_tags_do_not_break_ordinary_static_mounts(config, monkeypatch):
    from types import SimpleNamespace

    config.vrm_dir.mkdir()
    (config.vrm_dir / "ordinary.vrm").write_bytes(b"cloud model")
    original = Path.lstat

    def cloud_stat(path, *args, **kwargs):
        info = original(path, *args, **kwargs)
        values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
        values.update(st_file_attributes=0x400, st_reparse_tag=0x9000001A)
        return SimpleNamespace(**values)

    # Cover both eager mount capture and actual descriptor checks. This is a
    # simulated stat tag; real OneDrive hydration remains a Windows acceptance.
    monkeypatch.setattr(Path, "lstat", cloud_stat)
    app = Starlette(routes=[Mount("/", ReadOnlyWorkshopStaticFiles(
        directory=config.vrm_dir, config_manager=config
    ))])
    with TestClient(app) as client:
        response = client.get("/ordinary.vrm")
        assert response.status_code == 200 and response.content == b"cloud model"


@pytest.mark.parametrize("kind", ["live2d", "vrm", "pmx"])
def test_monitor_denies_pending_model_resources_until_main_recovers(
    service, tmp_path, monkeypatch, kind
):
    path, _ = bundle_path(tmp_path, kind)
    save = fail_registration(service, monkeypatch)
    failed = install(service, path)
    row = journal(service, failed["task_id"])["record"]
    directory = getattr(service.paths, row["model_format"])
    local = Path(row["local_entrypoint"]).relative_to(row["model_format"]).as_posix()
    app = Starlette(
        routes=[
            Mount(
                "/",
                ReadOnlyWorkshopStaticFiles(
                    directory=directory, config_manager=service.config
                ),
            )
        ]
    )
    with TestClient(app) as client:
        response = client.get("/" + local)
        assert (
            response.status_code == 503
            and response.headers["cache-control"] == "no-store"
        )
        # Monitor neither recovers the journal nor claims Main's OS writer lock.
        assert service.installer.journal_path(failed["task_id"]).exists()
        monkeypatch.setattr(service.installer.registry, "save", save)
        assert service.recover()[failed["task_id"]] == "completed"
        assert client.get("/" + local).status_code == 200


def test_ordinary_models_preserve_get_head_etag_and_ranges(config):
    config.vrm_dir.mkdir()
    path = config.vrm_dir / "ordinary.vrm"
    path.write_bytes(b"0123456789")
    app = Starlette(
        routes=[
            Mount(
                "/",
                ReadOnlyWorkshopStaticFiles(
                    directory=config.vrm_dir, config_manager=config
                ),
            )
        ]
    )
    with TestClient(app) as client:
        response = client.get("/ordinary.vrm")
        assert (
            response.content == b"0123456789"
            and response.headers["content-length"] == "10"
        )
        assert client.head("/ordinary.vrm").content == b""
        assert (
            client.get(
                "/ordinary.vrm", headers={"If-None-Match": response.headers["etag"]}
            ).status_code
            == 304
        )
        assert (
            client.get("/ordinary.vrm", headers={"Range": "bytes=2-4"}).content
            == b"234"
        )
        multi = client.get("/ordinary.vrm", headers={"Range": "bytes=0-1,8-9"})
        assert (
            multi.status_code == 206
            and b"01" in multi.content
            and b"89" in multi.content
        )
        assert (
            client.get("/ordinary.vrm", headers={"Range": "bytes=99-100"}).status_code
            == 416
        )
    assert not (config.app_docs_dir / "model_workshop").exists()


def test_descriptor_is_pinned_before_the_response_start_even_if_path_is_replaced(
    config, monkeypatch
):
    config.vrm_dir.mkdir()
    path = config.vrm_dir / "ordinary.vrm"
    path.write_bytes(b"old complete model")
    original = _PinnedModelFileResponse._open_checked_model

    def replace_after_open(self):
        result = original(self)
        try:
            path.rename(path.with_suffix(".retained"))
        except PermissionError:
            # Windows may deny pathname replacement while the descriptor is
            # open; that OS guarantee also preserves the checked response.
            if os.name != "nt":
                raise
        else:
            path.write_bytes(b"new pathname content")
        return result

    monkeypatch.setattr(
        _PinnedModelFileResponse, "_open_checked_model", replace_after_open
    )
    app = Starlette(
        routes=[
            Mount(
                "/",
                ReadOnlyWorkshopStaticFiles(
                    directory=config.vrm_dir, config_manager=config
                ),
            )
        ]
    )
    with TestClient(app) as client:
        response = client.get("/ordinary.vrm")
        assert response.content == b"old complete model"
        assert response.headers["content-length"] == str(len(response.content))


def test_case_alias_to_pending_resource_cannot_bypass_the_snapshot(
    service, tmp_path, monkeypatch
):
    path, _ = bundle_path(tmp_path, "vrm")
    fail_registration(service, monkeypatch)
    failed = install(service, path)
    row = journal(service, failed["task_id"])["record"]
    target = service.paths.root / row["target"]
    alias = target.with_name(target.name.upper())
    if not alias.exists():
        pytest.skip("case-sensitive filesystem has no case alias")
    with pytest.raises(WorkshopError, match="unavailable"):
        runtime.snapshot(service.config).require(alias)


def test_cancel_while_opening_waits_and_closes_the_returned_descriptor(
    config, monkeypatch
):
    import asyncio
    import threading
    from services.model_workshop.paths import WorkshopPaths

    config.vrm_dir.mkdir()
    path = config.vrm_dir / "ordinary.vrm"
    path.write_bytes(b"model")
    entered, release = threading.Event(), threading.Event()
    opened = []
    original = _PinnedModelFileResponse._open_checked_model

    def hold(self):
        result = original(self)
        opened.append(self._source if self._source is not None else result[0])
        entered.set()
        assert release.wait(5)
        return result

    monkeypatch.setattr(_PinnedModelFileResponse, "_open_checked_model", hold)
    response = _PinnedModelFileResponse(
        path, workshop_paths=WorkshopPaths.capture(config), conditional=lambda *_: False
    )

    async def run():
        async def send(_message):
            pass

        async def receive():
            return {"type": "http.disconnect"}

        task = asyncio.create_task(
            response({"type": "http", "method": "GET", "headers": []}, receive, send)
        )
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    try:
        asyncio.run(run())
        assert all(handle.closed for handle in opened)
    finally:
        release.set()
        for handle in opened:
            handle.close()
