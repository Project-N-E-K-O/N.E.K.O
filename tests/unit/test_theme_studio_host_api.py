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
@pytest.mark.parametrize(
    ("raw", "suffix"),
    [("a" * 38 + ".json", ".json"), ("樱花雾" * 20 + ".zip", ".zip"), ("plain-name", "")],
)
def test_download_filename_keeps_extension_when_truncating(raw, suffix):
    name = ts_service.ThemeStudioService._download_filename(raw)
    assert name.endswith(suffix)
    assert len(name) <= 40 + len(suffix)


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
def test_run_rejects_cross_site_and_non_json_mutations(studio):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from main_routers.cookies_login_router import verify_local_access
    from main_routers.theme_studio_router import router

    app = FastAPI()
    app.include_router(router)
    # Simulate a loopback peer: the CSRF guard must hold even then.
    app.dependency_overrides[verify_local_access] = lambda: None
    with TestClient(app) as client:
        bodyless = client.post("/api/theme_studio/run/reset_theme")
        assert bodyless.status_code == 415
        cross_site = client.post(
            "/api/theme_studio/run/reset_theme",
            json={},
            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        )
        assert cross_site.status_code == 403
        same_origin = client.post(
            "/api/theme_studio/run/get_theme",
            json={},
            headers={"Sec-Fetch-Site": "same-origin"},
        )
        assert same_origin.status_code == 200
        assert same_origin.json()["ok"] is True


@pytest.mark.unit
@pytest.mark.parametrize("animation", [{"animation": False}, {"reducedMotion": True}])
def test_motion_switches_stop_drift_animation(animation):
    from theme_studio import materials as ts_materials

    raw = ts_materials.default_materials()
    raw["enabled"] = True
    raw.setdefault("animation", {}).update(animation)
    clean, _warnings = ts_materials.normalize_materials(raw)
    light, dark = ts_materials.compile_materials(clean)
    assert light["--nts-mt-anim"] == "none"
    assert dark["--nts-mt-anim"] == "none"


@pytest.mark.unit
def test_reset_theme_keeps_saved_material_presets(studio):
    from theme_studio import materials as ts_materials

    saved = theme_studio.run_entry_sync(
        "save_material_preset",
        {"name": "my-glass", "materials": ts_materials.default_materials()},
    )
    assert saved.ok, saved.error
    before = theme_studio.run_entry_sync("get_theme", {}).value["materials"]["custom_presets"]
    assert before, "precondition: a custom preset was stored"

    reset = theme_studio.run_entry_sync("reset_theme", {})
    assert reset.ok, reset.error
    after = theme_studio.run_entry_sync("get_theme", {}).value["materials"]
    assert set(after["custom_presets"]) == set(before)
    assert after["enabled"] is False


@pytest.mark.unit
def test_import_with_materials_keeps_saved_material_presets(studio):
    import json

    from theme_studio import materials as ts_materials

    saved = theme_studio.run_entry_sync(
        "save_material_preset",
        {"name": "my-glass", "materials": ts_materials.default_materials()},
    )
    assert saved.ok, saved.error
    incoming = ts_materials.default_materials()
    incoming["enabled"] = True
    payload = json.dumps({"vars": {"--nts-accent": "#ff8800"}, "materials": incoming})

    imported = theme_studio.run_entry_sync("import_theme", {"payload": payload})
    assert imported.ok, imported.error
    after = theme_studio.run_entry_sync("get_theme", {}).value["materials"]
    assert "my-glass" in after["custom_presets"]
    assert after["enabled"] is True


@pytest.mark.unit
def test_color_only_library_entry_does_not_reset_current_materials(studio):
    from theme_studio import materials as ts_materials

    current = ts_materials.default_materials()
    current["enabled"] = True
    applied = theme_studio.run_entry_sync("apply_theme", {"materials": current})
    assert applied.ok, applied.error

    record = studio._save_library_item(
        name="colors-only", vars_map={"--nts-accent": "#3366ff"}, dark_map={}, materials=None
    )
    assert "materials" not in record

    switched = theme_studio.run_entry_sync("apply_saved_theme", {"id": record["id"]})
    assert switched.ok, switched.error
    assert switched.value["material_enabled"] is True
    assert theme_studio.run_entry_sync("get_theme", {}).value["materials"]["enabled"] is True


