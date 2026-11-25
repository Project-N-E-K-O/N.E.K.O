# -*- coding: utf-8 -*-
"""Security tests for Steam Workshop preview upload, status check, and publish (F0224, F0024, F0225)."""

from __future__ import annotations

import io
import json
import os
import shutil
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
    from main_routers.workshop_router import preview_cards, publish as wr_publish

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
    (staging_folder / preview_cards._PREVIEW_STAGING_MARKER_FILE).write_text("preview.png", encoding="utf-8")

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
    assert json.loads(resp.body)["error"] == "文件大小无效"
    assert upload.closed is True
    assert upload.total_read == 1025


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
    unowned_old_dir = staging_root / "item_in_staging"
    extra_file_old_dir = staging_root / "stale-with-card"
    fresh_dir = staging_root / "fresh-id"
    for d in (stale_dir, unowned_old_dir, extra_file_old_dir, fresh_dir):
        d.mkdir(parents=True)
    (stale_dir / "preview.png").write_bytes(_png_bytes())
    (stale_dir / preview_cards._PREVIEW_STAGING_MARKER_FILE).write_text("preview.png", encoding="utf-8")
    (unowned_old_dir / "card.json").write_text("{}", encoding="utf-8")
    (unowned_old_dir / "preview.png").write_bytes(_png_bytes())
    (extra_file_old_dir / "preview.png").write_bytes(_png_bytes())
    (extra_file_old_dir / preview_cards._PREVIEW_STAGING_MARKER_FILE).write_text("preview.png", encoding="utf-8")
    (extra_file_old_dir / "card.json").write_text("{}", encoding="utf-8")
    (fresh_dir / "preview.png").write_bytes(_png_bytes())
    (fresh_dir / preview_cards._PREVIEW_STAGING_MARKER_FILE).write_text("preview.png", encoding="utf-8")
    old_ts = time.time() - (preview_cards._PREVIEW_STAGING_TTL_SECONDS + 3600)
    for d in (stale_dir, unowned_old_dir, extra_file_old_dir):
        os.utime(d, (old_ts, old_ts))

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(_png_bytes()), content_folder=None)
    )
    assert resp.status_code == 200
    assert not stale_dir.exists()
    assert unowned_old_dir.is_dir() and (unowned_old_dir / "card.json").is_file()
    assert extra_file_old_dir.is_dir() and (extra_file_old_dir / "card.json").is_file()
    assert fresh_dir.is_dir()
    uploaded = Path(json.loads(resp.body)["file_path"])
    assert uploaded.is_file()
    assert (uploaded.parent / preview_cards._PREVIEW_STAGING_MARKER_FILE).is_file()


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
    extended_device = r"\\?\C:\Windows\win.ini"
    nt_device = r"\??\C:\Windows\win.ini"
    realpath_calls: list[str] = []

    def _spy_realpath(p):
        realpath_calls.append(p)
        return unc_base if p == mapped_base else p

    monkeypatch.setattr(preview_cards.os.path, "normpath", lambda p: p.replace("/", "\\"))
    monkeypatch.setattr(preview_cards.os.path, "realpath", _spy_realpath)
    monkeypatch.setattr(preview_cards.os.path, "normcase", lambda p: p.lower())
    monkeypatch.setattr(preview_cards.os.path, "commonpath", ntpath.commonpath)

    assert preview_cards._assert_workshop_path(unc_file, mapped_base) == unc_file
    realpath_calls.clear()
    with pytest.raises(PermissionError):
        preview_cards._assert_workshop_path(foreign_unc, mapped_base)
    with pytest.raises(PermissionError):
        preview_cards._assert_workshop_path(extended_device, mapped_base)
    with pytest.raises(PermissionError):
        preview_cards._assert_workshop_path(nt_device, mapped_base)
    assert all("attacker" not in c and "Windows" not in c for c in realpath_calls), realpath_calls


def test_verify_preview_image_file_rejects_mismatched_format_bmp_as_png(tmp_path):
    from main_routers.workshop_router import preview_cards

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(1, 2, 3)).save(buf, format="BMP")
    disguised = tmp_path / "preview.png"
    disguised.write_bytes(buf.getvalue())

    with pytest.raises(ValueError):
        preview_cards._verify_preview_image_file(str(disguised))


