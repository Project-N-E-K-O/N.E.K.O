"""Host integration contracts of the built-in Theme Studio.

Covers the dispatch allow-list, the loader's palette document, browser-side
exports, the loopback guard split and schedule thread restart.
"""

import base64
import io
import json
import zipfile

import pytest

import theme_studio
from theme_studio import service as ts_service
from theme_studio.i18n import msg, reset_request_locale, set_request_locale


@pytest.fixture
def studio(tmp_path, monkeypatch):
    monkeypatch.setattr(theme_studio, "_service", None)
    monkeypatch.setattr(theme_studio, "_service_started", False)
    monkeypatch.setattr(theme_studio, "_app_exiting", False)
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
    assert str(result.error) == msg("core.unknownEntry", entry_id=entry_id)
    # The schedule thread must still be alive: ``shutdown`` was not reachable.
    assert studio._schedule_thread is not None and studio._schedule_thread.is_alive()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [("en-US", "Unknown Theme Studio action: nope"), ("zh-CN", "未知的主题工坊操作：nope"),
     ("zh-TW", "未知的主題工坊操作：nope")],
)
def test_backend_messages_follow_the_request_language(studio, raw, expected):
    token = set_request_locale(raw)
    try:
        result = theme_studio.run_entry_sync("nope", {})
    finally:
        reset_request_locale(token)
    assert str(result.error) == expected


@pytest.mark.unit
def test_run_route_uses_the_panel_locale_header(studio):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from main_routers.cookies_login_router import verify_local_access
    from main_routers.theme_studio_router import router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[verify_local_access] = lambda: None
    with TestClient(app) as client:
        response = client.post(
            "/api/theme_studio/run/nope",
            json={},
            headers={"Sec-Fetch-Site": "same-origin", "X-Neko-Locale": "ja"},
        )
    assert response.status_code == 200
    assert response.json()["error"] == "不明なテーマスタジオ操作です：nope"


@pytest.mark.unit
def test_message_table_is_complete_in_every_language():
    import re

    from config.prompts.prompts_theme_studio import THEME_STUDIO_MESSAGES

    langs = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    placeholder = re.compile(r"(?<!\{)\{([A-Za-z_][A-Za-z0-9_]*)\}(?!\})")
    for msg_id, entry in THEME_STUDIO_MESSAGES.items():
        assert set(entry) == langs, msg_id
        want = sorted(placeholder.findall(entry["zh"]))
        for lang, text in entry.items():
            assert text, (msg_id, lang)
            assert sorted(placeholder.findall(text)) == want, (msg_id, lang)


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
def test_shared_theme_with_materials_off_turns_recipient_materials_off(studio):
    exported = theme_studio.run_entry_sync("export_theme_archive", {"id": "amber-dusk"})
    assert exported.ok, exported.error
    imported = theme_studio.run_entry_sync(
        "import_theme_archive", {"filename": "amber-dusk.zip", "data": exported.value["data"]}
    )
    assert imported.ok, imported.error
    theme_id = imported.value["theme"]["id"]

    # The recipient currently has materials on.
    on = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk-material"})
    assert on.ok and on.value["material_enabled"] is True

    applied = theme_studio.run_entry_sync("apply_saved_theme", {"id": theme_id})
    assert applied.ok, applied.error
    assert applied.value["material_enabled"] is False


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
    assert str(result.error) == msg("theme.reset.themeJsonUnreadable")


@pytest.mark.unit
def test_stamp_update_skips_when_schedule_cannot_be_read(tmp_path, monkeypatch):
    import json

    studio = ts_service.ThemeStudioService(data_dir=tmp_path / "data")
    studio._ensure_dirs()
    path = studio.data_dir / "schedule.json"
    original = {"schema": 2, "enabled": True, "zones": {"morning": "amber-dusk"}}
    path.write_text(json.dumps(original), encoding="utf-8")

    path_type = type(path)
    real_read_text = path_type.read_text

    def flaky(self, *args, **kwargs):
        if self == path:
            raise PermissionError("in use")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(path_type, "read_text", flaky)
    studio._update_schedule_stamp(last_zone="morning")
    monkeypatch.setattr(path_type, "read_text", real_read_text)

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["enabled"] is True
    assert saved["zones"]["morning"] == "amber-dusk"
    assert "last_zone" not in saved


