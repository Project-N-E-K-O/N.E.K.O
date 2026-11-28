"""Theme library recycle bin: delete moves entries to ``trash/``, restore and clear."""

import pytest

from theme_studio import service


pytestmark = pytest.mark.unit

GIF = bytes.fromhex("47494638396101000100800000000000ffffff2c00000000010001000002024401003b")


def success(result):
    assert result.ok, result.error
    return result.value


async def test_restore_keeps_background_bytes_and_only_refills_free_slots(tmp_path):
    """A trashed entry keeps its background alive, and restore only refills zones that are still free."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    filename = studio._save_background_bytes(GIF)
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
    assert (studio._backgrounds_dir() / filename).read_bytes() == GIF
    success(await studio.save_schedule(enabled=False, zones={"morning": "", "evening": other["id"]}))
    restored = success(await studio.restore_theme(id=tid))
    assert restored["schedule_restored"] == ["zones.morning"]
    assert file.read_bytes() == original
    zones = studio._read_schedule()["zones"]
    assert zones["morning"] == tid and zones["evening"] == other["id"]
    assert studio._trash_stats()["count"] == 0
    assert not (await studio.restore_theme(id=tid)).ok


async def test_delete_still_works_when_schedule_json_is_corrupt(tmp_path):
    """The schedule is only read for zone refs; a corrupt schedule.json must not block the delete."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    theme = studio._save_library_item(name="A", vars_map={"--nts-accent": "#abcdef"}, dark_map={})
    studio._schedule_path().write_text("{bad", encoding="utf-8")
    assert success(await studio.delete_theme(id=theme["id"]))["undo"]
    assert not (studio._library_dir() / (theme["id"] + ".json")).exists()
    item, = studio._trash_stats()["items"]
    assert item["theme_id"] == theme["id"] and item["schedule_zones"] == 0


async def test_pruning_keeps_newest_and_survives_missing_sidecar(tmp_path, monkeypatch):
    """Only TRASH_LIMIT batches are kept; a lost sidecar still lists and restores; clear spares unrelated files."""
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


async def test_failed_move_preserves_theme_and_undo_is_retryable(tmp_path, monkeypatch):
    """A failed delete leaves the entry in place; a failed restore leaves it in the bin for a retry."""
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


async def test_resaving_same_second_does_not_reuse_a_recoverable_id(tmp_path, monkeypatch):
    """A new entry never takes an id that is still waiting in the bin."""
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


async def test_same_clock_tick_deletes_stay_in_order(tmp_path, monkeypatch):
    """With a coarse clock, later deletes still sort as newer, so pruning drops the right batch."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    themes = [studio._save_library_item(name=name, vars_map={"--nts-accent": "#abcdef"}, dark_map={})
              for name in ["Z", "A"]]

    class FrozenDateTime(service.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 1, 1, tzinfo=tz)

    monkeypatch.setattr(service, "datetime", FrozenDateTime)
    for theme in themes:
        success(await studio.delete_theme(id=theme["id"]))
    assert [item["name"] for item in studio._trash_stats()["items"]] == ["A", "Z"]


async def test_trash_stays_out_of_library_listing_and_backup(tmp_path):
    """Trashed entries are not listed, not exported in a library backup, and are reported by studio_status."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    kept = studio._save_library_item(name="Kept", vars_map={"--nts-accent": "#abcdef"}, dark_map={})
    gone = studio._save_library_item(name="Gone", vars_map={"--nts-accent": "#123456"}, dark_map={})
    success(await studio.delete_theme(id=gone["id"]))
    assert [item["id"] for item in studio._library_items()] == [kept["id"]]
    _, _, exported = studio._build_library_archive()
    assert exported == 1
    status = success(await studio.studio_status())
    assert status["library_count"] == 1 and status["trash"]["count"] == 1


async def test_delete_and_restore_refuse_builtin_and_unknown_ids(tmp_path):
    """Built-in themes cannot be trashed, and restore needs a matching batch."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    preset = next(iter(studio._presets_dir().glob("*.json"))).stem
    assert not (await studio.delete_theme(id=preset)).ok
    assert not (await studio.delete_theme(id="../evil")).ok
    assert not (await studio.restore_theme(id="missing-theme")).ok
    assert not (await studio.restore_theme(id=preset)).ok
    assert studio._trash_stats()["count"] == 0