@pytest.mark.asyncio
async def test_preview_staging_rejects_in_workshop_symlink_redirect_and_protects_exports(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    export_dir = workshop / "WorkshopExport"
    legit_export = export_dir / "legit_item"
    old_child = legit_export / "old_dir"
    export_dir.mkdir(parents=True)
    legit_export.mkdir()
    old_child.mkdir()
    (old_child / "keep.txt").write_text("keep", encoding="utf-8")
    old_ts = time.time() - (preview_cards._PREVIEW_STAGING_TTL_SECONDS + 3600)
    os.utime(old_child, (old_ts, old_ts))

    staging_link = export_dir / "_preview_staging"
    try:
        staging_link.symlink_to(legit_export, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(_png_bytes()), content_folder=None)
    )
    assert resp.status_code == 403

    preview_cards._cleanup_stale_preview_staging(str(staging_link), workshop_base=str(workshop))
    preview_cards._cleanup_consumed_preview_staging(str(old_child / "keep.txt"), str(workshop))
    assert (old_child / "keep.txt").is_file()


@pytest.mark.asyncio
async def test_publish_preserves_literal_percent_in_preview_image_path(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_target"
    percent_dir = workshop / "WorkshopExport" / "item%20v2"
    content_folder.mkdir(parents=True)
    percent_dir.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    percent_preview = percent_dir / "cover%20face.png"
    percent_preview.write_bytes(_png_bytes())

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)

    captured = {}

    def _fake_preflight(_sw, _title, _desc, _cf, preview_arg, *_rest):
        captured["preview_image"] = preview_arg
        return "7890"

    monkeypatch.setattr(wr_publish, "_preflight_and_publish", _fake_preflight)
    publish_resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Percent Item",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(percent_preview),
        })
    )
    assert publish_resp.status_code == 200
    assert (content_folder / "preview.png").is_file()
    assert captured["preview_image"] == str((content_folder / "preview.png").resolve())


@pytest.mark.asyncio
async def test_publish_continues_with_validated_preview_when_copy_fails_on_readonly_folder(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_ro"
    staging_folder = workshop / "WorkshopExport" / "_preview_staging" / "keep_me"
    content_folder.mkdir(parents=True)
    staging_folder.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    staged_preview = staging_folder / "cover.png"
    staged_preview.write_bytes(_png_bytes())

    captured = {}

    def _fake_preflight(_sw, _title, _desc, _cf, preview_arg, *_rest):
        captured["preview_image"] = preview_arg
        return "2468"

    def _fail_copy2(*_args, **_kwargs):
        raise PermissionError("read-only destination")

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)
    monkeypatch.setattr(wr_publish, "_preflight_and_publish", _fake_preflight)
    monkeypatch.setattr(shutil, "copy2", _fail_copy2)

    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Readonly",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(staged_preview),
        })
    )
    assert resp.status_code == 200
    assert captured["preview_image"] == str(staged_preview.resolve())
    assert staging_folder.exists()


@pytest.mark.asyncio
async def test_upload_jpeg_with_png_content_type_saves_jpg_and_publishes(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards, publish as wr_publish

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(120, 40, 80)).save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_jpeg_as_png"
    content_folder.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)

    # 1. Staged upload claiming image/png with JPEG payload must save as preview.jpg and publish cleanly.
    staged_resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(jpeg_bytes, content_type="image/png"), content_folder=None)
    )
    assert staged_resp.status_code == 200
    staged_data = json.loads(staged_resp.body)
    assert staged_data["file_path"].endswith("preview.jpg")
    assert Path(staged_data["file_path"]).is_file()

    captured = {}

    def _fake_preflight(_sw, _title, _desc, _cf, preview_arg, *_rest):
        captured["preview_image"] = preview_arg
        return "9991"

    monkeypatch.setattr(wr_publish, "_preflight_and_publish", _fake_preflight)
    publish_resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Jpeg Uploaded As Png",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": staged_data["file_path"],
        })
    )
    assert publish_resp.status_code == 200
    assert (content_folder / "preview.jpg").is_file()
    assert not (content_folder / "preview.png").exists()
    assert captured["preview_image"] == str((content_folder / "preview.jpg").resolve())

    # 2. Direct upload into content_folder claiming image/png with JPEG payload also writes preview.jpg.
    direct_folder = workshop / "WorkshopExport" / "item_direct"
    direct_folder.mkdir(parents=True)
    direct_resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(jpeg_bytes, content_type="image/png"), content_folder=str(direct_folder))
    )
    assert direct_resp.status_code == 200
    direct_data = json.loads(direct_resp.body)
    assert direct_data["file_path"].endswith("preview.jpg")
    assert (direct_folder / "preview.jpg").is_file()
    assert not (direct_folder / "preview.png").exists()


