"""Host integration contracts of the built-in Theme Studio.

Covers the dispatch allow-list, the loader's palette document, browser-side
exports, the loopback guard split and schedule thread restart.
"""

import base64
import io
import zipfile

import pytest

import theme_studio
from theme_studio import service as ts_service


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_studio, "_service", None)
    monkeypatch.setattr(theme_studio, "_service_started", False)
    monkeypatch.setattr(theme_studio, "_data_dir_override", tmp_path / "data")
    service = theme_studio.startup_service()
    yield service
    theme_studio.shutdown_service()


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry_id",
    ["shutdown", "startup", "startup_sync", "enable_file_logging", "studio_context", "_read_unlocked"],
)
def test_run_entry_refuses_unregistered_methods(studio, entry_id):
    result = theme_studio.run_entry_sync(entry_id, {})
    assert not result.ok
    assert "未知的主题工坊操作" in str(result.error)
    # The schedule thread must still be alive: ``shutdown`` was not reachable.
    assert studio._schedule_thread is not None and studio._schedule_thread.is_alive()


@pytest.mark.unit
def test_apply_saved_theme_entry_accepts_panel_id_argument(studio):
    result = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk"})
    assert result.ok, result.error
    assert result.value["applied"] is True


@pytest.mark.unit
def test_theme_document_carries_compiled_material_vars(studio):
    result = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk-material"})
    assert result.ok, result.error
    assert result.value["material_enabled"] is True

    document = theme_studio.get_theme_document()
    assert document["material_vars"]["light"], "loader would never see materials"
    assert document["material_attrs"]


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["zip", "json", "library"])
def test_export_to_file_returns_bytes_without_writing_user_dirs(studio, kind):
    # The default palette is empty; start from a built-in theme so there is something to export.
    seeded = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk"})
    assert seeded.ok, seeded.error
    if kind == "library":
        saved = theme_studio.run_entry_sync("save_theme", {"name": "樱花雾"})
        assert saved.ok, saved.error
    else:
        renamed = theme_studio.run_entry_sync("apply_theme", {"name": "樱花雾"})
        assert renamed.ok, renamed.error

    result = theme_studio.run_entry_sync("export_to_file", {"kind": kind, "target": "desktop"})
    assert result.ok, result.error
    payload = base64.b64decode(result.value["data"])
    assert len(payload) == result.value["bytes"]
    assert result.value["filename"].endswith(".json" if kind == "json" else ".zip")
    assert "path" not in result.value
    if kind != "json":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            assert archive.namelist()
    assert not (studio.data_dir / "exports").exists()


@pytest.mark.unit
def test_read_only_endpoints_are_open_but_run_stays_loopback_only(studio):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from main_routers.theme_studio_router import router

    app = FastAPI()
    app.include_router(router)
    # TestClient's peer is "testclient", i.e. not a direct loopback request.
    with TestClient(app) as client:
        palette = client.get("/api/theme_studio/theme.json")
        assert palette.status_code == 200
        assert palette.headers["cache-control"].startswith("no-store")
        assert "material_vars" in palette.json()
        assert client.get("/api/theme_studio/presets/amber-dusk.json").status_code == 200
        assert client.post("/api/theme_studio/run/get_theme", json={}).status_code == 403
        assert client.get("/api/theme_studio/exports/x.zip").status_code == 404


@pytest.mark.unit
def test_schedule_thread_restarts_after_shutdown(studio):
    first = studio._schedule_thread
    assert first is not None and first.is_alive()

    theme_studio.shutdown_service()
    first.join(timeout=5)
    assert not first.is_alive()

    restarted = theme_studio.startup_service()
    assert restarted is studio
    assert studio._schedule_thread is not first
    assert studio._schedule_thread.is_alive()


@pytest.mark.unit
def test_startup_survives_unwritable_theme_file(tmp_path, monkeypatch):
    studio = ts_service.ThemeStudioService(data_dir=tmp_path / "data")

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(studio, "_ensure_file", refuse)
    try:
        assert studio.startup_sync()["status"] == "ready"
    finally:
        studio.shutdown()