@pytest.mark.unit
def test_stale_tick_does_not_apply_a_theme_the_schedule_no_longer_wants(tmp_path, monkeypatch):
    import json
    from datetime import datetime

    studio = ts_service.ThemeStudioService(data_dir=tmp_path / "data")
    studio._ensure_dirs()
    (studio.data_dir / "schedule.json").write_text(
        json.dumps({"schema": 2, "enabled": True, "zones": {"morning": "amber-dusk"}}),
        encoding="utf-8",
    )
    studio._now = lambda: datetime(2026, 11, 23, 8, 0)

    real_read = studio._read_schedule
    calls = {"n": 0}

    def read_then_change(**kwargs):
        calls["n"] += 1
        result = real_read(**kwargs)
        if calls["n"] > 1:
            # The user re-picked the morning theme while the tick was running.
            result["zones"] = dict(result["zones"], morning="graphite")
        return result

    monkeypatch.setattr(studio, "_read_schedule", read_then_change)
    applied = []
    monkeypatch.setattr(studio, "_apply_theme_record", lambda theme_id: applied.append(theme_id) or {})

    assert studio._schedule_tick()["action"] == "superseded"
    assert applied == []


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
def test_requests_during_app_exit_do_not_restart_the_schedule_thread(studio):
    worker = studio._schedule_thread
    assert theme_studio.shutdown_service(3.0, final=True) is True
    worker.join(timeout=5)

    # A late request while the app exits still gets a working service ...
    result = theme_studio.run_entry_sync("get_theme", {})
    assert result.ok, result.error
    # ... but the schedule thread stays stopped.
    assert theme_studio.service_started() is False
    assert studio._schedule_thread is worker and not worker.is_alive()


@pytest.mark.unit
def test_startup_racing_with_exit_cannot_start_the_schedule_thread(tmp_path, monkeypatch):
    """A startup still running when the exit hook gave up waiting for the lock."""
    monkeypatch.setattr(theme_studio, "_service", None)
    monkeypatch.setattr(theme_studio, "_service_started", False)
    monkeypatch.setattr(theme_studio, "_app_exiting", False)
    monkeypatch.setattr(theme_studio, "_data_dir_override", tmp_path / "data")
    service = theme_studio.get_service()

    monkeypatch.setattr(theme_studio, "_app_exiting", True)
    # This is the step startup_sync() would reach after shutdown already ran.
    service._start_schedule_worker()
    assert service._schedule_thread is None


@pytest.mark.unit
def test_main_server_shutdown_hook_calls_theme_studio():
    import inspect

    from app.main_server import on_shutdown

    source = inspect.getsource(on_shutdown)
    assert "shutdown_service_async" in source
    assert 'what="theme studio shutdown"' in source


def _library_backup_zip(themes):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, doc in themes.items():
            archive.writestr(f"themes/{name}.json", json.dumps(doc))
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@pytest.mark.unit
def test_library_restore_keeps_absent_and_switch_only_materials(studio):
    data = _library_backup_zip({
        "colors-only": {"name": "colors-only", "vars": {"--nts-accent": "#3366ff"}, "dark_vars": {}},
        "switch-only": {
            "name": "switch-only",
            "vars": {"--nts-accent": "#3366ff"},
            "dark_vars": {},
            "materials": {"enabled": False},
        },
    })
    result = theme_studio.run_entry_sync("import_library", {"data": data})
    assert result.ok, result.error
    records = {}
    for summary in result.value["themes"]:
        path = studio._library_dir() / f"{summary['id']}.json"
        records[summary["name"]] = json.loads(path.read_text(encoding="utf-8"))
    assert "materials" not in records["colors-only"]
    assert records["switch-only"]["materials"] == {"enabled": False}