@pytest.mark.asyncio
async def test_publish_auto_discovers_mismatched_extension_thumbnail_and_copies_canonical_ext(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(15, 90, 200)).save(buf, format="JPEG")
    jpeg_bytes = buf.getvalue()

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_auto_mismatch"
    content_folder.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    (content_folder / "thumbnail.png").write_bytes(jpeg_bytes)

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)

    captured = {}

    def _fake_preflight(_sw, _title, _desc, _cf, preview_arg, *_rest):
        captured["preview_image"] = preview_arg
        return "9992"

    monkeypatch.setattr(wr_publish, "_preflight_and_publish", _fake_preflight)
    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Auto Discovered Mismatched Thumbnail",
            "content_folder": str(content_folder),
            "visibility": 0,
        })
    )
    assert resp.status_code == 200
    assert (content_folder / "preview.jpg").is_file()
    assert captured["preview_image"] == str((content_folder / "preview.jpg").resolve())

    # Unsupported auto-discovered formats (e.g. BMP disguised as thumbnail.png) degrade to publishing without preview.
    bmp_folder = workshop / "WorkshopExport" / "item_auto_bmp"
    bmp_folder.mkdir(parents=True)
    (bmp_folder / "card.json").write_text("{}", encoding="utf-8")
    bmp_buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(1, 2, 3)).save(bmp_buf, format="BMP")
    (bmp_folder / "thumbnail.png").write_bytes(bmp_buf.getvalue())

    captured.clear()
    degraded_resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Auto Discovered BMP",
            "content_folder": str(bmp_folder),
            "visibility": 0,
        })
    )
    assert degraded_resp.status_code == 200
    assert json.loads(degraded_resp.body)["success"] is True
    assert captured["preview_image"] == ""


@pytest.mark.asyncio
async def test_publish_skips_copy_when_canonical_preview_path_is_directory(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_dir_collision"
    (content_folder / "preview.png").mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    staging_folder = workshop / "WorkshopExport" / "_preview_staging" / "stage_keep"
    staging_folder.mkdir(parents=True)
    staged_preview = staging_folder / "cover.png"
    staged_preview.write_bytes(_png_bytes())

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)

    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Dir Target Collision",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(staged_preview),
        })
    )
    assert resp.status_code == 400
    assert json.loads(resp.body)["success"] is False
    assert staging_folder.exists()
    assert not (content_folder / "preview.png" / "cover.png").exists()


@pytest.mark.asyncio
async def test_publish_does_not_delete_content_folder_inside_preview_staging(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards, publish as wr_publish

    workshop = tmp_path / "workshop"
    export_dir = workshop / "WorkshopExport"
    content_inside_staging = export_dir / "_preview_staging" / "item_in_staging"
    content_inside_staging.mkdir(parents=True)
    (content_inside_staging / "card.json").write_text("{}", encoding="utf-8")
    source_cover = content_inside_staging / "cover.png"
    source_cover.write_bytes(_png_bytes())

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)
    monkeypatch.setattr(wr_publish, "_preflight_and_publish", lambda *_a: "9994")

    # 1) Reject publishing when content_folder itself is inside _preview_staging.
    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Content Under Staging",
            "content_folder": str(content_inside_staging),
            "visibility": 0,
            "preview_image": str(source_cover),
        })
    )
    assert resp.status_code == 403
    assert (content_inside_staging / "card.json").is_file()

    # 2) Publishing normal_item using preview from unowned _preview_staging/item_in_staging preserves item_in_staging.
    normal_content = export_dir / "normal_item"
    normal_content.mkdir(parents=True)
    (normal_content / "card.json").write_text("{}", encoding="utf-8")
    resp_unowned = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Normal Content With Unowned Staging Source",
            "content_folder": str(normal_content),
            "visibility": 0,
            "preview_image": str(source_cover),
        })
    )
    assert resp_unowned.status_code == 200
    assert (content_inside_staging / "card.json").is_file()

    # 3) Owned upload-created staging dir (marked + isolated) IS cleaned up after successful publish.
    separate_stage = export_dir / "_preview_staging" / "ephemeral_stage"
    separate_stage.mkdir(parents=True)
    separate_cover = separate_stage / "preview.png"
    separate_cover.write_bytes(_png_bytes())
    (separate_stage / preview_cards._PREVIEW_STAGING_MARKER_FILE).write_text("preview.png", encoding="utf-8")

    resp2 = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Normal Content Separate Staging",
            "content_folder": str(normal_content),
            "visibility": 0,
            "preview_image": str(separate_cover),
        })
    )
    assert resp2.status_code == 200
    assert not separate_stage.exists()


