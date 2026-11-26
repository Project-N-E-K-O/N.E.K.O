import base64
import json

import pytest

from theme_studio import materials
from theme_studio.i18n import reset_request_locale, set_request_locale
from theme_studio.service import ThemeStudioService


@pytest.mark.asyncio
async def test_preset_round_trip_leaves_live_materials_untouched(tmp_path):
    studio = ThemeStudioService(data_dir=tmp_path)
    draft = {"preset": "frosted", "surface": {"radius": 31}, "enabled": True}
    saved = (await studio.save_material_preset("First", draft)).value
    assert not saved["materials"]["enabled"]
    assert not saved["replaced"]
    assert saved["materials"]["custom_presets"]["First"]["surface"]["radius"] == 31
    download = (await studio.export_material_preset("First")).value
    assert download["mime"] == "application/json"
    assert json.loads(base64.b64decode(download["data"]))["name"] == "First"
    imported = (await studio.import_material_preset(data=download["data"], name="Second")).value
    assert imported["names"] == ["First", "Second"]
    assert not imported["materials"]["enabled"]
    assert imported["materials"]["custom_presets"]["Second"] == saved["materials"]["custom_presets"]["First"]
    applied = (await studio.save_material_preset("Second", draft, apply=True)).value
    assert applied["replaced"]
    assert applied["materials"]["enabled"]
    assert applied["materials"]["preset"] == "Second"
    deleted = (await studio.delete_material_preset("Second")).value
    assert deleted["removed"]["surface"]["radius"] == 31
    assert deleted["materials"]["preset"] == "custom"


@pytest.mark.asyncio
async def test_preset_limit_and_bad_input_preserve_disk(tmp_path, monkeypatch):
    studio = ThemeStudioService(data_dir=tmp_path)
    monkeypatch.setattr(materials, "MATERIAL_PRESET_LIMIT", 1)
    assert (await studio.save_material_preset("old")).ok
    assert (await studio.save_material_preset("old")).value["replaced"]
    before = studio.theme_path.read_bytes()
    assert not (await studio.save_material_preset("new")).ok
    assert not (await studio.save_material_preset("old", ["custom"])).ok
    assert not (await studio.save_material_preset("old", apply="invalid")).ok
    assert not (await studio.import_material_preset(data="???")).ok
    assert studio.theme_path.read_bytes() == before


def test_material_warnings_cover_dark_sections_and_components():
    component = next(iter(materials.COMPONENTS))
    draft = {"surface": {"radius": 2000}, "dark": {"global": {"radius": 0}},
             "components": {component: {"mode": "custom", "unknownParam": True}}}
    live, warnings = materials.apply_materials(materials.default_materials(), draft)
    assert len(warnings) == 4
    assert live["surface"]["radius"] == materials.PARAM_BY_KEY["radius"].maximum
    assert "radius" not in live["dark"]["global"]
    assert live["components"][component]["mode"] == "inherit"


@pytest.mark.parametrize("locale", ["en", "ja", "ko", "zh-CN", "zh-TW", "ru", "pt", "es"])
def test_material_warning_identifies_parameter_in_each_language(locale):
    token = set_request_locale(locale)
    try:
        _, warnings = materials.normalize_materials({"dark": {"global": {"radius": 1}}})
    finally:
        reset_request_locale(token)
    assert len(warnings) == 1
    assert "dark.global.radius" in warnings[0]
    assert "surface" in warnings[0]


@pytest.mark.asyncio
async def test_every_editor_parameter_round_trips_through_the_live_theme(tmp_path):
    studio = ThemeStudioService(data_dir=tmp_path)
    params = (await studio.material_spec()).value["params"]
    draft = {"enabled": True}
    for param in params:
        kind, default = param["kind"], param["default"]
        if kind == "bool":
            value = not default
        elif kind == "enum":
            value = next(choice for choice in param["choices"] if choice != default)
        elif kind == "color":
            value = "#238f47"
        elif kind == "string":
            value = "sample"
        else:
            value = param["min"] if param["min"] != default else param["max"]
        draft.setdefault(param["section"], {})[param["key"]] = value

    saved = await studio.apply_theme(materials=draft)
    assert saved.ok and not saved.value["material_warnings"]
    actual = (await studio.get_theme()).value["materials"]
    for param in params:
        section, key = param["section"], param["key"]
        assert actual[section][key] == draft[section][key], (section, key)