@pytest.mark.unit
def test_library_backup_reports_images_it_could_not_pack(studio, monkeypatch):
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
    filename = studio._save_background_bytes(gif)
    studio._save_library_item(
        name="with-image",
        vars_map={"--nts-chat-bg-image": f'url("{studio._background_url(filename)}")'},
        dark_map={},
    )
    real_write = zipfile.ZipFile.write

    def locked(self, path, arcname=None, *args, **kwargs):
        if str(arcname or "").startswith("images/"):
            raise PermissionError("locked")
        return real_write(self, path, arcname, *args, **kwargs)

    monkeypatch.setattr(zipfile.ZipFile, "write", locked)
    result = theme_studio.run_entry_sync("export_to_file", {"kind": "library"})
    assert result.ok, result.error
    assert result.value["skipped_images"] == 1


@pytest.mark.unit
def test_json_download_of_a_disabled_current_theme_stays_disabled(studio):
    import json

    seeded = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk"})
    assert seeded.ok, seeded.error
    off = theme_studio.run_entry_sync("apply_theme", {"enabled": False})
    assert off.ok, off.error

    result = theme_studio.run_entry_sync("export_to_file", {"kind": "json"})
    assert result.ok, result.error
    doc = json.loads(base64.b64decode(result.value["data"]).decode("utf-8"))
    assert doc["enabled"] is False


@pytest.mark.unit
def test_library_backup_reports_backgrounds_deleted_before_the_backup(studio):
    gif = base64.b64decode("R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==")
    filename = studio._save_background_bytes(gif)
    studio._save_library_item(
        name="with-image",
        vars_map={"--nts-chat-bg-image": f'url("{studio._background_url(filename)}")'},
        dark_map={},
    )
    (studio._backgrounds_dir() / filename).unlink()

    result = theme_studio.run_entry_sync("export_to_file", {"kind": "library"})
    assert result.ok, result.error
    assert result.value["skipped_images"] == 1


@pytest.mark.unit
def test_library_backup_uses_the_file_name_as_theme_id(studio):
    library = studio._library_dir()
    library.mkdir(parents=True, exist_ok=True)
    (library / "copied-entry.json").write_text(
        json.dumps({
            "id": "original-entry",
            "name": "copy",
            "vars": {"--nts-accent": "#3366ff"},
            "dark_vars": {},
        }),
        encoding="utf-8",
    )
    _, data, count = studio._build_library_archive()
    assert count == 1
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert "themes/copied-entry.json" in archive.namelist()
        doc = json.loads(archive.read("themes/copied-entry.json").decode("utf-8"))
    assert doc["id"] == "copied-entry"


@pytest.mark.unit
def test_import_with_materials_off_as_string_does_not_wipe_palette(studio):
    seeded = theme_studio.run_entry_sync("apply_saved_theme", {"id": "amber-dusk"})
    assert seeded.ok, seeded.error
    before = theme_studio.run_entry_sync("get_theme", {}).value["vars"]
    assert before, "precondition: the palette has colors"

    payload = json.dumps({"vars": {}, "materials": {"enabled": "false"}})
    result = theme_studio.run_entry_sync("import_theme", {"payload": payload})
    assert not result.ok
    assert theme_studio.run_entry_sync("get_theme", {}).value["vars"] == before


@pytest.mark.unit
def test_apply_counts_a_cleared_variable_as_written(studio):
    seeded = theme_studio.run_entry_sync("apply_theme", {"vars": {"--nts-accent": "#3366ff"}})
    assert seeded.ok, seeded.error

    result = theme_studio.run_entry_sync(
        "apply_theme", {"vars": {"--nts-accent": None, "--nts-nope-var": "#ffffff"}}
    )
    assert result.ok, result.error
    assert result.value["rejected"]
    assert "--nts-accent" not in theme_studio.run_entry_sync("get_theme", {}).value["vars"]


@pytest.mark.unit
@pytest.mark.parametrize("name", ["frosted", "Frosted Glass", "CUSTOM"])
def test_material_preset_refuses_built_in_names(studio, name):
    from theme_studio import materials as ts_materials

    result = theme_studio.run_entry_sync(
        "save_material_preset", {"name": name, "materials": ts_materials.default_materials()}
    )
    assert not result.ok
    assert str(result.error) == msg("theme.preset.nameReserved", name=name)