@pytest.mark.asyncio
async def test_publish_explicit_preview_rejects_mismatched_jpeg_named_png(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(15, 90, 200)).save(buf, format="JPEG")

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_explicit_mismatch"
    content_folder.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    mismatched_file = content_folder / "thumbnail.png"
    mismatched_file.write_bytes(buf.getvalue())

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())

    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Explicit Mismatched Preview",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(mismatched_file),
        })
    )
    assert resp.status_code == 400
    assert json.loads(resp.body)["success"] is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink-before-dotdot semantics")
def test_assert_workshop_path_resolves_symlink_before_dotdot(tmp_path):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    item_sub = workshop / "item_a" / "sub"
    item_sub.mkdir(parents=True)
    real_cover = workshop / "item_a" / "cover.png"
    real_cover.write_bytes(_png_bytes())
    root_cover = workshop / "cover.png"
    root_cover.write_bytes(_png_bytes())

    alias = workshop / "alias"
    outside = tmp_path / "outside"
    outside_sub = outside / "sub"
    outside_sub.mkdir(parents=True)
    (outside / "secret.png").write_bytes(_png_bytes())
    outside_alias = workshop / "outside_link"
    try:
        alias.symlink_to(item_sub, target_is_directory=True)
        outside_alias.symlink_to(outside_sub, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    # POSIX symlink resolution precedes "..": alias/../cover.png resolves to item_a/cover.png, not workshop/cover.png.
    assert preview_cards._assert_workshop_path(str(alias / ".." / "cover.png"), str(workshop)) == str(
        real_cover.resolve()
    )
    with pytest.raises(PermissionError):
        preview_cards._assert_workshop_path(str(outside_alias / ".." / "secret.png"), str(workshop))


@pytest.mark.asyncio
async def test_upload_preview_image_closes_form_on_early_return(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards

    workshop = tmp_path / "workshop"
    workshop.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    closed_count = 0

    class _ClosableForm(dict):
        async def close(self):
            nonlocal closed_count
            closed_count += 1

    class _ClosableFormRequest:
        async def form(self):
            return _ClosableForm({
                "file": _StubUpload(_png_bytes()),
                "content_folder": str(outside),
            })

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    resp = await preview_cards.upload_preview_image(_ClosableFormRequest())
    assert resp.status_code == 403
    assert closed_count == 1


@pytest.mark.asyncio
async def test_preview_target_rejects_in_folder_symlink_in_upload_and_publish(tmp_path, monkeypatch):
    from main_routers.workshop_router import preview_cards, publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_sym_internal"
    textures_dir = content_folder / "textures"
    staging_folder = workshop / "WorkshopExport" / "_preview_staging" / "st"
    for d in (textures_dir, staging_folder):
        d.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")
    body_tex = textures_dir / "body.png"
    original_bytes = b"ORIGINAL-BODY-TEXTURE"
    body_tex.write_bytes(original_bytes)
    staged_png = staging_folder / "preview.png"
    staged_png.write_bytes(_png_bytes())

    preview_link = content_folder / "preview.png"
    try:
        preview_link.symlink_to(body_tex)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())

    upload_resp = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(_png_bytes()), content_folder=str(content_folder))
    )
    assert upload_resp.status_code == 403
    assert body_tex.read_bytes() == original_bytes

    pub_resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Internal Symlink Preview Target",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(staged_png),
        })
    )
    assert pub_resp.status_code == 403
    assert body_tex.read_bytes() == original_bytes


