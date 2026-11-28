"""Theme library tags: normalisation, the edit entry and round trips.

Tags are user-editable on custom library entries (``update_theme_meta``) and
ride along with every copy of a theme: the library listing, the share zip and
the library backup. They all go through ``service._theme_tags`` (trim, drop
control characters, clip to 12 chars, dedupe, at most 4).
"""

import base64
import json
import threading

import pytest

from theme_studio import service


pytestmark = pytest.mark.unit

ACCENT = {"--nts-accent": "#abcdef"}


def success(result):
    assert result.ok, result.error
    return result.value


def entry_path(studio, theme_id):
    return studio._library_dir() / f"{theme_id}.json"


def stored(studio, theme_id):
    return json.loads(entry_path(studio, theme_id).read_text(encoding="utf-8"))


def listed(items, theme_id):
    return next(item for item in items if item["id"] == theme_id)


def test_theme_tags_trim_dedupe_clip_and_limit():
    """Whitespace and control chars are stripped, clipped duplicates collapse, non-strings drop, max 4."""
    raw = [
        "  warm  ",
        "warm",
        "",
        "   ",
        7,
        None,
        "​cool\u0000",
        "abcdefghijklmn",
        "abcdefghijklXY",
        "dark",
        "fifth",
    ]
    assert service._theme_tags(raw) == ["warm", "cool", "abcdefghijkl", "dark"]
    assert service._theme_tags("warm, cool") == []
    assert service._theme_tags(None) == []


async def test_update_meta_normalises_tags(tmp_path):
    """The edit entry stores normalised tags and reports them back."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    tid = studio._save_library_item(name="A", vars_map=ACCENT, dark_map={})["id"]
    result = success(await studio.update_theme_meta(
        theme_id=tid, tags=[" warm ", "warm", "glass", "x" * 20, "night", "extra"]
    ))
    expected = ["warm", "glass", "x" * 12, "night"]
    assert result["updated"] and result["changed"] == ["tags"]
    assert result["tags"] == expected
    assert stored(studio, tid)["tags"] == expected


async def test_update_meta_accepts_comma_string_and_clears_with_empty_list(tmp_path):
    """A CLI-style separated string is split; an empty list removes the field; repeats are no-ops."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    tid = studio._save_library_item(name="A", vars_map=ACCENT, dark_map={})["id"]
    result = success(await studio.update_theme_meta(id=tid, tags="warm，glass;warm  night"))
    assert result["tags"] == ["warm", "glass", "night"]
    again = success(await studio.update_theme_meta(id=tid, tags=["warm", "glass", "night"]))
    assert again["updated"] is False and again["changed"] == []
    cleared = success(await studio.update_theme_meta(id=tid, tags=[]))
    assert cleared["changed"] == ["tags"] and cleared["tags"] == []
    assert "tags" not in stored(studio, tid)


@pytest.mark.parametrize("bad", [5, {"warm": 1}, ["warm", 3], ["warm", None], [["nested"]]])
async def test_update_meta_rejects_non_string_tags(tmp_path, bad):
    """A malformed tags value is refused rather than silently read as "clear all tags"."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    tid = studio._save_library_item(
        name="A", vars_map=ACCENT, dark_map={}, tags=["keep"]
    )["id"]
    before = entry_path(studio, tid).read_bytes()
    result = await studio.update_theme_meta(theme_id=tid, tags=bad)
    assert not result.ok
    assert entry_path(studio, tid).read_bytes() == before


async def test_update_meta_refuses_builtin_themes(tmp_path):
    """Built-in themes ship read-only: their tags cannot be edited and nothing is written."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    preset = next(iter(studio._presets_dir().glob("*.json"))).stem
    before = studio._preset_path(preset).read_bytes()
    result = await studio.update_theme_meta(theme_id=preset, tags=["mine"])
    assert not result.ok
    assert studio._preset_path(preset).read_bytes() == before
    assert not entry_path(studio, preset).exists()


async def test_update_meta_holds_write_lock_for_the_whole_edit(tmp_path, monkeypatch):
    """The read half of the read-modify-write already runs under ``_WRITE_LOCK``."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    tid = studio._save_library_item(name="A", vars_map=ACCENT, dark_map={})["id"]
    original = studio._load_library_item
    seen = []

    def load(theme_id):
        box = []
        probe = threading.Thread(target=lambda: box.append(service._WRITE_LOCK.acquire(blocking=False)))
        probe.start()
        probe.join()
        if box[0]:
            service._WRITE_LOCK.release()
        seen.append(box[0])
        return original(theme_id)

    monkeypatch.setattr(studio, "_load_library_item", load)
    success(await studio.update_theme_meta(theme_id=tid, tags=["warm"]))
    assert seen == [False]


async def test_list_themes_exposes_normalised_tags(tmp_path):
    """Library and built-in entries list their tags, cleaned the same way as an edit would write them."""
    studio = service.ThemeStudioService(data_dir=tmp_path)
    tid = studio._save_library_item(name="A", vars_map=ACCENT, dark_map={})["id"]
    record = stored(studio, tid)
    record["tags"] = [" warm ", "warm", 9, "‮night", "", "glass", "late"]
    entry_path(studio, tid).write_text(json.dumps(record), encoding="utf-8")
    themes = success(await studio.list_themes())["themes"]
    assert listed(themes, tid)["tags"] == ["warm", "night", "glass", "late"]
    builtins = [item for item in themes if item["builtin"]]
    assert builtins and any(item["tags"] for item in builtins)
    for item in builtins:
        assert item["tags"] == service._theme_tags(item["tags"])


async def test_tags_survive_save_and_share_zip_round_trip(tmp_path):
    """Tags written at save time travel through export_theme_archive / import_theme_archive."""
    source = service.ThemeStudioService(data_dir=tmp_path / "a")
    tid = source._save_library_item(
        name="Shared", vars_map=ACCENT, dark_map={}, note="hi", tags=[" warm", "warm", "glass"]
    )["id"]
    assert stored(source, tid)["tags"] == ["warm", "glass"]
    success(await source.update_theme_meta(theme_id=tid, tags=["warm", "glass", "night"]))
    archive = success(await source.export_theme_archive(theme_id=tid))

    target = service.ThemeStudioService(data_dir=tmp_path / "b")
    imported = success(await target.import_theme_archive(filename=archive["filename"], data=archive["data"]))
    new_id = imported["theme"]["id"]
    assert imported["theme"]["tags"] == ["warm", "glass", "night"]
    assert stored(target, new_id)["tags"] == ["warm", "glass", "night"]
    assert listed(success(await target.list_themes())["themes"], new_id)["tags"] == ["warm", "glass", "night"]


async def test_tags_survive_library_backup_round_trip(tmp_path):
    """A library backup restores each entry with its own tags, and untagged entries stay untagged."""
    source = service.ThemeStudioService(data_dir=tmp_path / "a")
    source._save_library_item(name="Tagged", vars_map=ACCENT, dark_map={}, tags=["warm", "glass"])
    source._save_library_item(name="Plain", vars_map={"--nts-accent": "#123456"}, dark_map={})
    _, data, exported = source._build_library_archive()
    assert exported == 2

    target = service.ThemeStudioService(data_dir=tmp_path / "b")
    success(await target.import_library(filename="backup.zip", data=base64.b64encode(data).decode("ascii")))
    by_name = {item["name"]: item for item in target._library_items()}
    assert by_name["Tagged"]["tags"] == ["warm", "glass"]
    assert by_name["Plain"]["tags"] == []