@pytest.mark.unit
def test_stale_schedule_tick_does_not_apply_after_disable(tmp_path):
    from datetime import datetime

    studio = ts_service.ThemeStudioService(data_dir=tmp_path / "data")
    studio._ensure_dirs()
    # On disk the user has already disabled the schedule ...
    (studio.data_dir / "schedule.json").write_text(
        json.dumps({"schema": 2, "enabled": False, "zones": {"morning": "amber-dusk"}}),
        encoding="utf-8",
    )
    studio._now = lambda: datetime(2026, 11, 23, 8, 0)
    real_read = studio._read_schedule
    calls = {"count": 0}

    def read_schedule(**kwargs):
        calls["count"] += 1
        schedule = real_read(**kwargs)
        if calls["count"] == 1:
            # ... but the tick's unlocked snapshot was taken just before that.
            schedule = dict(schedule, enabled=True)
        return schedule

    applied = []
    studio._read_schedule = read_schedule
    studio._apply_theme_record = lambda theme_id: applied.append(theme_id) or {}

    assert studio._schedule_tick()["action"] == "disabled"
    assert applied == []


def _rgba_parts(value):
    body = value[value.index("(") + 1 : value.rindex(")")]
    return [float(part) for part in body.split(",")]


@pytest.mark.unit
def test_bad_rgba_alpha_only_resets_that_color():
    from theme_studio import materials as ts_materials

    raw = ts_materials.default_materials()
    raw["global"]["tint"] = "rgba(1, 2, 3, 1.2.3)"
    raw["lighting"]["edgeColor"] = "#123456"
    clean, warnings = ts_materials.normalize_materials(raw)
    assert clean["global"]["tint"] == ts_materials.PARAM_BY_KEY["tint"].default
    assert clean["lighting"]["edgeColor"] == "#123456"
    assert warnings


@pytest.mark.unit
def test_dark_tint_mix_keeps_the_input_alpha():
    from theme_studio import materials as ts_materials

    def dark_bg_alpha(tint):
        raw = ts_materials.default_materials()
        raw["global"]["tint"] = tint
        clean, _warnings = ts_materials.normalize_materials(raw)
        return _rgba_parts(ts_materials.compile_materials(clean)[1]["--nts-mt-bg"])[3]

    opaque = dark_bg_alpha("rgba(255, 255, 255, 1)")
    half = dark_bg_alpha("rgba(255, 255, 255, 0.5)")
    assert abs(half - opaque / 2) < 0.002


@pytest.mark.unit
def test_component_opacity_uses_the_dark_tint_in_dark_mode():
    from theme_studio import materials as ts_materials

    raw = ts_materials.default_materials()
    # Same opacity as the global one, so the component background must equal the global one.
    raw["components"] = {"chat": {"mode": "custom", "opacity": raw["global"]["opacity"]}}
    clean, _warnings = ts_materials.normalize_materials(raw)
    light, dark = ts_materials.compile_materials(clean)
    assert light["--nts-mt-c-bg-chat"] == light["--nts-mt-bg"]
    assert dark["--nts-mt-c-bg-chat"] == dark["--nts-mt-bg"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("keyword", "rgb"), [("white", [255, 255, 255]), ("Black", [0, 0, 0]), ("transparent", [0, 0, 0])]
)
def test_color_keywords_that_validate_also_compile(keyword, rgb):
    from theme_studio import materials as ts_materials

    raw = ts_materials.default_materials()
    raw["lighting"]["edgeColor"] = keyword
    clean, warnings = ts_materials.normalize_materials(raw)
    assert clean["lighting"]["edgeColor"] == keyword
    assert not warnings
    parts = _rgba_parts(ts_materials.compile_materials(clean)[0]["--nts-mt-edge-color"])
    assert parts[:3] == rgb
    assert parts[3] == (0.0 if keyword == "transparent" else 1.0)


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
