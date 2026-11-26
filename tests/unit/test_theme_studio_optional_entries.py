"""Optional Theme Studio entries: material preset rename / duplicate and batch delete."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from theme_studio import service
from theme_studio.i18n import msg


def _ok(result):
    assert result.ok, result.error
    return result.value


@pytest.fixture
def studio(tmp_path):
    instance = service.ThemeStudioService(data_dir=tmp_path)
    instance._ensure_dirs()
    return instance


def _save_preset(studio, name):
    return _ok(asyncio.run(studio.save_material_preset(name=name)))


@pytest.mark.unit
def test_rename_moves_the_preset_and_the_current_selection(studio):
    _save_preset(studio, "Mine")
    _save_preset(studio, "Other")
    asyncio.run(studio.apply_theme(materials={"enabled": True, "preset": "Mine"}))

    renamed = _ok(asyncio.run(studio.rename_material_preset(name="Mine", new_name="Renamed")))
    assert renamed["name"] == "Renamed"
    assert "Mine" not in renamed["names"] and {"Renamed", "Other"} <= set(renamed["names"])
    current = _ok(asyncio.run(studio.get_theme()))["materials"]
    assert current["preset"] == "Renamed"
    assert set(current["custom_presets"]) == {"Renamed", "Other"}


@pytest.mark.unit
def test_duplicate_keeps_the_source_and_picks_a_free_name(studio):
    _save_preset(studio, "Mine")
    first = _ok(asyncio.run(studio.duplicate_material_preset(name="Mine")))
    second = _ok(asyncio.run(studio.duplicate_material_preset(name="Mine")))
    assert first["name"] != second["name"]
    assert first["name"].startswith("Mine")
    names = set(second["names"])
    assert {"Mine", first["name"], second["name"]} <= names
    stored = _ok(asyncio.run(studio.get_theme()))["materials"]["custom_presets"]
    assert stored[first["name"]] == stored["Mine"]

    named = _ok(asyncio.run(studio.duplicate_material_preset(name="Mine", new_name="Copy B")))
    assert named["name"] == "Copy B"


@pytest.mark.unit
def test_rename_and_duplicate_refuse_bad_targets(studio):
    _save_preset(studio, "Mine")
    _save_preset(studio, "Other")
    taken = asyncio.run(studio.rename_material_preset(name="Mine", new_name="Other"))
    assert not taken.ok and str(taken.error) == msg("theme.preset.nameTaken", name="Other")
    same = asyncio.run(studio.duplicate_material_preset(name="Mine", new_name="Mine"))
    assert not same.ok
    reserved = asyncio.run(studio.rename_material_preset(name="Mine", new_name="liquid"))
    assert not reserved.ok and str(reserved.error) == msg("theme.preset.nameReserved", name="liquid")
    missing = asyncio.run(studio.duplicate_material_preset(name="Nope"))
    assert not missing.ok and str(missing.error) == msg("theme.preset.notFound", name="Nope")
    empty = asyncio.run(studio.rename_material_preset(name="Mine", new_name="  "))
    assert not empty.ok
    # Nothing changed by the refusals.
    stored = _ok(asyncio.run(studio.get_theme()))["materials"]["custom_presets"]
    assert set(stored) == {"Mine", "Other"}


def _library_entry(studio, name):
    return studio._save_library_item(name=name, vars_map={"--nts-accent": "#abcdef"}, dark_map={})["id"]


@pytest.mark.unit
def test_delete_themes_moves_each_entry_to_the_trash_and_reports_failures(studio):
    first = _library_entry(studio, "A")
    second = _library_entry(studio, "B")
    result = _ok(asyncio.run(studio.delete_themes(theme_ids=[first, second, "amber-dusk", "missing-one", first, 7])))
    assert result["deleted"] == [first, second]
    failed = {item["id"]: item["error"] for item in result["failed"]}
    assert failed["amber-dusk"] == msg("library.delete.builtin")
    assert "missing-one" in failed and "" in failed
    assert result["undo"] is True
    for theme_id in (first, second):
        assert not (studio._library_dir() / f"{theme_id}.json").exists()
    assert {item["theme_id"] for item in studio._trash_stats()["items"]} == {first, second}


@pytest.mark.unit
def test_delete_themes_validates_the_list(studio):
    assert not asyncio.run(studio.delete_themes(theme_ids=[])).ok
    assert not asyncio.run(studio.delete_themes(theme_ids="abc")).ok
    too_many = asyncio.run(studio.delete_themes(theme_ids=["x"] * (service.LIBRARY_MAX_ITEMS + 1)))
    assert not too_many.ok
    assert str(too_many.error) == msg("library.deleteMany.tooMany", limit=service.LIBRARY_MAX_ITEMS)


@pytest.mark.unit
def test_single_delete_keeps_its_shape(studio):
    theme_id = _library_entry(studio, "Solo")
    assert _ok(asyncio.run(studio.delete_theme(id=theme_id))) == {
        "deleted": True, "theme_id": theme_id, "undo": True,
    }


@pytest.mark.unit
def test_random_tweak_step_is_small_and_wired():
    """The panel's random tweak stays within small bounds and reuses the variant path."""
    js = (Path(__file__).resolve().parents[2] / "static" / "theme-studio" / "app.js").read_text(encoding="utf-8")
    body = js[js.index("function randomVariantStep"):js.index("function applyRandomTweak")]
    assert re.search(r"spread\(12\)", body) and re.search(r"spread\(0\.06\)", body) and re.search(r"spread\(0\.05\)", body)
    tweak = js[js.index("function applyRandomTweak"):js.index("function initWallpaperFeature")]
    assert "buildVariantPalette(step)" in tweak and "applyWallpaperPalette(" in tweak