@pytest.mark.asyncio
async def test_publish_auto_discovery_existing_target_is_validated_not_skipped(tmp_path, monkeypatch):
    from main_routers.workshop_router import publish as wr_publish

    jpeg_buf = io.BytesIO()
    Image.new("RGB", (4, 4), color=(10, 80, 160)).save(jpeg_buf, format="JPEG")
    jpeg_bytes = jpeg_buf.getvalue()

    workshop = tmp_path / "workshop"
    folder_valid = workshop / "WorkshopExport" / "item_existing_valid_jpg"
    folder_valid.mkdir(parents=True)
    (folder_valid / "card.json").write_text("{}", encoding="utf-8")
    (folder_valid / "thumbnail.png").write_bytes(jpeg_bytes)
    existing_jpg = folder_valid / "preview.jpg"
    existing_jpg.write_bytes(jpeg_bytes)

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)

    captured = {}

    def _fake_preflight(_sw, _title, _desc, _cf, preview_arg, *_rest):
        captured["preview_image"] = preview_arg
        return "9998"

    monkeypatch.setattr(wr_publish, "_preflight_and_publish", _fake_preflight)
    ok_resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Existing Valid Preview",
            "content_folder": str(folder_valid),
            "visibility": 0,
        })
    )
    assert ok_resp.status_code == 200
    assert captured["preview_image"] == str(existing_jpg.resolve())

    # Corrupt auto-discovered preview.jpg degrades with a warning and publishes without preview.
    folder_corrupt = workshop / "WorkshopExport" / "item_existing_corrupt_jpg"
    folder_corrupt.mkdir(parents=True)
    (folder_corrupt / "card.json").write_text("{}", encoding="utf-8")
    (folder_corrupt / "thumbnail.png").write_bytes(jpeg_bytes)
    (folder_corrupt / "preview.jpg").write_bytes(b"corrupt")

    captured.clear()
    degraded_resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Existing Corrupt Preview",
            "content_folder": str(folder_corrupt),
            "visibility": 0,
        })
    )
    assert degraded_resp.status_code == 200
    assert json.loads(degraded_resp.body)["success"] is True
    assert captured["preview_image"] == ""


def test_write_preview_image_writes_marker_first_and_rolls_back_on_failure(tmp_path, monkeypatch):
    """Staging writes marker before image bytes and removes staging_dir on failure."""
    from main_routers.workshop_router import preview_cards

    workshop_base = tmp_path / "workshop"
    staging_dir = workshop_base / "_preview_staging" / "upload-rollback"
    preview_path = staging_dir / "preview.png"
    real_atomic_write_bytes = preview_cards.atomic_write_bytes
    written_basenames: list[str] = []

    def _failing_atomic_write_bytes(target_path: str, data: bytes) -> None:
        basename = os.path.basename(target_path)
        written_basenames.append(basename)
        if basename == preview_cards._PREVIEW_STAGING_MARKER_FILE:
            real_atomic_write_bytes(target_path, data)
            return
        raise OSError("simulated disk full")

    monkeypatch.setattr(preview_cards, "atomic_write_bytes", _failing_atomic_write_bytes)

    with pytest.raises(OSError, match="simulated disk full"):
        preview_cards._write_preview_image(
            None,
            str(preview_path),
            _png_bytes(),
            workshop_base=str(workshop_base),
        )

    assert written_basenames == [preview_cards._PREVIEW_STAGING_MARKER_FILE, "preview.png"]
    assert not staging_dir.exists()


