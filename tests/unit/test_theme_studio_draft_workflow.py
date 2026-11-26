import asyncio
import json

import pytest

from tests.fake_clock import patch_module_clock
from theme_studio import service


@pytest.fixture
def studio(tmp_path):
    return service.ThemeStudioService(data_dir=tmp_path)


def run(entry):
    result = asyncio.run(entry)
    assert result.ok, result.error
    return result.value


def test_preview_is_bounded_validated_and_superseded_by_saved_changes(studio, monkeypatch):
    saved = run(studio.apply_theme(vars={"--nts-accent": "#202020"}))
    disk = studio.theme_path.read_bytes()
    key = "--nts-accent"
    preview = run(studio.start_preview(palette={key: "#bbb", "--ignored": "invalid"}, seconds=1))["preview"]
    assert preview["seconds"] == 5
    draft = studio._with_preview(studio._read_unlocked())
    assert draft["revision"] == saved["revision"]
    assert draft["vars"][key] == "#bbb" and "--ignored" not in draft["vars"]
    assert studio.theme_path.read_bytes() == disk
    assert not run(studio.stop_preview(id="previous"))["cleared"]
    patch_module_clock(monkeypatch, service, time=lambda: preview["expires_at"])
    assert studio.preview_snapshot() is None
    run(studio.start_preview(palette={key: ""}))
    assert key not in studio._with_preview(studio._read_unlocked())["vars"]
    run(studio.apply_theme(vars={key: "#fff"}, base_revision=saved["revision"]))
    assert studio.preview_snapshot() is None


def test_stale_apply_keeps_current_theme_and_preview(studio):
    old = run(studio.apply_theme(name="First"))
    saved = run(studio.apply_theme(name="Second", base_revision=old["revision"]))
    preview = run(studio.start_preview(palette={"--nts-accent": "#abc"}))
    disk = studio.theme_path.read_bytes()
    stale = run(studio.apply_theme(name="Lost update", base_revision=old["revision"]))
    assert stale["applied"] is False
    assert stale["conflict"]["disk_revision"] == saved["revision"]
    assert preview["preview"]["id"] == studio.preview_snapshot()["id"]
    assert studio.theme_path.read_bytes() == disk
    assert "revision" not in json.loads(disk)
    result = asyncio.run(studio.apply_theme(vars={"--unknown": "ignored"}))
    assert not result.ok and studio.theme_path.read_bytes() == disk
    reset = run(studio.reset_theme())
    assert reset["previous_revision"] == saved["revision"]
    assert studio.preview_snapshot() is None


def test_saved_theme_returns_actual_predecessor_for_undo(studio):
    old = run(studio.apply_theme(name="First", vars={"--nts-accent": "#123"}))
    saved = run(studio.apply_saved_theme(theme_id="amber-dusk"))
    assert saved["previous_revision"] == old["revision"]
    assert saved["revision"] == run(studio.get_theme())["revision"]


def test_check_contrast_uses_current_audit_and_overlay(studio):
    run(studio.apply_theme(vars={"--nts-topbar-bg": "#000", "--nts-topbar-text": "#fff"}))
    missing = run(studio.check_contrast(dark={"--nts-topbar-text": "#000"}))
    assert not any(row["mode"] == "dark" and row["pair"] == "topbar" for row in missing["rows"])
    result = run(studio.check_contrast(dark={"--nts-topbar-text": "#000", "--nts-topbar-bg": "#000"}))
    row = next(row for row in result["fails"] if row["pair"] == "topbar")
    assert row["mode"] == "dark" and row["ratio"] == 1.0
    assert any(row["mode"] == "light" and row["pair"] == "topbar" and row["ok"] for row in result["rows"])
    assert not run(studio.check_contrast(standalone=True))["rows"]


def test_check_contrast_uses_the_dark_compact_prompt_in_dark_mode(studio):
    # Light text passes on the dark #252d3d prompt and fails on the light one.
    result = run(studio.check_contrast(palette={"--nts-rec-text": "#e6ebf2"},
                                       dark={"--nts-rec-text": "#e6ebf2"}, standalone=True))
    rows = {row["mode"]: row for row in result["rows"] if row["pair"] == "recommendation-text"}
    assert rows["dark"]["ok"] and rows["dark"]["surface"] == "#252d3d"
    assert not rows["light"]["ok"] and rows["light"]["bg"] is None


def test_draft_and_apply_both_remove_old_material_overrides(studio):
    run(studio.apply_theme(materials={"enabled": True, "dark": {"surface": {"radius": 31}}}))
    run(studio.save_material_preset("Local"))
    patch = {"preset": "Local", "surface": {"radius": 24}, "dark": {}, "components": {}}
    run(studio.start_preview(materials=patch))
    draft = studio._with_preview(studio._read_unlocked())["materials"]
    saved = run(studio.apply_theme(materials=patch))
    assert not saved["material_warnings"]
    assert draft == saved["materials"]
    assert draft["dark"] == {} and "Local" in draft["custom_presets"]


def test_corrupt_palette_backup_survives_repair_and_keeps_original_bytes(studio):
    original = b"broken-json"
    studio.theme_path.write_bytes(original)
    studio._ensure_file()
    recovery = studio._theme_recovery
    assert recovery and (studio.data_dir / recovery["file"]).read_bytes() == original
    assert json.loads(studio.theme_path.read_bytes())["schema"] == service.SCHEMA_VERSION
    run(studio.apply_theme(name="Saved"))
    assert studio._theme_recovery is None


def test_diagnostics_merge_surfaces_by_theme_and_loader(studio):
    run(studio.apply_theme(name="First"))

    def report(version, surface):
        run(studio.loader_report(report={
            "version": version, "mtSurfaces": [surface], "mtPreset": "liquid", "mtQuality": "high",
        }))
        return json.loads((studio.data_dir / "diagnostics.json").read_bytes())["mt_surfaces"]

    assert report("one", {"k": "chatShell", "b": True}) == [{"k": "chatShell", "r": 0, "b": 1, "f": 0}]
    assert report("one", {"k": "chatShell", "r": True}) == [{"k": "chatShell", "r": 1, "b": 1, "f": 0}]
    assert report("two", {"k": "topbar", "f": True}) == [{"k": "topbar", "r": 0, "b": 0, "f": 1}]
