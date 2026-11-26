import json

import pytest

from theme_studio import service


def success(result):
    assert result.ok, result.error
    return result.value


def test_image_in_damaged_recovery_copy_survives_collection(tmp_path):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    gif = bytes.fromhex("47494638396101000100800000000000ffffff2c00000000010001000002024401003b")
    filename = studio._save_background_bytes(gif)
    backup = tmp_path / "theme.json.corrupt-123"
    backup.write_bytes(b"\xff" + studio._background_url(filename).encode("ascii") + b'"')
    assert studio._gc_backgrounds() == []
    assert (studio._backgrounds_dir() / filename).read_bytes() == gif


@pytest.mark.asyncio
async def test_restore_keeps_background_bytes_and_only_refills_free_slots(tmp_path):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    gif = bytes.fromhex("47494638396101000100800000000000ffffff2c00000000010001000002024401003b")
    filename = studio._save_background_bytes(gif)
    theme = studio._save_library_item(
        name="A", vars_map={"--nts-chat-bg-image": f'url("{studio._background_url(filename)}")'}, dark_map={}
    )
    other = studio._save_library_item(name="B", vars_map={"--nts-accent": "#abcdef"}, dark_map={})
    tid = theme["id"]
    file = studio._library_dir() / (tid + ".json")
    original = file.read_bytes()
    success(await studio.save_schedule(enabled=False, zones={"morning": tid, "evening": tid}))
    assert success(await studio.delete_theme(id=tid))["undo"]
    assert not file.exists()
    stats = studio._trash_stats()
    item, = stats["items"]
    assert stats["count"] == 1 and stats["files"] == 2
    assert item["restorable"] and item["bytes"] == len(original) and item["schedule_zones"] == 2
    assert item["theme_id"] == tid and item["name"] == "A" and item["deleted_at"]
    assert not studio._gc_backgrounds()
    assert (studio._backgrounds_dir() / filename).read_bytes() == gif
    success(await studio.save_schedule(enabled=False, zones={"morning": "", "evening": other["id"]}))
    restored = success(await studio.restore_theme(id=tid))
    assert restored["schedule_restored"] == ["zones.morning"]
    assert file.read_bytes() == original
    zones = studio._read_schedule()["zones"]
    assert zones["morning"] == tid and zones["evening"] == other["id"]
    assert studio._trash_stats()["count"] == 0
    assert not (await studio.restore_theme(id=tid)).ok


@pytest.mark.asyncio
async def test_delete_still_works_when_schedule_json_is_corrupt(tmp_path):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    theme = studio._save_library_item(name="A", vars_map={"--nts-accent": "#abcdef"}, dark_map={})
    studio._schedule_path().write_text("{bad", encoding="utf-8")
    assert success(await studio.delete_theme(id=theme["id"]))["undo"]
    assert not (studio._library_dir() / (theme["id"] + ".json")).exists()
    item, = studio._trash_stats()["items"]
    assert item["theme_id"] == theme["id"] and item["schedule_zones"] == 0


@pytest.mark.asyncio
async def test_material_preset_apply_returns_the_revision_get_theme_reports(tmp_path):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    studio._ensure_dirs()
    applied = success(await studio.apply_theme(materials={"enabled": True, "preset": "liquid"}))
    assert applied["revision"] == success(await studio.get_theme())["revision"]


@pytest.mark.asyncio
async def test_pruning_keeps_newest_and_survives_missing_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "TRASH_LIMIT", 2)
    studio = service.ThemeStudioService(data_dir=tmp_path)
    themes = [studio._save_library_item(name=name, vars_map={"--nts-accent": "#abcdef"}, dark_map={})
              for name in ["Z", "M", "A"]]
    for theme in themes:
        success(await studio.delete_theme(id=theme["id"]))
    newest, older = studio._trash_stats()["items"]
    assert [newest["name"], older["name"]] == ["A", "M"]
    assert not (await studio.restore_theme(id=themes[0]["id"])).ok
    (studio._trash_dir() / (newest["batch"] + ".meta.json")).unlink()
    assert studio._trash_stats()["items"][0]["name"] == "A"
    success(await studio.restore_theme(id=themes[-1]["id"]))
    unrelated = studio._trash_dir() / "notes__draft.json"
    unrelated.write_text("{}", encoding="utf-8")
    cleared = success(await studio.clear_trash())
    assert cleared["removed"] == 2 and cleared["bytes"] > 0
    assert unrelated.exists()
    assert studio._trash_stats()["count"] == 0