@pytest.mark.asyncio
async def test_publish_rejects_content_folder_enclosing_preview_staging(tmp_path, monkeypatch):
    """Publishing WorkshopExport or workshop root must be rejected with 403 before preflight."""
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    export_dir = workshop / "WorkshopExport"
    staged_file = export_dir / "_preview_staging" / "stage1" / "preview.png"
    staged_file.parent.mkdir(parents=True)
    staged_file.write_bytes(_png_bytes())
    (export_dir / "item_1").mkdir()
    (export_dir / "item_1" / "card.json").write_text("{}", encoding="utf-8")

    published_calls: list[tuple] = []
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)
    monkeypatch.setattr(
        wr_publish,
        "_preflight_and_publish",
        lambda *args: published_calls.append(args) or "9990",
    )

    for forbidden_folder in (str(export_dir), str(workshop)):
        resp = await wr_publish.publish_to_workshop(
            _StubJsonRequest({
                "title": "Enclosing Staging Folder",
                "content_folder": forbidden_folder,
                "visibility": 0,
            })
        )
        assert resp.status_code == 403
        body = json.loads(resp.body)
        assert body["success"] is False
        assert "包含预览暂存目录" in body["message"]
    assert published_calls == []
    assert staged_file.is_file()


@pytest.mark.asyncio
async def test_publish_writes_workshop_meta_before_cleaning_staged_preview(tmp_path, monkeypatch):
    """Published metadata snapshot must be written before cleaning consumed preview staging."""
    from main_routers.workshop_router import preview_cards, publish as wr_publish

    workshop = tmp_path / "workshop"
    content_folder = workshop / "WorkshopExport" / "item_card"
    content_folder.mkdir(parents=True)
    (content_folder / "card.json").write_text("{}", encoding="utf-8")

    staged_dir = workshop / "WorkshopExport" / "_preview_staging" / "st_order"
    staged_dir.mkdir(parents=True)
    staged_preview = staged_dir / "preview.png"
    staged_preview.write_bytes(_png_bytes())
    (staged_dir / preview_cards._PREVIEW_STAGING_MARKER_FILE).write_bytes(b"preview.png")

    events: list[str] = []
    real_cleanup = wr_publish._cleanup_consumed_preview_staging

    def _recording_write_meta(*_args, **_kwargs):
        assert staged_dir.exists()
        events.append("write_workshop_meta")

    def _recording_cleanup(*args, **kwargs):
        events.append("cleanup_staged_preview")
        return real_cleanup(*args, **kwargs)

    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)
    monkeypatch.setattr(wr_publish, "_preflight_and_publish", lambda *_args: "12345")
    monkeypatch.setattr(wr_publish, "calculate_content_hash", lambda _folder: "deadbeef")
    monkeypatch.setattr(wr_publish, "read_workshop_meta", lambda _name: None)
    monkeypatch.setattr(wr_publish, "write_workshop_meta", _recording_write_meta)
    monkeypatch.setattr(wr_publish, "_cleanup_consumed_preview_staging", _recording_cleanup)

    resp = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Meta Before Cleanup",
            "content_folder": str(content_folder),
            "visibility": 0,
            "preview_image": str(staged_preview),
            "character_card_name": "item_card",
        })
    )
    assert resp.status_code == 200
    assert events == ["write_workshop_meta", "cleanup_staged_preview"]
    assert not staged_dir.exists()


