# -*- coding: utf-8 -*-
"""Security tests for Steam Workshop preview upload, status check, and publish (F0224, F0024, F0225)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image


pytestmark = pytest.mark.unit


def _png_bytes() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(10, 20, 30)).save(buf, format="PNG")
    return buf.getvalue()


class _StubUpload:
    def __init__(self, data: bytes, content_type: str = "image/png"):
        self._data = data
        self.content_type = content_type
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk = self._data[self._offset:]
            self._offset = len(self._data)
            return chunk
        chunk = self._data[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk


class _StubFormRequest:
    def __init__(self, file_obj, content_folder: str | None = None):
        self._file = file_obj
        self._content_folder = content_folder

    async def form(self) -> dict[str, object]:
        fields: dict[str, object] = {"file": self._file}
        if self._content_folder is not None:
            fields["content_folder"] = self._content_folder
        return fields


class _StubJsonRequest:
    def __init__(self, payload: dict[str, object]):
        self._payload = payload

    async def json(self) -> dict[str, object]:
        return self._payload


@pytest.mark.asyncio
async def test_upload_preview_image_blocks_outside_and_sibling_and_symlink_folders(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    workshop.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sibling = tmp_path / "workshop-evil"
    sibling.mkdir()
    symlink_dir = workshop / "WorkshopExport" / "sym_item"
    symlink_dir.parent.mkdir(parents=True)
    symlink_created = False
    try:
        symlink_dir.symlink_to(outside, target_is_directory=True)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))

    bad_folders = [str(outside), str(workshop / ".." / "outside"), str(sibling), str(workshop)]
    if symlink_created:
        bad_folders.append(str(symlink_dir))
    for bad_folder in bad_folders:
        resp = await preview_cards.upload_preview_image(
            _StubFormRequest(_StubUpload(_png_bytes()), content_folder=bad_folder)
        )
        assert resp.status_code == 403
        assert json.loads(resp.body)["success"] is False

    assert not (outside / "preview.png").exists()
    assert not (sibling / "preview.png").exists()


@pytest.mark.asyncio
async def test_upload_preview_image_rejects_corrupt_bytes_and_stages_inside_workshop(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    export_item = workshop / "WorkshopExport" / "item_1"
    export_item.mkdir(parents=True)
    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))

    bad_resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(b"not-an-image"), content_folder=str(export_item))
    )
    assert bad_resp.status_code == 400
    assert not (export_item / "preview.png").exists()

    no_folder_resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(_png_bytes()), content_folder=None)
    )
    assert no_folder_resp.status_code == 200
    staged_path = Path(json.loads(no_folder_resp.body)["file_path"])
    assert staged_path.is_file()
    assert staged_path.is_relative_to((workshop / "WorkshopExport" / "_preview_staging").resolve())
    assert staged_path.parent != Path(tempfile.gettempdir()).resolve()


def test_check_upload_status_blocks_traversal_and_sibling_and_symlink(tmp_path, monkeypatch):
    from main_routers import workshop_router
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    item_dir = workshop / "WorkshopExport" / "item_1"
    item_dir.mkdir(parents=True)
    (item_dir / "steam_workshop_id_98765.txt").write_text("98765", encoding="utf-8")

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "steam_workshop_id_111.txt").write_text("111", encoding="utf-8")
    sibling = tmp_path / "workshop-evil"
    sibling.mkdir()
    (sibling / "steam_workshop_id_222.txt").write_text("222", encoding="utf-8")
    sym = workshop / "sym_out"
    symlink_created = False
    try:
        sym.symlink_to(outside, target_is_directory=True)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))

    app = FastAPI()
    app.include_router(workshop_router.router)
    with TestClient(app) as client:
        bad_paths = ["../../outside", str(sibling)]
        if symlink_created:
            bad_paths.append(str(sym))
        for bad in bad_paths:
            r = client.get("/api/steam/workshop/check-upload-status", params={"item_path": bad})
            assert r.status_code == 403
            assert r.json()["success"] is False

        ok = client.get("/api/steam/workshop/check-upload-status", params={"item_path": "WorkshopExport/item_1"})
        assert ok.status_code == 200
        assert ok.json()["published_file_id"] == "98765"


@pytest.mark.asyncio
async def test_publish_rejects_outside_preview_image_and_non_images(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_1"
    staging_folder = workshop / "WorkshopExport" / "_preview_staging" / "abc"
    outside = tmp_path / "outside"
    for d in (content_folder, staging_folder, outside):
        d.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")

    secret_png = outside / "secret.png"
    secret_png.write_bytes(_png_bytes())
    symlink_png = workshop / "WorkshopExport" / "escape.png"
    symlink_created = False
    try:
        symlink_png.symlink_to(secret_png)
        symlink_created = True
    except (OSError, NotImplementedError):
        pass
    bad_ext = workshop / "WorkshopExport" / "secret.txt"
    bad_ext.write_text("secret", encoding="utf-8")
    corrupt_png = workshop / "WorkshopExport" / "corrupt.png"
    corrupt_png.write_bytes(b"corrupt")
    valid_staged = staging_folder / "preview.png"
    valid_staged.write_bytes(_png_bytes())

    steamworks = SimpleNamespace(
        UGC=SimpleNamespace(
            CreateItem=lambda *_a, **_kw: None,
            StartItemUpdate=lambda *_a, **_kw: None,
            SetItemContent=lambda *_a, **_kw: None,
            SetItemPreview=lambda *_a, **_kw: None,
        )
    )
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: True)

    bad_previews = [str(secret_png), f"{workshop}/../outside/secret.png"]
    if symlink_created:
        bad_previews.append(str(symlink_png))
    for bad_preview in bad_previews:
        resp = await wr_publish.publish_to_workshop(
            _StubJsonRequest({
                "title": "Test",
                "content_folder": str(content_folder),
                "visibility": 0,
                "preview_image": bad_preview,
            })
        )
        assert resp.status_code == 403
        assert not (content_folder / "preview.png").exists()

    for bad_img in (str(bad_ext), str(corrupt_png)):
        resp = await wr_publish.publish_to_workshop(
            _StubJsonRequest({
                "title": "Test",
                "content_folder": str(content_folder),
                "visibility": 0,
                "preview_image": bad_img,
            })
        )
        assert resp.status_code == 400
        assert not (content_folder / "preview.png").exists()

    # Staged preview must not be prematurely removed if Steam publish fails (e.g. 503).
    resp_fail = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Test",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(valid_staged),
        })
    )
    assert resp_fail.status_code == 503
    assert (content_folder / "preview.png").is_file()
    assert staging_folder.exists()

    # Once Steam publish succeeds, consumed staging directory is cleaned up.
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)
    monkeypatch.setattr(wr_publish, "_preflight_and_publish", lambda *_a, **_kw: "123456")
    resp_ok = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Test",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(valid_staged),
        })
    )
    assert resp_ok.status_code == 200
    assert (content_folder / "preview.png").is_file()
    assert not staging_folder.exists()


@pytest.mark.asyncio
async def test_publish_returns_403_on_dangling_preview_symlink(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_sym"
    staging_folder = workshop / "WorkshopExport" / "_preview_staging" / "staged"
    outside = tmp_path / "outside"
    for d in (content_folder, staging_folder, outside):
        d.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    (content_folder / "thumbnail.png").write_bytes(_png_bytes())
    valid_staged = staging_folder / "staged.png"
    valid_staged.write_bytes(_png_bytes())

    overwrite_target = outside / "overwrite_target.png"
    try:
        (content_folder / "preview.png").symlink_to(overwrite_target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    steamworks = SimpleNamespace(
        UGC=SimpleNamespace(
            CreateItem=lambda *_a, **_kw: None,
            StartItemUpdate=lambda *_a, **_kw: None,
            SetItemContent=lambda *_a, **_kw: None,
            SetItemPreview=lambda *_a, **_kw: None,
        )
    )
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: True)

    # Case A: auto-discovery finds thumbnail.png, tries to normalize to preview.png (dangling symlink -> outside)
    resp_auto = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Test",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": "",
        })
    )
    assert resp_auto.status_code == 403
    assert not overwrite_target.exists()

    # Case B: explicit valid staged preview tries to copy to preview.png (dangling symlink -> outside)
    resp_explicit = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Test",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(valid_staged),
        })
    )
    assert resp_explicit.status_code == 403
    assert not overwrite_target.exists()


@pytest.mark.asyncio
async def test_upload_preview_image_streams_bounded_chunks_and_stops_at_cap(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    export_item = workshop / "WorkshopExport" / "item_cap"
    export_item.mkdir(parents=True)
    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(preview_cards, "MAX_PREVIEW_UPLOAD_BYTES", 1024)

    class _SpyOversizedUpload:
        content_type = "image/png"

        def __init__(self, total_bytes: int):
            self._total = total_bytes
            self._offset = 0
            self.read_sizes: list[int] = []
            self.total_read = 0
            self.closed = False

        async def read(self, size: int = -1) -> bytes:
            self.read_sizes.append(size)
            if size is None or size < 0:
                to_send = self._total - self._offset
            else:
                to_send = min(size, self._total - self._offset)
            self._offset += to_send
            self.total_read += to_send
            return b"x" * to_send

        async def close(self) -> None:
            self.closed = True

    upload = _SpyOversizedUpload(100 * 1024)
    resp = await preview_cards.upload_preview_image(
        _StubFormRequest(upload, content_folder=str(export_item))
    )
    assert resp.status_code == 400
    assert upload.closed is True
    assert upload.total_read <= 1024 + 64 * 1024
    assert upload.total_read < 100 * 1024


@pytest.mark.asyncio
async def test_prepare_upload_rejects_dot_live2d_model_name(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish
    from utils import frontend_utils

    workshop = tmp_path / "workshop"
    user_live2d = tmp_path / "user_live2d"
    legit_model = user_live2d / "LegitModel"
    workshop.mkdir()
    legit_model.mkdir(parents=True)
    (legit_model / "LegitModel.model3.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(
        wr_publish,
        "get_config_manager",
        lambda: SimpleNamespace(
            live2d_dir=user_live2d,
            readable_live2d_dir=user_live2d,
            project_root=tmp_path,
            card_faces_dir=tmp_path / "faces",
        ),
    )
    monkeypatch.setattr(frontend_utils, "find_model_directory", lambda name: (str(user_live2d / name), "/user_live2d"))

    resp = await wr_publish.prepare_workshop_upload(
        _StubJsonRequest({
            "charaData": {"name": "Neko"},
            "modelName": ".",
            "modelType": "live2d",
            "fileName": "neko.chara.json",
        })
    )
    assert resp.status_code == 400
    assert json.loads(resp.body)["success"] is False
    export_dir = workshop / "WorkshopExport"
    if export_dir.exists():
        assert list(export_dir.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_preview_image_rejects_symlinked_staging_dir(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    export_dir = workshop / "WorkshopExport"
    outside = tmp_path / "outside"
    export_dir.mkdir(parents=True)
    outside.mkdir(parents=True)
    try:
        (export_dir / "_preview_staging").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(_png_bytes()), content_folder=None)
    )
    assert resp.status_code == 403
    assert list(outside.iterdir()) == []


@pytest.mark.asyncio
async def test_upload_preview_image_prunes_stale_staging_dirs(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    staging_root = workshop / "WorkshopExport" / "_preview_staging"
    stale_dir = staging_root / "stale-id"
    fresh_dir = staging_root / "fresh-id"
    stale_dir.mkdir(parents=True)
    fresh_dir.mkdir(parents=True)
    (stale_dir / "preview.png").write_bytes(_png_bytes())
    (fresh_dir / "preview.png").write_bytes(_png_bytes())
    old_ts = time.time() - (preview_cards._PREVIEW_STAGING_TTL_SECONDS + 3600)
    os.utime(stale_dir, (old_ts, old_ts))

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(_png_bytes()), content_folder=None)
    )
    assert resp.status_code == 200
    assert not stale_dir.exists()
    assert fresh_dir.is_dir()
    assert Path(json.loads(resp.body)["file_path"]).is_file()


@pytest.mark.asyncio
async def test_publish_rejects_posix_backslash_traversal_content_folder(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX-only backslash normalization bypass probe")
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    workshop.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Test",
            "content_folder": f"{workshop}/..\\outside",
            "visibility": 0,
        })
    )
    assert resp.status_code == 403
    assert json.loads(resp.body)["success"] is False


def test_preview_validation_does_not_mutate_pil_max_image_pixels(tmp_path):
    from main_routers.workshop_router import preview_cards

    before = Image.MAX_IMAGE_PIXELS
    png_file = tmp_path / "preview.png"
    png_file.write_bytes(_png_bytes())

    preview_cards._validate_preview_image_bytes(_png_bytes())
    preview_cards._verify_preview_image_file(str(png_file))
    assert Image.MAX_IMAGE_PIXELS == before

    with pytest.raises(ValueError):
        preview_cards._check_preview_image_dimensions(SimpleNamespace(size=(20_000, 20_000)))


@pytest.mark.asyncio
async def test_publish_returns_400_when_explicit_staged_preview_missing(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_expired"
    content_folder.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    (content_folder / "thumbnail.png").write_bytes(_png_bytes())
    missing_staged = workshop / "WorkshopExport" / "_preview_staging" / "expired-id" / "preview.png"

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())

    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Test",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(missing_staged),
        })
    )
    assert resp.status_code == 400
    assert json.loads(resp.body)["success"] is False


def test_assert_workshop_path_allows_unc_when_resolved_base_is_unc(monkeypatch):
    import ntpath

    from main_routers.workshop_router import preview_cards

    mapped_base = r"Z:\workshop"
    unc_base = r"\\fileserver\share\workshop"
    unc_file = r"\\fileserver\share\workshop\WorkshopExport\_preview_staging\abc\preview.png"
    foreign_unc = r"\\attacker\share\evil.png"
    nt_device = r"\??\C:\Windows\win.ini"

    monkeypatch.setattr(preview_cards.os.path, "normpath", lambda p: p.replace("/", "\\"))
    monkeypatch.setattr(
        preview_cards.os.path,
        "realpath",
        lambda p: unc_base if p == mapped_base else p,
    )
    monkeypatch.setattr(preview_cards.os.path, "normcase", lambda p: p.lower())
    monkeypatch.setattr(preview_cards.os.path, "commonpath", ntpath.commonpath)

    assert preview_cards._assert_workshop_path(unc_file, mapped_base) == unc_file
    with pytest.raises(PermissionError):
        preview_cards._assert_workshop_path(foreign_unc, mapped_base)
    with pytest.raises(PermissionError):
        preview_cards._assert_workshop_path(nt_device, mapped_base)