@pytest.mark.asyncio
async def test_failed_move_preserves_theme_and_undo_is_retryable(tmp_path, monkeypatch):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    record = studio._save_library_item(name="A", vars_map={"--nts-accent": "#abcdef"}, dark_map={})
    tid = record["id"]
    original = service._write_json_atomic

    def fail(*args):
        raise PermissionError("write denied")

    monkeypatch.setattr(service, "_write_json_atomic", fail)
    assert not (await studio.delete_theme(id=tid)).ok
    assert studio._load_library_item(tid) == record and studio._trash_stats()["count"] == 0
    monkeypatch.setattr(service, "_write_json_atomic", original)
    success(await studio.save_schedule(enabled=False, zones={"morning": tid}))
    success(await studio.delete_theme(id=tid))
    success(await studio.save_schedule(enabled=False, zones={"morning": ""}))
    monkeypatch.setattr(studio, "_save_schedule_unlocked", fail)
    assert not (await studio.restore_theme(id=tid)).ok
    assert not studio._theme_exists(tid) and studio._trash_stats()["count"] == 1
    monkeypatch.undo()
    assert success(await studio.restore_theme(id=tid))["schedule_restored"] == ["zones.morning"]


@pytest.mark.asyncio
async def test_resaving_same_second_does_not_reuse_a_recoverable_id(tmp_path, monkeypatch):
    class FrozenDateTime(service.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, tzinfo=tz)

    monkeypatch.setattr(service, "datetime", FrozenDateTime)
    studio = service.ThemeStudioService(data_dir=tmp_path)
    theme = {"name": "a", "vars_map": {"--nts-accent": "#abcdef"}, "dark_map": {}}
    original = studio._save_library_item(**theme)
    success(await studio.delete_theme(id=original["id"]))
    new = studio._save_library_item(**theme)
    assert original["id"] != new["id"]
    success(await studio.restore_theme(id=original["id"]))
    assert studio._theme_exists(new["id"]) and studio._theme_exists(original["id"])


@pytest.mark.asyncio
async def test_apply_legacy_library_name_returns_the_persisted_revision(tmp_path):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    record = studio._save_library_item(name="legacy", vars_map={"--nts-accent": "#abcdef"}, dark_map={})
    record["name"] = "one\n" + "z" * 55
    path = studio._library_dir() / (record["id"] + ".json")
    path.write_text(json.dumps(record), encoding="utf-8")
    applied = success(await studio.apply_saved_theme(id=record["id"]))
    palette = success(await studio.get_theme())
    assert applied["theme"]["name"] == palette["name"] == "one " + "z" * 36
    assert applied["revision"] == palette["revision"]


@pytest.mark.asyncio
async def test_display_name_deduplication_preserves_distinct_ids(tmp_path):
    studio = service.ThemeStudioService(data_dir=tmp_path)
    success(await studio.apply_theme(vars={"--nts-accent": "#abcdef"}))
    assert not (await studio.save_theme(name="  ")).ok
    assert not (await studio.save_theme(name="x" * 41)).ok
    name = "x" * 40
    first = success(await studio.save_theme(name=name))["theme"]
    duplicate = success(await studio.save_theme(name=name))
    second = duplicate["theme"]
    assert second["name"] == "x" * 36 + " (2)" and duplicate["renamed_from"] == name
    assert second["id"] != first["id"]
    path = studio._library_dir() / (second["id"] + ".json")
    assert "_renamed_from" not in json.loads(path.read_text(encoding="utf-8"))
    assert not (await studio.update_theme_meta(id=second["id"], name=name)).ok
    renamed = success(await studio.update_theme_meta(id=first["id"], name="new\n name"))
    assert renamed["name"] == "new name" and renamed["theme_id"] == first["id"]