@pytest.mark.asyncio
async def test_upload_and_verify_accept_camera_mpo_and_truncated_jpeg_and_hint_select_file(tmp_path, monkeypatch):
    """P6/P7/P8: accept camera MPO and EOI-truncated JPEGs, and hint Select File on 403 outside preview."""
    from main_routers.workshop_router import preview_cards, publish as wr_publish

    # Build real camera-style MPO bytes (JPEG + MPF APP2 marker) verified by Pillow as format == 'MPO'.
    mpo_buf = io.BytesIO()
    Image.new("RGB", (8, 8), color=(30, 120, 210)).save(
        mpo_buf,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", (8, 8), color=(60, 150, 240))],
    )
    mpo_bytes = mpo_buf.getvalue()
    with Image.open(io.BytesIO(mpo_bytes)) as probe_mpo:
        assert probe_mpo.format == "MPO"

    # Build a slightly truncated JPEG missing the trailing EOI marker (0xFF 0xD9) so Image.load() fails.
    jpeg_buf = io.BytesIO()
    Image.new("RGB", (16, 16), color=(200, 100, 50)).save(jpeg_buf, format="JPEG")
    full_jpeg = jpeg_buf.getvalue()
    assert full_jpeg.endswith(b"\xff\xd9")
    truncated_jpeg = full_jpeg[:-2]
    with Image.open(io.BytesIO(truncated_jpeg)) as probe_trunc:
        with pytest.raises(OSError):
            probe_trunc.load()

    assert preview_cards._validate_preview_image_bytes(mpo_bytes) == "JPEG"
    assert preview_cards._validate_preview_image_bytes(truncated_jpeg) == "JPEG"

    workshop = tmp_path / "workshop"
    outside = tmp_path / "outside"
    export_mpo = workshop / "WorkshopExport" / "item_mpo"
    export_trunc = workshop / "WorkshopExport" / "item_trunc"
    for d in (outside, export_mpo, export_trunc):
        d.mkdir(parents=True)
    (export_mpo / "card.json").write_text("{}", encoding="utf-8")
    (export_trunc / "card.json").write_text("{}", encoding="utf-8")

    mpo_file = export_mpo / "camera.jpg"
    mpo_file.write_bytes(mpo_bytes)
    trunc_file = export_trunc / "trunc.jpeg"
    trunc_file.write_bytes(truncated_jpeg)
    assert preview_cards._verify_preview_image_file(str(mpo_file)) == "JPEG"
    assert preview_cards._verify_preview_image_file(str(trunc_file)) == "JPEG"

    monkeypatch.setattr(preview_cards, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())

    staged_mpo = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(mpo_bytes, content_type="image/jpeg"), content_folder=None)
    )
    assert staged_mpo.status_code == 200
    assert json.loads(staged_mpo.body)["file_path"].endswith("preview.jpg")

    staged_trunc = await preview_cards.upload_preview_image(
        _StubFormRequest(_StubUpload(truncated_jpeg, content_type="image/jpeg"), content_folder=None)
    )
    assert staged_trunc.status_code == 200
    assert json.loads(staged_trunc.body)["file_path"].endswith("preview.jpg")

    outside_img = outside / "local.png"
    outside_img.write_bytes(_png_bytes())
    resp_outside = await wr_publish.publish_to_workshop(
        _StubJsonRequest({
            "title": "Outside Preview Hint",
            "content_folder": str(export_mpo),
            "visibility": 0,
            "preview_image": str(outside_img),
        })
    )
    assert resp_outside.status_code == 403
    assert "选择文件" in json.loads(resp_outside.body)["message"]


@pytest.mark.asyncio
async def test_publish_rejects_symlinked_preview_staging_and_its_target(tmp_path, monkeypatch):
    """Reject publishing enclosing folders or target paths when _preview_staging is a symlink."""
    from main_routers.workshop_router import publish as wr_publish

    workshop = tmp_path / "workshop"
    export_dir = workshop / "WorkshopExport"
    ordinary_target = export_dir / "ordinary_staging_target"
    stage_child = ordinary_target / "stage1"
    stage_child.mkdir(parents=True)
    (ordinary_target / "card.json").write_text("{}", encoding="utf-8")
    (stage_child / "card.json").write_text("{}", encoding="utf-8")
    staged_file = stage_child / "preview.png"
    staged_file.write_bytes(_png_bytes())

    staging_link = export_dir / "_preview_staging"
    try:
        staging_link.symlink_to(ordinary_target, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"Symlinks are not supported in this test environment: {exc}")

    published_calls: list[tuple] = []
    monkeypatch.setattr(wr_publish, "get_workshop_path_async", AsyncMock(return_value=str(workshop)))
    monkeypatch.setattr(wr_publish, "get_steamworks", lambda: SimpleNamespace())
    monkeypatch.setattr(wr_publish, "_is_workshop_publish_native_crash_risk", lambda: False)
    monkeypatch.setattr(
        wr_publish,
        "_preflight_and_publish",
        lambda *args: published_calls.append(args) or "9990",
    )

    for forbidden_folder in (
        str(export_dir),
        str(workshop),
        str(staging_link / "stage1"),
        str(ordinary_target),
    ):
        resp = await wr_publish.publish_to_workshop(
            _StubJsonRequest({
                "title": "Symlinked Staging Publish",
                "content_folder": forbidden_folder,
                "visibility": 0,
            })
        )
        assert resp.status_code == 403
        assert json.loads(resp.body)["success"] is False

    assert published_calls == []
    assert staged_file.is_file()