@pytest.mark.unit
def test_daily_schedule_applies_again_on_the_next_day(tmp_path):
    import json
    from datetime import datetime

    studio = ts_service.ThemeStudioService(data_dir=tmp_path / "data")
    studio._ensure_dirs()
    (studio.data_dir / "schedule.json").write_text(
        json.dumps({"schema": 2, "enabled": True, "zones": {"morning": "amber-dusk"}}),
        encoding="utf-8",
    )
    clock = {"now": datetime(2026, 11, 23, 8, 0)}  # Monday morning
    studio._now = lambda: clock["now"]

    assert studio._schedule_tick()["action"] == "applied"
    clock["now"] = datetime(2026, 11, 23, 9, 30)
    assert studio._schedule_tick()["action"] == "unchanged"
    clock["now"] = datetime(2026, 11, 24, 8, 0)  # Tuesday morning
    assert studio._schedule_tick()["action"] == "applied"


@pytest.mark.unit
def test_legacy_schedule_without_date_applies_again_the_next_day(tmp_path):
    import json
    from datetime import datetime

    studio = ts_service.ThemeStudioService(data_dir=tmp_path / "data")
    studio._ensure_dirs()
    # Written by a build before last_date existed: Monday morning already applied.
    (studio.data_dir / "schedule.json").write_text(
        json.dumps({
            "schema": 2,
            "enabled": True,
            "zones": {"morning": "amber-dusk"},
            "last_zone": "morning",
            "last_theme": "amber-dusk",
            "last_day_kind": "weekday",
        }),
        encoding="utf-8",
    )
    clock = {"now": datetime(2026, 11, 23, 8, 0)}
    studio._now = lambda: clock["now"]

    assert studio._schedule_tick()["action"] == "unchanged"
    clock["now"] = datetime(2026, 11, 24, 8, 0)
    assert studio._schedule_tick()["action"] == "applied"


@pytest.mark.unit
def test_reset_theme_refuses_when_theme_file_is_temporarily_unreadable(studio, monkeypatch):
    path_type = type(studio.theme_path)
    real_read_bytes = path_type.read_bytes

    def locked(self):
        if self == studio.theme_path:
            raise PermissionError("in use")
        return real_read_bytes(self)

    monkeypatch.setattr(path_type, "read_bytes", locked)
    result = theme_studio.run_entry_sync("reset_theme", {})
    assert not result.ok
    assert "稍后重试" in str(result.error)


@pytest.mark.unit
def test_night_zone_occurrence_belongs_to_the_day_it_started():
    from datetime import datetime

    monday_late = ts_service._zone_occurrence_date(datetime(2026, 11, 23, 23, 0), "night")
    tuesday_early = ts_service._zone_occurrence_date(datetime(2026, 11, 24, 1, 0), "night")
    assert monday_late == tuesday_early == "2026-11-23"
    assert ts_service._zone_occurrence_date(datetime(2026, 11, 24, 8, 0), "morning") == "2026-11-24"


@pytest.mark.unit
def test_huge_integer_material_value_falls_back_instead_of_raising():
    from theme_studio import materials as ts_materials

    raw = ts_materials.default_materials()
    raw["global"]["opacity"] = 10**400
    clean, warnings = ts_materials.normalize_materials(raw)
    assert clean["global"]["opacity"] == ts_materials.default_materials()["global"]["opacity"]
    assert warnings


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
def test_shutdown_stops_schedule_thread_and_waits_for_running_entry(studio):
    import threading
    import time

    worker = studio._schedule_thread
    assert worker is not None and worker.is_alive()

    # An entry still writing when shutdown starts: shutdown must wait for it.
    theme_studio._ENTRY_LOCK.acquire()
    threading.Timer(0.3, theme_studio._ENTRY_LOCK.release).start()
    started = time.monotonic()
    assert theme_studio.shutdown_service(3.0) is True
    assert time.monotonic() - started >= 0.25
    assert not worker.is_alive()
    assert theme_studio.service_started() is False


@pytest.mark.unit
def test_shutdown_gives_up_after_its_timeout(studio):
    import time

    theme_studio._ENTRY_LOCK.acquire()
    try:
        started = time.monotonic()
        assert theme_studio.shutdown_service(0.2) is False
        assert time.monotonic() - started < 2.0
    finally:
        theme_studio._ENTRY_LOCK.release()


@pytest.mark.unit
def test_main_server_shutdown_hook_calls_theme_studio():
    import inspect

    from app.main_server import on_shutdown

    source = inspect.getsource(on_shutdown)
    assert "shutdown_service_async" in source
    assert 'what="theme studio shutdown"' in source


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
