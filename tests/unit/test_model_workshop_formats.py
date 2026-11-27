"""Native admission fixtures are structural samples, not renderable avatars."""

import hashlib
import base64
import json
import struct
import zipfile

import pytest

from services.model_workshop.formats import (
    FormatLimits, ModelFormatError, validate_live2d_bundle, validate_vrm_bundle,
    validate_mmd_bundle, validate_model_bundle,
)


def make_native_bundle(tmp_path, *, kind="live2d", entry="Avatar.model3.json", files):
    """Each repository owns its fixtures and does not import the other checkout."""
    manifest = {
        "schema": "neko.model.package.v1", "model_format": kind, "entrypoint": entry,
        "display_name": "Native fixture", "attribution": "Fixture author", "license": "Test only",
        "files": [{"path": name, "byte_size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                  for name, raw in sorted(files.items())],
    }
    path = tmp_path / "native.neko-model.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, raw in files.items():
            archive.writestr("model/" + name, raw)
    return path


def live2d_files(*, reference="textures/face.png", mapping=None):
    """Use real Cubism field shapes with tiny non-renderable binary placeholders."""
    model = {"Version": 3, "FileReferences": {"Moc": "Avatar.moc3", "Textures": [reference]}}
    if mapping is not None:
        model["EmotionMapping"] = mapping
    motion = {
        "Version": 3, "Meta": {"Duration": 1, "Fps": 30, "Loop": False, "CurveCount": 1,
                                "TotalSegmentCount": 1, "TotalPointCount": 2},
        "Curves": [{"Target": "Parameter", "Id": "ExistingParameter", "Segments": [0, 0, 0, 1, 1]}],
    }
    return {
        "Avatar.model3.json": json.dumps(model).encode(),
        "Avatar.moc3": b"MOC3 structural fixture only",
        "textures/face.png": b"\x89PNG\r\n\x1a\nfixture",
        "motions/test.motion3.json": json.dumps(motion).encode(),
        "expressions/test.exp3.json": json.dumps({"Type": "Live2D Expression", "Parameters": []}).encode(),
        "LICENSE.txt": b"Permission statement",
    }


def test_accepts_live2d_and_preserves_original_bytes(tmp_path):
    files = live2d_files(mapping={"expressions": {"existing_group": ["expressions/test.exp3.json"]}})
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_live2d_bundle(path)
    assert result.model_format == "live2d"
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]



@pytest.mark.parametrize("name", ["Avatar.vtube.json", "Avatar.xyplugin.json", "settings/Avatar.XYPLUGIN.JSON",
                                "items_pinned_to_model.json", "settings/ITEMS_PINNED_TO_MODEL.JSON"])
def test_live2d_tool_settings_are_preserved_as_data(tmp_path, name):
    """Tool settings are opaque to NEKO; their strings never become dependencies."""
    files = live2d_files()
    files[name] = b'{"url":"http://127.0.0.1/private","path":"../../outside.png","command":"do not execute"}'
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_live2d_bundle(path)
    assert {item.path for item in result.files} == set(files)
    assert path.read_bytes() == before


@pytest.mark.parametrize("name", ["Avatar.vtube.json", "Avatar.xyplugin.json", "items_pinned_to_model.json", "parameters.json"])
@pytest.mark.parametrize("raw", [b"not JSON", b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":1e999}', b"\xff", b"[]"])
def test_live2d_tool_settings_require_valid_json_objects(tmp_path, name, raw):
    """A recognized filename cannot admit binary payloads or malformed JSON."""
    files = live2d_files()
    files[name] = raw
    with pytest.raises(ModelFormatError, match="invalid_model_structure") as error:
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))
    assert error.value.path == name


@pytest.mark.parametrize("budget", ["bytes", "depth", "nodes"])
@pytest.mark.parametrize("name", ["Avatar.vtube.json", "Avatar.xyplugin.json", "parameters.json"])
def test_live2d_tool_settings_use_existing_json_limits(tmp_path, budget, name):
    files = live2d_files()
    if budget == "bytes":
        value, limits, code = {"note": "x" * 600}, FormatLimits(json_bytes=512), "model_json_too_large"
    elif budget == "depth":
        value = {}
        for _ in range(12):
            value = {"nested": value}
        limits, code = FormatLimits(json_depth=8), "model_json_too_complex"
    else:
        value, limits, code = {"values": list(range(200))}, FormatLimits(json_nodes=100), "model_json_too_complex"
    files[name] = json.dumps(value).encode()
    with pytest.raises(ModelFormatError, match=code) as error:
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files), format_limits=limits)
    assert error.value.path == name


@pytest.mark.parametrize("name", ["not_items_pinned_to_model.json", "Avatar.vtube.json.exe", "unknown.json",
                                "Avatar.xyplugin.json.exe", "other_parameters.json", "parameters.json.exe"])
def test_live2d_tool_settings_do_not_allow_other_payload_names(tmp_path, name):
    files = live2d_files()
    files[name] = b"{}"
    with pytest.raises(ModelFormatError, match="unsupported_model_resource") as error:
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))
    assert error.value.path == name


@pytest.mark.parametrize("kind", ["vrm", "mmd"])
@pytest.mark.parametrize("name", ["Avatar.xyplugin.json", "items_pinned_to_model.json", "parameters.json"])
def test_tool_settings_exception_is_limited_to_live2d(tmp_path, kind, name):
    """Compatibility for Live2D must not relax other formats' resource contracts."""
    entry = "Avatar.vrm" if kind == "vrm" else "Models/Avatar.pmx"
    files = {entry: vrm_bytes()} if kind == "vrm" else {
        entry: pmx_bytes(), "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture",
    }
    files[name] = b"{}"
    with pytest.raises(ModelFormatError, match="unsupported_model_resource"):
        validate_model_bundle(make_native_bundle(tmp_path, kind=kind, entry=entry, files=files))


@pytest.mark.parametrize("reference", [
    "../textures/face.png", "/textures/face.png", "https://other.invalid/face.png",
    "textures/Face.png", "textures/face%2Epng", "textures/face.png#hidden", "textures/face.png?q=1",
])
def test_rejects_reference_outside_exact_runtime_target(tmp_path, reference):
    with pytest.raises(ModelFormatError):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=live2d_files(reference=reference)))


@pytest.mark.parametrize("reference", ["../other/shared.exp3.json", "static/other/shared.exp3.json"])
def test_emotion_mapping_cannot_escape_via_basename_filter(tmp_path, reference):
    files = live2d_files(mapping={"expressions": {"existing_group": [reference]}})
    # Even a matching basename cannot legitimize a different runtime URL.
    files["other/shared.exp3.json"] = files["expressions/test.exp3.json"]
    files["static/other/shared.exp3.json"] = files["expressions/test.exp3.json"]
    with pytest.raises(ModelFormatError):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("path", ["motions/test.motion3.json", "expressions/test.exp3.json"])
def test_unreferenced_scanned_animation_is_still_validated(tmp_path, path):
    files = live2d_files()
    files[path] = b"not JSON"
    with pytest.raises(ModelFormatError) as error:
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))
    assert error.value.path == path


@pytest.mark.parametrize("change", ["segments", "curve_count", "nonfinite", "bad_target"])
def test_rejects_malformed_motion_layout(tmp_path, change):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    if change == "segments":
        motion["Curves"][0]["Segments"].pop()
    elif change == "curve_count":
        motion["Meta"]["CurveCount"] = 2
    elif change == "bad_target":
        motion["Curves"][0]["Target"] = []
    else:
        motion["Curves"][0]["Segments"][1] = float("inf")
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    with pytest.raises(ModelFormatError):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("validator", [validate_live2d_bundle, validate_model_bundle])
def test_nonstandard_motion_reports_compatibility_without_rewriting(tmp_path, validator):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    motion["Curves"][0]["Target"] = "2422911982"
    motion["Meta"].update(TotalSegmentCount=2, TotalPointCount=1)
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validator(path)
    assert result.compatibility_warning_count == 2
    assert tuple(item.as_dict() for item in result.compatibility_warnings) == (
        {"code": "live2d_motion_count_mismatch", "path": "motions/test.motion3.json"},
        {"code": "live2d_unknown_motion_target", "path": "motions/test.motion3.json"},
    )
    assert path.read_bytes() == before


@pytest.mark.parametrize("validator", [validate_live2d_bundle, validate_model_bundle])
@pytest.mark.parametrize("position", [0, 1])
def test_empty_curve_in_an_otherwise_playable_motion_reports_compatibility(tmp_path, validator, position):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    motion["Curves"].insert(position, {"Target": "Parameter", "Id": "ParamMouthForm", "Segments": []})
    motion["Meta"]["CurveCount"] = 2
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validator(path)
    assert result.compatibility_warning_count == 1
    assert tuple(item.as_dict() for item in result.compatibility_warnings) == (
        {"code": "live2d_empty_motion_curve", "path": "motions/test.motion3.json"},
    )
    assert path.read_bytes() == before


def test_motion_with_only_empty_curves_still_fails_without_a_fallback_point(tmp_path):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    motion["Curves"][0]["Segments"] = []
    motion["Meta"].update(TotalSegmentCount=0, TotalPointCount=0)
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    with pytest.raises(ModelFormatError, match="invalid_model_structure"):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("change", ["underallocated_segments", "oversized_segments", "oversized_points",
                                   "invalid_points", "empty_target", "numeric_target", "empty_segments", "bad_opcode"])
def test_motion_compatibility_does_not_admit_unsafe_layouts(tmp_path, change):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    if change == "underallocated_segments":
        motion["Meta"]["TotalSegmentCount"] = 0
    elif change == "oversized_segments":
        motion["Meta"]["TotalSegmentCount"] = 10 ** 12
    elif change == "oversized_points":
        motion["Meta"]["TotalPointCount"] = 10 ** 12
    elif change == "invalid_points":
        motion["Meta"]["TotalPointCount"] = True
    elif change == "empty_target":
        motion["Curves"][0]["Target"] = ""
    elif change == "numeric_target":
        motion["Curves"][0]["Target"] = 2422911982
    elif change == "empty_segments":
        motion["Curves"][0]["Segments"] = [0, 0]
    else:
        motion["Curves"][0]["Segments"][2] = 9
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


def test_motion_compatibility_report_is_bounded_and_keeps_total(tmp_path):
    files = live2d_files()
    motion = json.loads(files.pop("motions/test.motion3.json"))
    motion["Curves"][0]["Target"] = "CustomTarget"
    for index in range(101):
        files[f"motions/{index:03}.motion3.json"] = json.dumps(motion).encode()
    result = validate_model_bundle(make_native_bundle(tmp_path, files=files))
    assert result.compatibility_warning_count == 101
    assert len(tuple(item.as_dict() for item in result.compatibility_warnings)) == 100
    assert tuple(item.as_dict() for item in result.compatibility_warnings)[-1]["path"] == "motions/099.motion3.json"




@pytest.mark.parametrize("validator", [validate_live2d_bundle, validate_model_bundle])
def test_null_display_info_is_unset_and_resource_bytes_are_preserved(tmp_path, validator):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["DisplayInfo"] = None
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validator(path)
    assert result.model_format == "live2d" and not result.compatibility_warning_count
    assert path.read_bytes() == before
    with zipfile.ZipFile(path) as archive:
        assert archive.read("model/Avatar.model3.json") == files["Avatar.model3.json"]


@pytest.mark.parametrize("reference", [False, 0, [], {}, "../info.cdi3.json",
                                     "/info.cdi3.json", "https://other.invalid/info.cdi3.json"])
def test_display_info_values_still_require_a_valid_internal_reference(tmp_path, reference):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["DisplayInfo"] = reference
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("contents", [b"not JSON", b"{}", b'{"Version":3,"Parameters":"invalid"}'])
def test_display_info_file_contents_remain_validated(tmp_path, contents):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["DisplayInfo"] = "info.cdi3.json"
    files["Avatar.model3.json"] = json.dumps(model).encode()
    files["info.cdi3.json"] = contents
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("field", ["Physics", "Pose", "DisplayInfo", "UserData", "Motions", "Expressions", "Sound", "mapping"])
def test_missing_optional_effect_warns_without_changing_the_package(tmp_path, field):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    missing = "missing.json" if field != "Sound" else "missing.mp3"
    refs = model["FileReferences"]
    if field == "Motions":
        refs[field] = {"Tap": [{"File": missing}]}
    elif field == "Expressions":
        refs[field] = [{"Name": "Smile", "File": missing}]
    elif field == "Sound":
        refs["Motions"] = {"Tap": [{"File": "motions/test.motion3.json", "Sound": missing}]}
    elif field == "mapping":
        model["EmotionMapping"] = {"motions": {"happy": ["missing.motion3.json"]}}
        missing = "missing.motion3.json"
    else:
        refs[field] = missing
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_model_bundle(path)
    assert tuple(item.as_dict() for item in result.compatibility_warnings) == ({"code": "live2d_optional_resource_unavailable", "path": missing},)
    assert path.read_bytes() == before


@pytest.mark.parametrize("file_value", ["absent", None, ""])
def test_command_only_motion_entries_are_preserved_with_one_compatibility_notice(tmp_path, file_value):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    command = {"Name": "init", "PostCommand": "parameters lock touch_drag1 $drag1", "VarFloats": [{"Code": "init 0"}]}
    if file_value != "absent":
        command["File"] = file_value
    model["FileReferences"]["Motions"] = {"init": [command, {"Name": "menu", "Choices": []}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_model_bundle(path)
    assert tuple(item.as_dict() for item in result.compatibility_warnings) == ({"code": "live2d_unsupported_tool_effect", "path": "Avatar.model3.json"},)
    assert path.read_bytes() == before


@pytest.mark.parametrize("physics", [{"File": "missing.json"}, {"File": "physics.json"}, {}, None, ""])
def test_third_party_physics_config_is_safe_data_and_reports_unsupported_effects(tmp_path, physics):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["PhysicsV2"] = physics
    files["Avatar.model3.json"] = json.dumps(model).encode()
    files["physics.json"] = b'{"Version":3,"Meta":{"PhysicsSettingCount":0},"PhysicsSettings":[]}'
    if not isinstance(physics, dict) or physics.get("File") != "physics.json":
        files.pop("physics.json")
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_model_bundle(path)
    expected = []
    if physics == {"File": "missing.json"}:
        expected.append({"code": "live2d_optional_resource_unavailable", "path": "missing.json"})
    if isinstance(physics, dict):
        expected.append({"code": "live2d_unsupported_tool_effect", "path": "Avatar.model3.json"})
    assert list(tuple(item.as_dict() for item in result.compatibility_warnings)) == expected
    assert path.read_bytes() == before


@pytest.mark.parametrize("reference", [False, 0, [], {}, "../outside.json", "%2e%2e/outside.json",
                                     "https://other.invalid/a.json", "/a.json", "payload.exe"])
@pytest.mark.parametrize("field", ["PhysicsV2", "Motions"])
def test_tool_effect_references_still_require_safe_local_resource_types(tmp_path, reference, field):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"][field] = {"File": reference} if field == "PhysicsV2" else {"init": [{"File": reference}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("raw", [b'{"customPhysics":{"url":"https://other.invalid/ignored","command":"ignored"}}',
                               b'not JSON', b'[]', b'{"x":1,"x":2}', b'{"x":NaN}'])
def test_physics_v2_companion_is_bounded_opaque_json(tmp_path, raw):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["PhysicsV2"] = {"File": "third-party.json"}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    files["third-party.json"] = raw + b' ' * 512
    path = make_native_bundle(tmp_path, files=files)
    if raw.startswith(b'{"customPhysics"'):
        assert validate_model_bundle(path).compatibility_warning_count == 1
        with pytest.raises(ModelFormatError, match="model_json_too_large") as error:
            validate_model_bundle(path, format_limits=FormatLimits(json_bytes=len(files["third-party.json"]) - 1))
        assert error.value.path == "third-party.json"
    else:
        with pytest.raises(ModelFormatError):
            validate_model_bundle(path)


def test_command_only_motion_still_checks_its_sound_reference(tmp_path):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Motions"] = {"init": [{"PostCommand": "ignored", "Sound": "https://other.invalid/voice.wav"}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("value", [False, 0, [], "file.json"])
def test_physics_v2_container_cannot_be_a_scalar_or_array(tmp_path, value):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["PhysicsV2"] = value
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


def test_unknown_reference_extensions_are_not_blanket_allowed(tmp_path):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["UnknownPlugin"] = {"File": "missing.json"}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError, match="unsupported_model_reference"):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("reference", ["motions/missing.motion3.json?v=2", "motions/missing.motion3.json#preview"])
def test_missing_optional_motion_with_query_or_fragment_warns(tmp_path, reference):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Motions"] = {"Idle": [{"File": reference}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    result = validate_model_bundle(make_native_bundle(tmp_path, files=files))
    assert tuple(item.as_dict() for item in result.compatibility_warnings) == (
        {"code": "live2d_optional_resource_unavailable", "path": reference},
    )


@pytest.mark.parametrize("reference", ["motions/Idle#2.json", "motions/Idle%232.json"])
def test_optional_motion_with_a_safe_but_incompatible_url_warns(tmp_path, reference):
    files = live2d_files()
    files[reference] = files.pop("motions/test.motion3.json")
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Motions"] = {"Idle": [{"File": reference}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    result = validate_model_bundle(make_native_bundle(tmp_path, files=files))
    assert tuple(item.as_dict() for item in result.compatibility_warnings) == ({"code": "live2d_optional_resource_unavailable", "path": reference},)


@pytest.mark.parametrize("reference", ["../outside.json", "%2e%2e/outside.json", "x/%2e%2e/%2e%2e/outside.json",
                                     "https://other.invalid/a.json", "//other.invalid/a.json", "/a.json",
                                     "x#hidden/../../outside.json", "x\x00.json", "script.js", "missing.json.exe"])
def test_optional_effects_cannot_escape_or_admit_payloads(tmp_path, reference):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Physics"] = reference
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("malformed", [False, True])
def test_referenced_generic_json_motion_is_checked_by_content(tmp_path, malformed):
    files = live2d_files()
    files["Motions_Tap_0.json"] = files.pop("motions/test.motion3.json")
    if malformed:
        files["Motions_Tap_0.json"] = b'{}'
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Motions"] = {"Tap": [{"File": "Motions_Tap_0.json", "PostCommand": "do not execute"}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    if malformed:
        with pytest.raises(ModelFormatError):
            validate_model_bundle(path)
    else:
        assert validate_model_bundle(path).compatibility_warning_count == 0


@pytest.mark.parametrize("name", ["physics3.json", "Physics.json"])
def test_generic_optional_physics_uses_the_physics_schema(tmp_path, name):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    # The bare standard basename is recognizable even when an exporter points elsewhere.
    model["FileReferences"]["Physics"] = "Physics.json"
    files["Avatar.model3.json"] = json.dumps(model).encode()
    files[name] = b'{"Version":3,"Meta":{"PhysicsSettingCount":0},"PhysicsSettings":[]}'
    assert validate_model_bundle(make_native_bundle(tmp_path, files=files)).model_format == "live2d"
    files[name] = b'{"Version":3}'
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize(("extension", "header"), [("mp3", b'\xff\xfb\xb0\x04'), ("mp3", b'ID3'),
                                                  ("wav", b'RIFF\x00\x00\x00\x00WAVE'), ("ogg", b'OggS\x00')])
def test_motion_audio_is_preserved_as_a_local_resource(tmp_path, extension, header):
    files = live2d_files()
    name = "voice." + extension
    files[name] = header + b'\x00' * 24
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Motions"] = {"Tap": [{"File": "motions/test.motion3.json", "Sound": name}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    assert validate_model_bundle(path).compatibility_warning_count == 0
    assert path.read_bytes() == before
    files[name] = b'<script>not audio</script>'
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("field", ["Moc", "Textures"])
def test_null_display_info_exception_does_not_relax_other_references(tmp_path, field):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"].update(DisplayInfo=None)
    model["FileReferences"][field] = None
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))




@pytest.mark.parametrize("name", ["parameters.json", "settings/PARAMETERS.JSON"])
def test_neko_model_parameters_are_preserved_without_interpreting_keys(tmp_path, name):
    files = live2d_files()
    files[name] = b'{"param_0":1,"ParamAngleX":-2.5,"custom/id":0}'
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_model_bundle(path)
    assert name in {item.path for item in result.files}
    assert path.read_bytes() == before
    assert result.compatibility_warning_count == 0


@pytest.mark.parametrize("value", [True, None, "1", "../outside.png", [], {}, float("inf")])
def test_neko_model_parameters_require_finite_numeric_values(tmp_path, value):
    files = live2d_files()
    files["parameters.json"] = json.dumps({"param_0": value}).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("field", ["Physics", "Pose", "DisplayInfo", "UserData"])
@pytest.mark.parametrize("empty", [None, ""])
def test_empty_optional_live2d_resources_are_unset(tmp_path, field, empty):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"][field] = empty
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    before = path.read_bytes()
    result = validate_model_bundle(path)
    assert result.model_format == "live2d" and path.read_bytes() == before


@pytest.mark.parametrize("field", ["Physics", "Pose", "DisplayInfo", "UserData"])
@pytest.mark.parametrize("value", [False, 0, [], {}])
def test_optional_resource_compatibility_does_not_accept_wrong_types(tmp_path, field, value):
    files = live2d_files()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"][field] = value
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("validator", [validate_live2d_bundle, validate_model_bundle])
def test_optional_motion_loop_can_be_omitted(tmp_path, validator):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    del motion["Meta"]["Loop"]
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    assert validator(make_native_bundle(tmp_path, files=files)).compatibility_warning_count == 0



def test_rejects_duplicate_native_json_fields(tmp_path):
    files = live2d_files()
    files["expressions/test.exp3.json"] = b'{"Type":"Live2D Expression","Parameters":[],"Parameters":[]}'
    with pytest.raises(ModelFormatError):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("extra", ["role.chara.json", "api_keys.json", "voice.wav", "payload.exe"])
def test_rejects_non_model_payloads_without_silent_removal(tmp_path, extra):
    files = live2d_files()
    files[extra] = b"unsupported"
    with pytest.raises(ModelFormatError, match="unsupported_model_resource"):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


def test_rejects_executable_disguised_as_texture(tmp_path):
    files = live2d_files()
    files["textures/face.png"] = b"MZ executable payload"
    with pytest.raises(ModelFormatError, match="invalid_model_image"):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


def test_accepts_pose_shape_used_by_bundled_model(tmp_path):
    """The real bundled pose has a Type discriminator and no Version field."""
    files = live2d_files()
    files["Avatar.pose3.json"] = json.dumps({"Type": "Live2D Pose", "Groups": [[{"Id": "ExistingPart"}]]}).encode()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Pose"] = "Avatar.pose3.json"
    files["Avatar.model3.json"] = json.dumps(model).encode()
    assert validate_live2d_bundle(make_native_bundle(tmp_path, files=files)).model_format == "live2d"


def vrm_bytes(*, version="1.0", change=None):
    """Minimal GLB dependency graph; this does not certify a renderable humanoid."""
    kind = "VRM" if version == "0.0" else "VRMC_vrm"
    bones = [{"bone": "hips", "node": 0}] if version == "0.0" else {"hips": {"node": 0}}
    data = {
        "asset": {"version": "2.0"}, "buffers": [{"byteLength": 4}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 4}],
        "nodes": [{}], "scenes": [{"nodes": [0]}], "scene": 0, "extensionsUsed": [kind],
        "extensions": {kind: {"specVersion": version, "meta": {"name": "Fixture", "authors": ["Fixture"]},
                              "humanoid": {"humanBones": bones}}},
    }
    if change:
        change(data)
    raw = json.dumps(data, separators=(",", ":")).encode()
    raw += b" " * (-len(raw) % 4)
    return struct.pack("<4s4I", b"glTF", 2, 28 + len(raw) + 4, len(raw), 0x4E4F534A) + raw + struct.pack("<2I", 4, 0x004E4942) + b"BIN!"


@pytest.mark.parametrize("version", ["0.0", "1.0", "1.0-beta"])
def test_accepts_supported_vrm_extensions_and_preserves_bytes(tmp_path, version):
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(version=version)})
    before = path.read_bytes()
    assert validate_vrm_bundle(path).model_format == "vrm"
    assert path.read_bytes() == before


def vrm_graph(data):
    """Independent loader dependency fixture: texture, animation, skin and accessors."""
    matrix = struct.pack("<16f", 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1)
    data["buffers"].append({"byteLength": 64, "uri": "data:application/octet-stream;base64," + base64.b64encode(matrix).decode()})
    data["bufferViews"].append({"buffer": 1, "byteLength": 64})
    data["images"] = [{"uri": "data:image/png;base64,YQ=="}]
    data["textures"] = [{"source": 0}]
    data["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
    data["accessors"] = [{"bufferView": 0, "componentType": 5126, "count": 1, "type": "SCALAR"},
                         {"bufferView": 1, "componentType": 5126, "count": 1, "type": "VEC3"},
                         {"bufferView": 1, "componentType": 5126, "count": 1, "type": "MAT4"}]
    data["skins"] = [{"joints": [0], "inverseBindMatrices": 2}]
    data["nodes"][0]["skin"] = 0
    data["animations"] = [{"samplers": [{"input": 0, "output": 1}],
                           "channels": [{"sampler": 0, "target": {"node": 0, "path": "translation"}}]}]


@pytest.mark.parametrize("reference", ["tool.vtube.json#x.json", "tool.vtube.json?x.json", "tool%2Evtube.json", "TOOL.vtube.json"])
@pytest.mark.parametrize("valid_motion", [False, True])
def test_optional_alias_checks_the_resource_the_loader_can_actually_open(tmp_path, reference, valid_motion):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    if not valid_motion:
        motion["Meta"]["TotalSegmentCount"] = 400_000_000
    files["tool.vtube.json"] = json.dumps(motion).encode()
    model = json.loads(files["Avatar.model3.json"])
    model["FileReferences"]["Motions"] = {"Idle": [{"File": reference}]}
    files["Avatar.model3.json"] = json.dumps(model).encode()
    path = make_native_bundle(tmp_path, files=files)
    if valid_motion:
        assert validate_model_bundle(path).compatibility_warning_count == 1
    else:
        with pytest.raises(ModelFormatError):
            validate_model_bundle(path)


def test_emotion_mapping_cannot_disguise_an_unsafe_motion_as_tool_data(tmp_path):
    files = live2d_files(mapping={"motions": {"happy": ["tool.vtube.json#x.motion3.json"]}})
    motion = json.loads(files["motions/test.motion3.json"])
    motion["Meta"]["TotalSegmentCount"] = 400_000_000
    files["tool.vtube.json"] = json.dumps(motion).encode()
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("count,events,accepted", [(300_000_000, [], False), (1, [], False),
    (True, [], False), (-1, [], False), (1, [None], False), (1, [{"Time": 0, "Value": 1}], False),
    (1, [{"Time": "0", "Value": "hello"}], False), (1, [{"Time": 0, "Value": "hello"}], True), (0, [], True)])
def test_motion_events_bound_the_exact_array_the_loader_allocates(tmp_path, count, events, accepted):
    files = live2d_files()
    motion = json.loads(files["motions/test.motion3.json"])
    motion["Meta"]["UserDataCount"] = count
    motion["UserData"] = events
    files["motions/test.motion3.json"] = json.dumps(motion).encode()
    path = make_native_bundle(tmp_path, files=files)
    if accepted:
        validate_model_bundle(path)
    else:
        with pytest.raises(ModelFormatError):
            validate_model_bundle(path)


@pytest.mark.parametrize("change", ["material", "base_texture", "normal_texture", "mtoon_texture", "morph_count", "stride_tail", "sparse_indices", "sparse_values"])
def test_vrm_rejects_resources_the_bundled_loader_cannot_read(tmp_path, change):
    def graph(data):
        vrm_graph(data)
        data["meshes"] = [{"primitives": [{"attributes": {"POSITION": 1}, "material": 0}]}]
        if change == "material":
            data["meshes"][0]["primitives"][0]["material"] = 1
        elif change == "base_texture":
            data["materials"][0]["pbrMetallicRoughness"]["baseColorTexture"]["index"] = 1
        elif change == "normal_texture":
            data["materials"][0]["normalTexture"] = {"index": 1}
        elif change == "mtoon_texture":
            data["materials"][0]["extensions"] = {"VRMC_materials_mtoon": {"shadeMultiplyTexture": {"index": 1}}}
        elif change == "morph_count":
            data["meshes"][0]["primitives"].append({"attributes": {"POSITION": 1}, "targets": [{"POSITION": 1}]})
            data.pop("animations")
        elif change == "stride_tail":
            data["bufferViews"][1].update(byteStride=16, byteLength=28)
            data["accessors"] = [{"bufferView": 1, "componentType": 5126, "count": 2, "type": "VEC3"}]
            data.pop("animations"); data.pop("skins"); data["nodes"][0].pop("skin")
            data["meshes"][0]["primitives"][0]["attributes"]["POSITION"] = 0
        else:
            data["accessors"][1]["sparse"] = {"count": 1, "indices": {"bufferView": 1, "componentType": 5123}, "values": {"bufferView": 1}}
            key = "indices" if change == "sparse_indices" else "values"
            data["accessors"][1]["sparse"][key]["byteOffset"] = 1
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError):
        validate_model_bundle(path)


@pytest.mark.parametrize("change", [
    lambda d: d["textures"][0].update(source=1),
    lambda d: d["textures"][0].update(source=True),
    lambda d: d["textures"][0].update(extensions={"EXT_texture_webp": {"source": 1}}),
    lambda d: d["textures"][0].update(extensions={"EXT_texture_avif": {"source": 1}}),
    lambda d: d["animations"][0]["samplers"][0].update(input=3),
    lambda d: d["animations"][0]["samplers"][0].update(output=3),
    lambda d: d["animations"][0]["channels"][0].update(sampler=1),
    lambda d: d["animations"][0]["channels"][0]["target"].update(node=1),
    lambda d: d["skins"][0].update(inverseBindMatrices=3),
    lambda d: d["skins"][0].update(inverseBindMatrices=0),
    lambda d: d["accessors"][2].update(componentType=5123),
    lambda d: (d["nodes"].append({}), d["skins"][0].update(joints=[0, 1])),
])
def test_vrm_loading_dependencies_reject_invalid_indices_and_skin_shape(tmp_path, change):
    """Out-of-range texture/animation/skin data must fail before the renderer loads it."""
    def graph(data):
        vrm_graph(data)
        change(data)
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError):
        validate_vrm_bundle(path)


def test_vrm_accepts_valid_loading_dependencies_and_zero_initialized_accessor(tmp_path):
    """Zero values need neither a view nor sparse overrides in glTF 2.0."""
    def graph(data):
        vrm_graph(data)
        data["accessors"][1].pop("bufferView")
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    before = path.read_bytes()
    assert validate_vrm_bundle(path).model_format == "vrm"
    assert path.read_bytes() == before


def sparse_accessors(data, *, count=2_000_000_000, copies=1, backed=False):
    """A tiny encoded model may request a huge zero/sparse backing allocation."""
    data["buffers"].append({"byteLength": 64, "uri": "data:application/octet-stream;base64," + base64.b64encode(bytes(64)).decode()})
    data["bufferViews"].extend([{"buffer": 1, "byteLength": 64, "byteStride": 16},
        {"buffer": 1, "byteLength": 1}, {"buffer": 1, "byteOffset": 4, "byteLength": 12}])
    accessor = {"componentType": 5126, "count": count, "type": "VEC3",
                "sparse": {"count": 1, "indices": {"bufferView": 2, "componentType": 5121},
                           "values": {"bufferView": 3}}}
    if backed:
        accessor["bufferView"] = 1
        data["bufferViews"][1]["byteStride"] = 12
    data["accessors"] = [dict(accessor) for _ in range(copies)]


@pytest.mark.parametrize("count,copies,backed,budget", [(2_000_000_000, 1, False, None),
    (4, 2, False, 145), (4, 2, True, 176)])
def test_vrm_sparse_allocations_are_bounded_individually_and_in_total(tmp_path, count, copies, backed, budget):
    """Count bounds actual zero-filled arrays and packed sparse buffer clones."""
    raw = vrm_bytes(change=lambda data: sparse_accessors(data, count=count, copies=copies, backed=backed))
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": raw})
    options = {} if budget is None else {"format_limits": FormatLimits(vrm_accessor_bytes=budget)}
    with pytest.raises(ModelFormatError, match="model_accessor_budget_exceeded"):
        validate_vrm_bundle(path, **options)


def test_vrm_zero_accessor_allocation_accepts_the_exact_budget(tmp_path):
    """The budget is a byte limit, not a rejection of legitimate zero-filled data."""
    raw = vrm_bytes(change=lambda data: data.update(bufferViews=[], accessors=[{"componentType": 5126, "count": 1, "type": "MAT4"}]))
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": raw})
    validate_vrm_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=64))


@pytest.mark.parametrize("shape", ["MAT3", "MAT4"])
def test_vrm_rejects_sparse_matrix_shapes_the_bundled_loader_cannot_apply(tmp_path, shape):
    """Valid glTF data still cannot use a sparse shape rejected by this loader."""
    def matrix(data):
        sparse_accessors(data, count=1)
        data["accessors"][0]["type"] = shape
        data["bufferViews"][3] = {"buffer": 1, "byteLength": 64}
    raw = vrm_bytes(change=matrix)
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": raw})
    with pytest.raises(ModelFormatError, match="unsupported_model_structure"):
        validate_vrm_bundle(path)


@pytest.mark.parametrize("change", [
    lambda d: d["buffers"][0].update(uri="https://other.invalid/buffer.bin"),
    lambda d: d["buffers"][0].update(uri="file:///outside.bin"),
    lambda d: d.update(images=[{"uri": "texture.png"}]),
    lambda d: d.update(images=[{"uri": "https://other.invalid/texture.png"}]),
    lambda d: d["bufferViews"][0].update(byteLength=500),
    lambda d: d["bufferViews"][0].update(buffer=True),
    lambda d: d["nodes"][0].update(children=[0]),
    lambda d: d["nodes"][0].update(mesh=0),
    lambda d: d["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["hips"].update(node=4),
    lambda d: d["extensions"]["VRMC_vrm"].update(specVersion="future"),
    lambda d: d["extensions"].update(KHR_draco_mesh_compression={}),
    lambda d: d["extensions"].update(VRM={}),
    lambda d: d.update(accessors=[{"bufferView": 0, "componentType": 5126, "count": 3, "type": "VEC3"}]),
])
def test_rejects_vrm_external_or_inconsistent_structure(tmp_path, change):
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=change)})
    with pytest.raises(ModelFormatError):
        validate_vrm_bundle(path)


def test_accepts_bounded_inline_vrm_data(tmp_path):
    def inline(data):
        data["buffers"][0]["uri"] = "data:application/octet-stream;base64,QklOIQ=="
        data["images"] = [{"uri": "data:image/png;base64,iVBORw0KGgo="}]
        data["extensions"]["VRMC_vrm"]["meta"]["licenseUrl"] = "https://author.invalid/license"
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=inline)})
    assert validate_vrm_bundle(path).model_format == "vrm"


@pytest.mark.parametrize("damage", ["short_header", "wrong_total", "wrong_chunk"])
def test_rejects_invalid_glb_framing_even_with_valid_outer_hashes(tmp_path, damage):
    raw = bytearray(vrm_bytes())
    if damage == "short_header":
        raw = raw[:12]
    elif damage == "wrong_total":
        struct.pack_into("<I", raw, 8, len(raw) + 4)
    else:
        struct.pack_into("<I", raw, 16, 0x004E4942)
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": bytes(raw)})
    with pytest.raises(ModelFormatError):
        validate_vrm_bundle(path)


@pytest.mark.parametrize("missing,mode,accepted", [
    (None, 4, True), ("JOINTS_0", 4, False), ("WEIGHTS_0", 4, False), ("WEIGHTS_0", 1, True),
])
def test_skinned_vrm_requires_joint_and_weight_attributes(tmp_path, missing, mode, accepted):
    def graph(data):
        data["accessors"] = [
            {"componentType": 5126, "count": 3, "type": "VEC3"},
            {"componentType": 5121, "count": 3, "type": "VEC4"},
            {"componentType": 5126, "count": 3, "type": "VEC4"},
        ]
        attributes = {"POSITION": 0, "JOINTS_0": 1, "WEIGHTS_0": 2}
        if missing:
            del attributes[missing]
        data["meshes"] = [{"primitives": [{"attributes": attributes, "mode": mode}]}]
        data["nodes"][0].update(mesh=0, skin=0)
        data["skins"] = [{"joints": [0]}]

    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    if not accepted:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_vrm_bundle(path)
    else:
        assert validate_vrm_bundle(path).model_format == "vrm"


def test_vrm_skin_weight_budget_counts_each_skinned_triangle_primitive(tmp_path):
    def graph(data):
        data["accessors"] = [
            {"componentType": 5126, "count": 3, "type": "VEC3"},
            {"componentType": 5121, "count": 3, "type": "VEC4"},
            {"componentType": 5126, "count": 3, "type": "VEC4"},
        ]
        triangle = {"attributes": {"POSITION": 0, "JOINTS_0": 1, "WEIGHTS_0": 2}}
        line = {"attributes": {"POSITION": 0}, "mode": 1}
        data["meshes"] = [{"primitives": [triangle, triangle, line]}]
        data["nodes"][0].update(mesh=0, skin=0)
        data["skins"] = [{"joints": [0]}]

    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    assert validate_vrm_bundle(path, format_limits=FormatLimits(vrm_skin_weight_work=6)).model_format == "vrm"
    with pytest.raises(ModelFormatError, match="^model_accessor_budget_exceeded$"):
        validate_vrm_bundle(path, format_limits=FormatLimits(vrm_skin_weight_work=5))


def test_vrm_extra_documentation_is_rejected_instead_of_discarded(tmp_path):
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(), "LICENSE.txt": b"Read me"})
    with pytest.raises(ModelFormatError, match="unsupported_model_resource"):
        validate_vrm_bundle(path)


def pmx_bytes(texture="../Textures/face.png", *, shared=True, version=2.0, broken_weight=False, second_reference=None):
    """Build every PMX section, retaining the real texture/material byte layout."""
    def text(value):
        raw = value.encode("utf-8")
        return struct.pack("<i", len(raw)) + raw
    raw = b"PMX " + struct.pack("<f", version) + bytes([8, 1, 0, 1, 1, 1, 1, 1, 1])
    raw += text("Fixture") + text("") * 3
    raw += struct.pack("<i", 1 if broken_weight else 0)
    if broken_weight:
        raw += bytes(32) + bytes([99]) + bytes(5)
    textures = (texture,) if second_reference is None else (texture, second_reference)
    raw += struct.pack("<2i", 0, len(textures)) + b"".join(text(name) for name in textures)
    raw += struct.pack("<i", len(textures))
    for index in range(len(textures)):
        raw += text("Material") + text("") + bytes(65)
        raw += struct.pack("<bbBBB", index, -1, 0, 1 if shared else 0, 0)
        raw += text("") + struct.pack("<i", 0)
    # Bones, morphs, display frames, rigid bodies and joints are empty sections.
    raw += struct.pack("<5i", 0, 0, 0, 0, 0)
    if version > 2.05:
        raw += struct.pack("<i", 0)
    return raw


def pmd_bytes(texture="../Textures/face.png", *, toon=-1, toon_path=None, toon_paths=None):
    """PMD fixtures include optional English/toon/physics tail when requested."""
    def fixed(value, size):
        raw = value.encode("cp932")
        assert len(raw) <= size
        return raw.ljust(size, b"\x00")
    raw = b"Pmd" + struct.pack("<f", 1.0) + bytes(276)
    raw += struct.pack("<3I", 0, 0, 1) + bytes(44)
    raw += struct.pack("<bBI", toon, 0, 0) + fixed(texture, 20)
    raw += struct.pack("<3H2BI", 0, 0, 0, 0, 0, 0)
    if toon_path is not None or toon_paths is not None:
        paths = toon_paths if toon_paths is not None else [toon_path] + [""] * 9
        assert len(paths) == 10
        raw += b"\x00" + b"".join(fixed(value, 100) for value in paths) + struct.pack("<2I", 0, 0)
    return raw


@pytest.mark.parametrize("image_type,required", [(2, 16), (10, 28)])
def test_mmd_tga_decoded_storage_has_a_budget(tmp_path, image_type, required):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, image_type, 0, 0, 0, 0, 0, 2, 2, 24, 0)
    pixels = bytes(12) if image_type == 2 else b"\x83\x00\x00\x00"
    files = {"Avatar.pmx": pmx_bytes("face.tga"), "face.tga": header + pixels}
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files=files)
    validate_mmd_bundle(path, format_limits=FormatLimits(mmd_tga_bytes=required))
    with pytest.raises(ModelFormatError, match="^model_accessor_budget_exceeded$"):
        validate_mmd_bundle(path, format_limits=FormatLimits(mmd_tga_bytes=required - 1))


@pytest.mark.parametrize("second_reference,budget", [(None, 27), ("./face.tga", 55), ("other.tga", 55)])
def test_mmd_checks_total_tga_budget_before_reading_packets(tmp_path, monkeypatch, second_reference, budget):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 10, 0, 0, 0, 0, 0, 2, 2, 24, 0)
    image = header + b"\x83\x00\x00\x00"
    files = {
        "Avatar.pmx": pmx_bytes("face.tga", second_reference=second_reference),
        "face.tga": image,
    }
    if second_reference == "other.tga":
        files["other.tga"] = image
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files=files)
    packet_reads = []
    read = zipfile.ZipExtFile.read

    def track(handle, size=-1):
        if size == 1 and handle.name.endswith(".tga"):
            packet_reads.append(handle.name)
        return read(handle, size)

    monkeypatch.setattr(zipfile.ZipExtFile, "read", track)
    with pytest.raises(ModelFormatError, match="^model_accessor_budget_exceeded$"):
        validate_mmd_bundle(path, format_limits=FormatLimits(mmd_tga_bytes=budget))
    assert not packet_reads


def test_mmd_unused_tga_only_needs_a_valid_header(tmp_path):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 10, 0, 0, 0, 0, 0, 2, 2, 24, 0)
    files = {
        "Avatar.pmx": pmx_bytes("face.png"),
        "face.png": b"\x89PNG\r\n\x1a\nfixture",
        "unused.tga": header,
    }
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files=files)
    assert validate_mmd_bundle(path).model_format == "mmd"


@pytest.mark.parametrize("image_type,identifier,pixels,accepted", [
    (2, b"id", bytes(6), True),
    (2, b"id", bytes(5), False),
    (10, b"", b"\x81" + bytes(3), True),
    (10, b"", b"\x01" + bytes(6), True),
    (10, b"", b"\x82" + bytes(3), False),
    (10, b"", b"\x01" + bytes(5), False),
    (10, b"", b"\x81" + bytes(2), False),
    (10, b"", b"\x80" + bytes(3), False),
])
def test_mmd_tga_requires_complete_pixel_packets(tmp_path, image_type, identifier, pixels, accepted):
    header = struct.pack("<BBBHHBHHHHBB", len(identifier), 0, image_type, 0, 0, 0, 0, 0, 2, 1, 24, 0)
    files = {"Avatar.pmx": pmx_bytes("face.tga"), "face.tga": header + identifier + pixels}
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files=files)
    if accepted:
        assert validate_mmd_bundle(path).model_format == "mmd"
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_image$"):
            validate_mmd_bundle(path)


@pytest.mark.parametrize("extension", ["pmx", "pmd"])
def test_mmd_tga_cannot_be_used_as_a_custom_toon(tmp_path, extension):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 1, 1, 24, 0)
    entry = "Avatar." + extension
    model = pmx_bytes("ramp.tga", shared=False) if extension == "pmx" else pmd_bytes("", toon=0, toon_path="ramp.tga")
    files = {entry: model, "ramp.tga": header + bytes(3)}
    path = make_native_bundle(tmp_path, kind="mmd", entry=entry, files=files)
    with pytest.raises(ModelFormatError, match="^invalid_model_image$"):
        validate_mmd_bundle(path)


@pytest.mark.parametrize("bits,accepted", [(8, True), (24, False)])
def test_mmd_gray_tga_matches_the_loader_depth(tmp_path, bits, accepted):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 3, 0, 0, 0, 0, 0, 1, 1, bits, 0)
    files = {"Avatar.pmx": pmx_bytes("face.tga"), "face.tga": header + bytes(bits // 8)}
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files=files)
    if accepted:
        validate_mmd_bundle(path)
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_image$"):
            validate_mmd_bundle(path)


@pytest.mark.parametrize("reference,accepted", [("face.tga", True), ("./face.tga", False)])
def test_mmd_tga_budget_counts_distinct_texture_cache_keys(tmp_path, reference, accepted):
    header = struct.pack("<BBBHHBHHHHBB", 0, 0, 2, 0, 0, 0, 0, 0, 2, 2, 24, 0)
    files = {"Avatar.pmx": pmx_bytes("face.tga", second_reference=reference), "face.tga": header + bytes(12)}
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files=files)
    if accepted:
        validate_mmd_bundle(path, format_limits=FormatLimits(mmd_tga_bytes=16))
    else:
        with pytest.raises(ModelFormatError, match="^model_accessor_budget_exceeded$"):
            validate_mmd_bundle(path, format_limits=FormatLimits(mmd_tga_bytes=16))


@pytest.mark.parametrize("kind", ["pmx", "pmx21", "pmd"])
def test_accepts_nested_mmd_with_internal_parent_texture_reference(tmp_path, kind):
    main = "Models/Avatar.pmd" if kind == "pmd" else "Models/Avatar.pmx"
    model = pmd_bytes() if kind == "pmd" else pmx_bytes(version=2.1 if kind == "pmx21" else 2.0)
    files = {main: model, "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture", "LICENSE.txt": b"Test only"}
    path = make_native_bundle(tmp_path, kind="mmd", entry=main, files=files)
    before = path.read_bytes()
    assert validate_mmd_bundle(path).entrypoint == main
    assert path.read_bytes() == before


@pytest.mark.parametrize("kind", ["pmx", "pmd"])
@pytest.mark.parametrize("reference", ["../../face.png", "https://bad/x.png", "../Textures/Face.png", "../Textures/face.png#part", "../Textures/face%2Epng"])
def test_mmd_rejects_missing_escaping_and_runtime_url_mismatch(tmp_path, kind, reference):
    # PMD's fixed reference slot is only 20 bytes; use compact equivalent cases.
    if kind == "pmd":
        reference = reference.replace("../Textures/", "../T/").replace("https://bad/x.png", "http://x/a.png")
    main = "Models/Avatar." + kind
    raw = pmx_bytes(reference) if kind == "pmx" else pmd_bytes(reference)
    files = {main: raw, "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture", "T/face.png": b"\x89PNG\r\n\x1a\nfixture"}
    with pytest.raises(ModelFormatError):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


def test_pmx_custom_toon_filename_does_not_implicitly_become_shared(tmp_path):
    main = "Models/Avatar.pmx"
    files = {main: pmx_bytes("toon01.bmp", shared=False)}
    with pytest.raises(ModelFormatError, match="missing_model_resource"):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


def test_pmd_shared_toon_is_decided_by_its_converted_texture_table(tmp_path):
    main = "Models/Avatar.pmd"
    files = {main: pmd_bytes("", toon=0, toon_path="toon01.bmp")}
    assert validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files)).model_format == "mmd"
    files[main] = pmd_bytes("", toon=0, toon_path="custom.bmp")
    with pytest.raises(ModelFormatError, match="missing_model_resource"):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


@pytest.mark.parametrize("kind", ["pmx", "pmd"])
def test_rejects_truncated_mmd_after_material_dependency_section(tmp_path, kind):
    main = "Models/Avatar." + kind
    raw = pmx_bytes() if kind == "pmx" else pmd_bytes()
    files = {main: raw[:-1], "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture"}
    with pytest.raises(ModelFormatError):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


@pytest.mark.parametrize("reference", ["face.png", "face.sph"])
def test_pmd_duplicate_toon_keys_cannot_hide_the_loaders_external_texture(tmp_path, reference):
    """The converter uses map size, which can select an existing toon slot."""
    main = "Models/Avatar.pmd"
    paths = ["", "", "https://evil.invalid/x.png"] + [""] * 7
    files = {main: pmd_bytes(reference, toon_paths=paths), "Models/" + reference: b"\x89PNG\r\n\x1a\nfixture"}
    with pytest.raises(ModelFormatError):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


def test_pmd_validates_the_actual_converted_slot_instead_of_the_appended_path(tmp_path):
    """Keep loader-compatible local paths, even when a nominal diffuse path is unused."""
    main = "Models/Avatar.pmd"
    paths = ["", "", "../Textures/loaded.png"] + [""] * 7
    files = {main: pmd_bytes("missing.png", toon_paths=paths), "Textures/loaded.png": b"\x89PNG\r\n\x1a\nfixture"}
    validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


def test_pmx_unknown_skin_weight_structure_is_not_skipped(tmp_path):
    main = "Models/Avatar.pmx"
    files = {main: pmx_bytes(broken_weight=True), "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture"}
    with pytest.raises(ModelFormatError):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


def test_mmd_independent_animation_attachment_is_not_silently_dropped(tmp_path):
    main = "Models/Avatar.pmx"
    files = {main: pmx_bytes(), "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture", "motion.vmd": b"unsupported"}
    with pytest.raises(ModelFormatError, match="unsupported_model_resource"):
        validate_mmd_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main, files=files))


@pytest.mark.parametrize("kind", ["live2d", "vrm", "mmd"])
def test_common_admission_dispatches_all_three_formats(tmp_path, kind):
    if kind == "live2d":
        files, entry = live2d_files(), "Avatar.model3.json"
    elif kind == "vrm":
        files, entry = {"Avatar.vrm": vrm_bytes()}, "Avatar.vrm"
    else:
        entry = "Models/Avatar.pmx"
        files = {entry: pmx_bytes(), "Textures/face.png": b"\x89PNG\r\n\x1a\nfixture"}
    assert validate_model_bundle(make_native_bundle(tmp_path, kind=kind, entry=entry, files=files)).model_format == kind


@pytest.mark.parametrize(("original", "renamed", "field"), [
    ("motions/test.motion3.json", "motions/test.MOTION3.JSON", "Motions"),
    ("expressions/test.exp3.json", "expressions/test.EXP3.JSON", "Expressions"),
])
def test_referenced_uppercase_animation_suffix_does_not_skip_structure_check(tmp_path, original, renamed, field):
    """Referenced assets load by exact URL even if recursive scanning omits them."""
    files = live2d_files()
    files.pop(original)
    files[renamed] = b"not JSON"
    model = json.loads(files["Avatar.model3.json"])
    reference = {"File": renamed, "Name": "Existing expression"}
    model["FileReferences"][field] = {"Existing group": [reference]} if field == "Motions" else [reference]
    files["Avatar.model3.json"] = json.dumps(model).encode()
    with pytest.raises(ModelFormatError):
        validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


def test_vrm_overlapping_buffer_views_cannot_multiply_decoded_memory(tmp_path):
    """A small shared buffer is copied once per distinct view by the bundled loader."""
    def graph(data):
        raw = bytes(64 * 1024)
        data['buffers'].append({'byteLength': len(raw), 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(raw).decode()})
        data['bufferViews'].extend({'buffer': 1, 'byteLength': len(raw)} for _ in range(4097))
        data['accessors'] = [{'bufferView': i + 1, 'componentType': 5126, 'type': 'SCALAR', 'count': 1} for i in range(4097)]
        data['meshes'] = [{'primitives': [{'attributes': {'_VIEW_' + str(i): i for i in range(4097)}}]}]
        data['nodes'][0]['mesh'] = 0
    raw = vrm_bytes(change=graph)
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': raw})
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
        validate_vrm_bundle(path)


def test_vrm_allows_overlapping_views_inside_the_copy_budget(tmp_path):
    raw = vrm_bytes(change=lambda data: data['bufferViews'].extend([{'buffer': 0, 'byteLength': 4}] * 2))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': raw})
    validate_vrm_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=12))


@pytest.mark.parametrize('budget,accepted', [(67, False), (68, True)])
def test_vrm_view_copies_and_zero_arrays_share_one_budget(tmp_path, budget, accepted):
    raw = vrm_bytes(change=lambda data: data.update(accessors=[{'componentType': 5126, 'type': 'MAT4', 'count': 1}]))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': raw})
    if accepted:
        validate_vrm_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=budget))
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_vrm_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=budget))


def vrm_animation_output(data, target, shape, component, normalized, count, keys, interpolation):
    """Animation cardinality fixture with separately valid morph geometry."""
    vrm_graph(data)
    for index in (0, 1):
        data['accessors'][index].pop('bufferView')
    data['accessors'][0]['count'] = keys
    data['accessors'][1].update(type=shape, componentType=component, normalized=normalized, count=count)
    data['animations'][0]['samplers'][0]['interpolation'] = interpolation
    data['animations'][0]['channels'][0]['target']['path'] = target
    if target == 'weights':
        data['accessors'].extend([
            {'componentType': 5126, 'count': 1, 'type': 'VEC3'},
            {'componentType': 5121, 'count': 1, 'type': 'VEC4'},
            {'componentType': 5126, 'count': 1, 'type': 'VEC4'},
        ])
        data['meshes'] = [{'primitives': [{
            'attributes': {'POSITION': 3, 'JOINTS_0': 4, 'WEIGHTS_0': 5},
            'targets': [{'POSITION': 3}, {'POSITION': 3}],
        }]}]
        data['nodes'][0]['mesh'] = 0


@pytest.mark.parametrize('options', [
    ('translation', 'SCALAR', 5126, False, 1, 1, 'LINEAR'),
    ('scale', 'VEC4', 5126, False, 1, 1, 'LINEAR'),
    ('rotation', 'VEC3', 5126, False, 1, 1, 'LINEAR'),
    ('translation', 'VEC3', 5123, True, 1, 1, 'LINEAR'),
    ('rotation', 'VEC4', 5123, False, 1, 1, 'LINEAR'),
    ('weights', 'VEC3', 5126, False, 2, 1, 'LINEAR'),
    ('translation', 'VEC3', 5126, False, 2, 1, 'LINEAR'),
    ('translation', 'VEC3', 5126, False, 3, 1, 'CUBICSPLINE'),
    ('translation', 'VEC3', 5126, False, 5, 2, 'CUBICSPLINE'),
    ('weights', 'SCALAR', 5126, False, 1, 1, 'LINEAR'),
])
def test_vrm_rejects_animation_outputs_incompatible_with_their_target(tmp_path, options):
    raw = vrm_bytes(change=lambda data: vrm_animation_output(data, *options))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': raw})
    with pytest.raises(ModelFormatError):
        validate_vrm_bundle(path)


@pytest.mark.parametrize('options', [
    ('translation', 'VEC3', 5126, False, 1, 1, 'LINEAR'),
    ('rotation', 'VEC4', 5126, False, 1, 1, 'STEP'),
    ('rotation', 'VEC4', 5123, True, 1, 1, 'LINEAR'),
    ('weights', 'SCALAR', 5121, True, 2, 1, 'LINEAR'),
    ('translation', 'VEC3', 5126, False, 6, 2, 'CUBICSPLINE'),
])
def test_vrm_accepts_compatible_animation_outputs_and_normalized_rotations(tmp_path, options):
    raw = vrm_bytes(change=lambda data: vrm_animation_output(data, *options))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': raw})
    validate_vrm_bundle(path)
@pytest.mark.parametrize("version", [0, 3])
def test_bundled_cubism_motion_versions_are_admitted(tmp_path, version):
    """The bundled SDK loads both legacy default-model and current motions."""
    files = live2d_files()
    name = "motions/test.motion3.json"
    motion = json.loads(files[name])
    motion["Version"] = version
    files[name] = json.dumps(motion).encode()
    validate_live2d_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize("budget,accepted", [(55, False), (56, True)])
def test_normalized_animation_outputs_count_each_channel_allocation(tmp_path, budget, accepted):
    """Shared normalized outputs still allocate one float array per channel."""
    def graph(data):
        data.update(bufferViews=[], accessors=[
            {"componentType": 5126, "count": 1, "type": "SCALAR"},
            {"componentType": 5121, "normalized": True, "count": 1, "type": "VEC4"},
        ], animations=[{"samplers": [{"input": 0, "output": 1}],
                        "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation"}} for _ in range(3)]}])
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    if accepted:
        validate_vrm_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=budget))
    else:
        with pytest.raises(ModelFormatError, match="model_accessor_budget_exceeded"):
            validate_vrm_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=budget))


@pytest.mark.parametrize("interleaved", ["time", "rotation", "translation"])
def test_vrm_animation_does_not_read_interleaved_padding_as_keyframes(tmp_path, interleaved):
    def graph(data):
        stride = 16
        blob = base64.b64encode(bytes(stride)).decode()
        data["buffers"] = [{"byteLength": stride, "uri": "data:application/octet-stream;base64," + blob}]
        data["bufferViews"] = [{"buffer": 0, "byteLength": stride, "byteStride": stride}]
        rotation = interleaved == "rotation"
        data["accessors"] = [
            {"componentType": 5126, "count": 1, "type": "SCALAR"},
            {"componentType": 5121 if rotation else 5126, "count": 1,
             "normalized": rotation, "type": "VEC4" if rotation else "VEC3"},
        ]
        data["accessors"][0 if interleaved == "time" else 1]["bufferView"] = 0
        data["animations"] = [{
            "samplers": [{"input": 0, "output": 1}],
            "channels": [{"sampler": 0, "target": {"node": 0, "path": "rotation" if rotation else "translation"}}],
        }]

    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError, match="^unsupported_model_structure$"):
        validate_vrm_bundle(path)


@pytest.mark.parametrize("targets", [[{"POSITION": 999}], [], ["invalid"]])
def test_morph_dependencies_are_checked_without_weight_animations(tmp_path, targets):
    """Mesh loading resolves morph accessors even when the model has no animation."""
    def graph(data):
        data.update(accessors=[{"componentType": 5126, "count": 1, "type": "VEC3"}],
                    meshes=[{"primitives": [{"attributes": {"POSITION": 0}, "targets": targets}]}])
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError):
        validate_vrm_bundle(path)


@pytest.mark.parametrize("damage", ["vertex_null", "position_missing", "destination_missing", "normalization", "weight"])
def test_damaged_optional_physics_warns_without_rewriting_the_model(tmp_path, damage):
    files = live2d_files()
    vertex = {"Mobility": 1, "Delay": 0.2, "Acceleration": 1, "Radius": 1, "Position": {"X": 0, "Y": 1}}
    setting = {"Normalization": {key: {"Minimum": -1, "Maximum": 1, "Default": 0} for key in ("Position", "Angle")},
               "Input": [{"Source": {"Id": "ParamAngleX"}, "Weight": 100, "Reflect": False, "Type": "Angle"}],
               "Output": [{"Destination": {"Id": "ParamHair"}, "Weight": 100, "Reflect": False, "Type": "Angle", "Scale": 1, "VertexIndex": 1}],
               "Vertices": [dict(vertex), dict(vertex)]}
    data = {"Version": 3, "Meta": {"PhysicsSettingCount": 1, "EffectiveForces": {key: {"X": 0, "Y": 0} for key in ("Gravity", "Wind")}},
            "PhysicsSettings": [setting]}
    if damage == "vertex_null": setting["Vertices"][1] = None
    elif damage == "position_missing": setting["Vertices"][1].pop("Position")
    elif damage == "destination_missing": setting["Output"][0].pop("Destination")
    elif damage == "normalization": setting.pop("Normalization")
    else: setting["Input"][0]["Weight"] = "invalid"
    files["Avatar.physics3.json"] = json.dumps(data).encode()
    result = validate_model_bundle(make_native_bundle(tmp_path, files=files))
    assert {"code": "live2d_optional_resource_unavailable", "path": "Avatar.physics3.json"} in tuple(item.as_dict() for item in result.compatibility_warnings)


def test_old_pmd_without_toon_table_cannot_invent_a_shared_toon(tmp_path):
    main = "Models/Avatar.pmd"
    with pytest.raises(ModelFormatError):
        validate_model_bundle(make_native_bundle(tmp_path, kind="mmd", entry=main,
            files={main: pmd_bytes("", toon=0)}))


@pytest.mark.parametrize("length", [32, 48, 64])
@pytest.mark.parametrize("offset,valid", [(0, True), (4, True), (8, False), (12, False)])
def test_interleaved_accessor_checks_allocation_and_last_element(tmp_path, offset, valid, length):
    def graph(data):
        vrm_graph(data)
        data['bufferViews'][1].update(byteStride=16, byteLength=length)
        data['accessors'] = [{'bufferView': 1, 'componentType': 5126, 'type': 'VEC3', 'byteOffset': offset, 'count': 2}]
        data['animations'] = []; data['skins'] = []; data['nodes'][0].pop('skin')
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}}]}]
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('fps', [1e300, 1e9, -1, 'invalid'])
def test_physics_fps_cannot_exhaust_the_main_thread(tmp_path, fps):
    files = live2d_files()
    files['Avatar.physics3.json'] = json.dumps({'Version': 3, 'Meta': {'Fps': fps, 'PhysicsSettingCount': 0}, 'PhysicsSettings': []}).encode()
    with pytest.raises(ModelFormatError): validate_model_bundle(make_native_bundle(tmp_path, files=files))


@pytest.mark.parametrize('case', ['valid', 'index', 'shape', 'count', 'budget', 'primitives'])
def test_gpu_instances_validate_attributes_and_per_node_matrix_budget(tmp_path, case):
    def graph(data):
        vrm_graph(data)
        data['animations'] = []; data['skins'] = []; data['nodes'][0].pop('skin')
        data['accessors'].append({'componentType': 5126, 'type': 'VEC3', 'count': 4})
        attrs = {'TRANSLATION': 3}
        if case == 'index': attrs['TRANSLATION'] = 999
        elif case == 'shape': data['accessors'][3]['type'] = 'SCALAR'
        elif case == 'count': attrs['SCALE'] = 1
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 1}}] * (2 if case == 'primitives' else 1)}]
        data['nodes'][0].update(mesh=0, extensions={'EXT_mesh_gpu_instancing': {'attributes': attrs}})
        if case == 'budget': data['nodes'].append(dict(data['nodes'][0]))
        data['extensionsUsed'].append('EXT_mesh_gpu_instancing')
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if case == 'valid':
        assert validate_model_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=512)).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=512))


@pytest.mark.parametrize('case', ['vrm0_texture', 'missing_source'])
def test_vrm_texture_dependencies_must_be_loadable(tmp_path, case):
    def graph(data):
        vrm_graph(data)
        if case == 'missing_source': data['textures'][0].pop('source')
        else: data['extensions']['VRM']['materialProperties'] = [{'textureProperties': {'_MainTex': 99}}]
    raw = vrm_bytes(version='0.0' if case == 'vrm0_texture' else '1.0', change=graph)
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': raw})
    with pytest.raises(ModelFormatError): validate_model_bundle(path)


def test_compatibility_warnings_are_frozen_and_bundle_remains_hashable(tmp_path):
    from dataclasses import FrozenInstanceError
    files = live2d_files()
    model = json.loads(files['Avatar.model3.json']); model['FileReferences']['Physics'] = 'missing.physics3.json'
    files['Avatar.model3.json'] = json.dumps(model).encode()
    bundle = validate_model_bundle(make_native_bundle(tmp_path, files=files))
    assert hash(bundle) == hash(bundle)
    warning = bundle.compatibility_warnings[0]
    with pytest.raises(FrozenInstanceError): warning.code = 'changed'


@pytest.mark.parametrize("instances,vertices,valid", [(10, 50000, True), (2000000, 50000, False)])
def test_gpu_instance_draw_work_is_bounded_below_the_memory_budget(tmp_path, instances, vertices, valid):
    def graph(data):
        vrm_graph(data)
        data['animations'] = []; data['skins'] = []; data['nodes'][0].pop('skin')
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': vertices},
                             {'componentType': 5126, 'type': 'VEC3', 'count': instances}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}}]}]
        data['nodes'][0].update(mesh=0, extensions={'EXT_mesh_gpu_instancing': {'attributes': {'TRANSLATION': 1}}})
        data['extensionsUsed'].append('EXT_mesh_gpu_instancing')
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize("settings,fps,valid", [(30, 60, True), (150, 60, True), (300, 60, True), (300, 240, True), (1000, 60, False), (1000, 240, False)])
def test_physics_budget_counts_per_step_rig_work(tmp_path, settings, fps, valid):
    files = live2d_files()
    setting = {"Normalization": {key: {"Minimum": -1, "Maximum": 1, "Default": 0} for key in ("Position", "Angle")},
               "Input": [{"Source": {"Id": "ParamAngleX"}, "Weight": 100, "Type": "Angle"}] * 3,
               "Output": [{"Destination": {"Id": "ParamHair"}, "Weight": 100, "Type": "Angle", "Scale": 1, "VertexIndex": 1}] * 3,
               "Vertices": [{"Mobility": 1, "Delay": 0.2, "Acceleration": 1, "Radius": 1, "Position": {"X": 0, "Y": 1}}] * 3}
    files['Avatar.physics3.json'] = json.dumps({"Version": 3, "Meta": {"Fps": fps, "PhysicsSettingCount": settings,
        "EffectiveForces": {key: {"X": 0, "Y": 0} for key in ("Gravity", "Wind")}}, "PhysicsSettings": [setting] * settings}).encode()
    path = make_native_bundle(tmp_path, files=files)
    if valid:
        assert not validate_model_bundle(path).compatibility_warnings
    else:
        with pytest.raises(ModelFormatError, match='model_physics_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize('channels', [1, 100])
def test_shared_morph_dependencies_are_checked_once_across_animations(tmp_path, monkeypatch, channels):
    import importlib
    formats = importlib.import_module(validate_model_bundle.__module__)
    def graph(data):
        vrm_graph(data)
        data['accessors'] = [{'componentType': 5126, 'type': 'SCALAR', 'count': 1},
                             {'componentType': 5126, 'type': 'SCALAR', 'count': 50},
                             {'componentType': 5126, 'type': 'VEC3', 'count': 3}]
        data['skins'] = []; data['nodes'][0].pop('skin')
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 2}, 'targets': [{'POSITION': 2}] * 50}]}]
        data['nodes'][0]['mesh'] = 0
        animation = {'samplers': [{'input': 0, 'output': 1}],
                     'channels': [{'sampler': 0, 'target': {'node': 0, 'path': 'weights'}}] * channels}
        data['animations'] = [animation, animation]
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    original = formats._index; checked = []
    def counted(value, table, entry):
        if value == 2 and table and isinstance(table[0], dict) and 'componentType' in table[0]: checked.append(value)
        return original(value, table, entry)
    monkeypatch.setattr(formats, '_index', counted)
    validate_model_bundle(path)
    assert len(checked) == 51  # POSITION plus each of its 50 morphs, independent of channels.


@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('mode,valid', [(4, True), (5, False), (6, False)])
def test_instanced_draw_budget_uses_converted_triangle_indices(tmp_path, indexed, mode, valid):
    def graph(data):
        vrm_graph(data)
        data['animations'] = []; data['skins'] = []; data['nodes'][0].pop('skin')
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 50000},
                             {'componentType': 5126, 'type': 'VEC3', 'count': 100},
                             {'componentType': 5125, 'type': 'SCALAR', 'count': 50000}]
        primitive = {'attributes': {'POSITION': 0}, 'mode': mode}
        if indexed: primitive['indices'] = 2
        data['meshes'] = [{'primitives': [primitive]}]
        data['nodes'][0].update(mesh=0, extensions={'EXT_mesh_gpu_instancing': {'attributes': {'TRANSLATION': 1}}})
        data['extensionsUsed'].append('EXT_mesh_gpu_instancing')
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize('nodes,mode,valid', [(10, 4, True), (101, 4, False), (10, 5, True), (34, 5, False)])
def test_shared_mesh_draw_work_counts_ordinary_nodes(tmp_path, nodes, mode, valid):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 50000}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}, 'mode': mode}]}]
        data['nodes'] = [{'mesh': 0} for _ in range(nodes)]
        data['scenes'][0]['nodes'] = list(range(nodes))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize('mode', [4, 5, 6])
@pytest.mark.parametrize('primitives', [1, 2])
@pytest.mark.parametrize('indexed', [False, True])
@pytest.mark.parametrize('morph', [False, True])
def test_triangle_conversion_budgets_each_primitive_copy(tmp_path, mode, primitives, indexed, morph):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 1000},
                             {'componentType': 5125, 'type': 'SCALAR', 'count': 1000}]
        primitive = {'attributes': {'POSITION': 0}, 'mode': mode}
        if indexed: primitive['indices'] = 1
        if morph: primitive['targets'] = [{'POSITION': 0}]
        data['meshes'] = [{'primitives': [primitive] * primitives}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    limits = FormatLimits(vrm_accessor_bytes=60000)
    if mode == 4 or primitives == 1:
        assert validate_model_bundle(path, format_limits=limits).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=limits)


@pytest.mark.parametrize('shared,valid', [(False, False), (True, True)])
def test_triangle_conversion_counts_interleaved_padding_and_shares_its_clone(tmp_path, shared, valid):
    def graph(data):
        raw = bytes(32000)
        data['buffers'].append({'byteLength': len(raw), 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(raw).decode()})
        data['bufferViews'].append({'buffer': 1, 'byteLength': len(raw), 'byteStride': 32})
        data['accessors'] = [{'bufferView': 1, 'componentType': 5126, 'type': 'VEC3', 'count': 1000}]
        primitive = {'attributes': {'POSITION': 0}, 'mode': 5}
        if shared: primitive['attributes']['NORMAL'] = 0
        data['meshes'] = [{'primitives': [primitive]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    limits = FormatLimits(vrm_accessor_bytes=85000 if valid else 70000)
    if valid:
        assert validate_model_bundle(path, format_limits=limits).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=limits)


def test_triangle_conversion_counts_missing_morphs_that_fall_back_to_base_attributes(tmp_path):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 1000}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}, 'mode': 5, 'targets': [{'POSITION': 0}, {}]}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
        validate_model_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=60000))


@pytest.mark.parametrize('case', ['perspective', 'orthographic', 'missing_table', 'index', 'negative', 'boolean', 'string',
                                 'bad_table', 'bad_camera', 'unknown_type', 'missing_parameters'])
def test_scene_camera_dependencies_are_checked(tmp_path, case):
    def graph(data):
        kind = case if case in ('perspective', 'orthographic') else 'perspective'
        params = {'yfov': 1, 'znear': 0.1} if kind == 'perspective' else {'xmag': 1, 'ymag': 1, 'znear': 0.1, 'zfar': 10}
        data['cameras'] = [{'type': kind, kind: params}]
        data['nodes'][0]['camera'] = 0
        if case == 'missing_table': data.pop('cameras')
        elif case in ('index', 'negative', 'boolean', 'string'):
            data['nodes'][0]['camera'] = {'index': 99, 'negative': -1, 'boolean': True, 'string': '0'}[case]
        elif case == 'bad_table': data['cameras'] = {}
        elif case == 'bad_camera': data['cameras'] = [None]
        elif case == 'unknown_type': data['cameras'][0]['type'] = 'unknown'
        elif case == 'missing_parameters': data['cameras'][0].pop(kind)
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if case in ('perspective', 'orthographic'):
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('stride,sparse,valid', [(12, False, True), (12, True, True), (16, False, True),
                                               (16, True, False), (252, False, True), (252, True, False)])
@pytest.mark.parametrize('mode', [4, 5, 6])
def test_sparse_accessor_preserves_loaded_vertex_layout(tmp_path, stride, sparse, valid, mode):
    def graph(data):
        count = 10000
        raw = bytes(stride * count + 16)
        data['buffers'].append({'byteLength': len(raw), 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(raw).decode()})
        data['bufferViews'].extend([{'buffer': 1, 'byteLength': stride * count, 'byteStride': stride},
                                   {'buffer': 1, 'byteOffset': stride * count, 'byteLength': 1},
                                   {'buffer': 1, 'byteOffset': stride * count + 4, 'byteLength': 12}])
        accessor = {'bufferView': 1, 'componentType': 5126, 'type': 'VEC3', 'count': count}
        if sparse:
            accessor['sparse'] = {'count': 1, 'indices': {'bufferView': 2, 'componentType': 5121}, 'values': {'bufferView': 3}}
        data['accessors'] = [accessor]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}, 'mode': mode}]}]
        data['nodes'] = [{'mesh': 0} for _ in range(100)]
        data['scenes'][0]['nodes'] = list(range(100))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='unsupported_model_structure'):
            validate_model_bundle(path)


@pytest.mark.parametrize('mode', ['absent', *range(7), 7, -1, '4', '5', 5.5, 4.0, True, False, None, {}, []])
def test_vrm_primitive_mode_matches_loader_support(tmp_path, mode):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3}]
        primitive = {'attributes': {'POSITION': 0}}
        if mode != 'absent': primitive['mode'] = mode
        data['meshes'] = [{'primitives': [primitive]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if mode == 'absent' or type(mode) in (int, float) and mode in range(7):
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('stride,sparse,valid', [(None, False, True), (None, True, True), (8, False, True), (8, True, False)])
def test_padded_matrix_uses_loader_item_width_for_interleaving(tmp_path, stride, sparse, valid):
    def graph(data):
        sparse_accessors(data, count=4, backed=True)
        data['accessors'][0].update(type='MAT2', componentType=5121)
        if stride is None: data['bufferViews'][1].pop('byteStride')
        else: data['bufferViews'][1]['byteStride'] = stride
        if not sparse: data['accessors'][0].pop('sparse')
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='unsupported_model_structure'): validate_model_bundle(path)


def test_padded_interleaved_matrix_attributes_share_the_conversion_clone(tmp_path):
    def graph(data):
        data['buffers'].append({'byteLength': 32, 'uri': 'data:application/octet-stream;base64,' + base64.b64encode(bytes(32)).decode()})
        data['bufferViews'].append({'buffer': 1, 'byteLength': 32, 'byteStride': 8})
        data['accessors'] = [{'bufferView': 1, 'componentType': 5121, 'type': 'MAT2', 'count': 4},
                             {'componentType': 5126, 'type': 'VEC3', 'count': 4}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 1, '_A': 0, '_B': 0}, 'mode': 5}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    assert validate_model_bundle(path, format_limits=FormatLimits(vrm_accessor_bytes=210)).model_format == 'vrm'



def mmd_runtime_bytes(kind, *, vertices=3, morph_kinds=(), iterations=(), links=1,
                      parents=None, link=2, morph_records=None, owners=None, materials=0, grants=None, append_flag=256,
                      effector=1, rigid_bone=None):
    """Small complete layouts with controllable loader work, no external resources."""
    parents = list(parents) if parents is not None else [-1] * max(3, len(iterations))
    grants = grants or {}
    if kind == 'pmd':
        raw = b'Pmd' + struct.pack('<f', 1.0) + bytes(276)
        raw += struct.pack('<I', vertices) + bytes(vertices * 38) + struct.pack('<2I', 0, 0)
        raw += struct.pack('<H', len(parents))
        for index, parent in enumerate(parents):
            raw += bytes(20) + struct.pack('<hhBh', parent, grants.get(index, 0),
                                           5 if index in grants else 0, 100) + bytes(12)
        raw += struct.pack('<H', len(iterations))
        for owner, count in zip(owners if owners is not None else [0] * len(iterations), iterations):
            raw += struct.pack('<HHBHf', owner, effector, links, count, 1.0) + struct.pack('<H', link) * links
        raw += struct.pack('<H', len(morph_kinds) + 1)
        raw += bytes(25) + (bytes(20) + struct.pack('<IB', 0, 1)) * len(morph_kinds)
        raw += struct.pack('<2BI', 0, 0, 0)
        if rigid_bone is not None:
            raw += bytes(1001) + struct.pack("<I20xh61xI", 1, rigid_bone, 0)
        return raw
    def text(value):
        encoded = value.encode(); return struct.pack('<i', len(encoded)) + encoded
    bone_width = 2 if max([len(parents), *parents, *grants.values()]) > 127 else 1
    bone_code = 'h' if bone_width == 2 else 'b'
    raw = b'PMX ' + struct.pack('<f', 2.0) + bytes([8, 1, 0, 4, 1, 1, bone_width, 4, 1]) + text('Fixture') + text('') * 3
    raw += struct.pack('<i', vertices) + (bytes(33 + bone_width) + struct.pack('<f', 1.0)) * vertices
    raw += struct.pack('<3i', 0, 0, materials)
    raw += (text('') * 2 + bytes(65) + struct.pack('<bbBBB', -1, -1, 0, 1, 0) + text('') + struct.pack('<i', 0)) * materials
    raw += struct.pack('<i', len(parents))
    for index, parent in enumerate(parents):
        has_ik = index < len(iterations)
        raw += text('Bone' + str(index)) + text('') + bytes(12)
        flags = (33 if has_ik else 1) | (append_flag if index in grants else 0)
        raw += struct.pack('<' + bone_code + 'iH' + bone_code, parent, 0, flags, -1)
        if index in grants:
            raw += struct.pack('<' + bone_code + 'f', grants[index], 1.0)
        if has_ik:
            raw += struct.pack('<' + bone_code + 'ifi', effector, iterations[index], 1.0, links)
            raw += (struct.pack('<' + bone_code + 'B', link, 1) + bytes(24)) * links
    records = [(kind, ()) for kind in morph_kinds] if morph_records is None else morph_records
    raw += struct.pack('<i', len(records))
    for morph_kind, offsets in records:
        raw += text('Morph') + text('') + struct.pack('<BBi', 0, morph_kind, len(offsets))
        if morph_kind == 8:
            raw += b''.join(struct.pack('<b', offset) + bytes(113) for offset in offsets)
        else:
            raw += b''.join(struct.pack('<if', offset, 1.0) if morph_kind == 0 else struct.pack('<I3f', offset, 0.0, 0.0, 0.0)
                            for offset in offsets)
    raw += struct.pack('<i', 0)  # Display frames.
    if rigid_bone is None:
        return raw + struct.pack('<2i', 0, 0)
    return raw + struct.pack('<i', 1) + text('') * 2 + struct.pack('<' + bone_code, rigid_bone) + bytes(61) + struct.pack('<i', 0)


@pytest.mark.parametrize('kind', ['pmx', 'pmd'])
@pytest.mark.parametrize('budget,valid', [(71, False), (72, True)])
def test_mmd_empty_morphs_still_allocate_dense_positions(tmp_path, kind, budget, valid):
    entry = 'Avatar.' + kind
    raw = mmd_runtime_bytes(kind, morph_kinds=(0, 1))
    path = make_native_bundle(tmp_path, kind='mmd', entry=entry, files={entry: raw})
    limits = FormatLimits(mmd_morph_bytes=budget)
    if valid:
        assert validate_model_bundle(path, format_limits=limits).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=limits)


def test_mmd_morph_budget_rejects_tiny_compressed_package_with_gigabytes_of_positions(tmp_path):
    raw = mmd_runtime_bytes('pmx', vertices=100000, morph_kinds=[1] * 10000)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    assert path.stat().st_size < 20000
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


def test_non_position_pmx_morphs_do_not_consume_position_budget(tmp_path):
    raw = mmd_runtime_bytes('pmx', morph_kinds=(2, 3, 8))
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    assert validate_model_bundle(path, format_limits=FormatLimits(mmd_morph_bytes=0)).model_format == 'mmd'


@pytest.mark.parametrize('kind', ['pmx', 'pmd'])
@pytest.mark.parametrize('iterations,links,valid', [([40] * 10, 4, True), ([256] * 40, 4, True),
    ([4096], 1, True), ([4097], 1, False), ([4096] * 7, 4, False), ([0], 1, True)])
def test_mmd_ik_iteration_and_aggregate_work_limits(tmp_path, kind, iterations, links, valid):
    entry = 'Avatar.' + kind
    raw = mmd_runtime_bytes(kind, iterations=iterations, links=links)
    path = make_native_bundle(tmp_path, kind='mmd', entry=entry, files={entry: raw})
    if valid:
        assert validate_model_bundle(path).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize("kind,effector,iterations,owners,accepted", [
    ("pmx", 1, [1], None, False),
    ("pmx", -1, [1], None, False),
    ("pmx", 0, [1], None, True),
    ("pmd", 1, [1], None, False),
    ("pmd", 0, [1], None, True),
    ("pmd", 1, [1, 1], [0, 0], True),
    ("pmd", 2, [1, 1], [0, 0], False),
    ("pmd", 1, [1], [1], True),
])
def test_mmd_ik_effectors_match_the_loader_bone_slots(tmp_path, kind, effector, iterations, owners, accepted):
    raw = mmd_runtime_bytes(kind, parents=[-1], iterations=iterations, link=0, owners=owners, effector=effector)
    entry = f"Avatar.{kind}"
    path = make_native_bundle(tmp_path, kind="mmd", entry=entry, files={entry: raw})
    if accepted:
        assert validate_model_bundle(path).model_format == "mmd"
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_model_bundle(path)


def test_pmd_ik_conversion_has_a_budget_even_without_solver_work(tmp_path):
    model = mmd_runtime_bytes("pmd", iterations=[0, 0], links=0, owners=[0, 1])
    files = {"Avatar.pmd": model}
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmd", files=files)
    assert validate_mmd_bundle(path, format_limits=FormatLimits(pmd_ik_scan_work=8)).model_format == "mmd"
    with pytest.raises(ModelFormatError, match="^model_accessor_budget_exceeded$"):
        validate_mmd_bundle(path, format_limits=FormatLimits(pmd_ik_scan_work=7))


@pytest.mark.parametrize("bone,owners,accepted", [
    (2, None, True), (3, None, False), (-1, None, True), (-2, None, False),
    (3, [0, 0], True), (4, [0, 0], False), (-1, [1, 0], False),
])
def test_pmd_rigid_body_bones_match_converted_slots(tmp_path, bone, owners, accepted):
    raw = mmd_runtime_bytes("pmd", rigid_bone=bone, owners=owners, iterations=[1] * len(owners or ()))
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmd", files={"Avatar.pmd": raw})
    if accepted:
        assert validate_mmd_bundle(path).model_format == "mmd"
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_mmd_bundle(path)


@pytest.mark.parametrize("bone,accepted", [(2, True), (-1, True), (3, False), (-2, False)])
def test_pmx_rigid_body_bones_are_in_range(tmp_path, bone, accepted):
    raw = mmd_runtime_bytes("pmx", rigid_bone=bone)
    path = make_native_bundle(tmp_path, kind="mmd", entry="Avatar.pmx", files={"Avatar.pmx": raw})
    if accepted:
        validate_mmd_bundle(path)
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_mmd_bundle(path)


@pytest.mark.parametrize('iterations', [-1, 2147483647])
def test_pmx_ik_rejects_negative_or_unbounded_iterations(tmp_path, iterations):
    raw = mmd_runtime_bytes('pmx', iterations=[iterations])
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('case', ['point', 'directional', 'spot', 'missing_table', 'index', 'negative', 'bool', 'string',
                                 'bad_table', 'bad_light', 'bad_type', 'bad_spot', 'empty_node'])
def test_punctual_light_dependencies_match_loader(tmp_path, case):
    def graph(data):
        data['extensionsUsed'].append('KHR_lights_punctual')
        data['extensions']['KHR_lights_punctual'] = {'lights': [{'type': case if case in ('point', 'directional', 'spot') else 'point'}]}
        data['nodes'][0]['extensions'] = {'KHR_lights_punctual': {'light': 0}}
        if case == 'missing_table': data['extensions'].pop('KHR_lights_punctual')
        elif case in ('index', 'negative', 'bool', 'string'):
            data['nodes'][0]['extensions']['KHR_lights_punctual']['light'] = {'index': 999, 'negative': -1, 'bool': False, 'string': '0'}[case]
        elif case == 'bad_table': data['extensions']['KHR_lights_punctual']['lights'] = {}
        elif case == 'bad_light': data['extensions']['KHR_lights_punctual']['lights'] = [None]
        elif case == 'bad_type': data['extensions']['KHR_lights_punctual']['lights'][0]['type'] = 'unsupported'
        elif case == 'bad_spot': data['extensions']['KHR_lights_punctual']['lights'][0] = {'type': 'spot', 'spot': 'invalid'}
        elif case == 'empty_node': data['nodes'][0]['extensions']['KHR_lights_punctual'] = {}
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if case in ('point', 'directional', 'spot', 'empty_node'):
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('color,valid', [(None, False), ([], False), ([1, 1], False), ([1, 1, 1, 1], False),
    ([True, 1, 1], False), (['1', 1, 1], False), ({'0': 1}, False), ([0, 0.5, 1], True), ([0, 0, 0], True)])
def test_referenced_light_color_structure(tmp_path, color, valid):
    def graph(data):
        data['extensionsUsed'].append('KHR_lights_punctual')
        data['extensions']['KHR_lights_punctual'] = {'lights': [{'type': 'point', 'color': color}]}
        data['nodes'][0]['extensions'] = {'KHR_lights_punctual': {'light': 0}}
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('kind', ['pmx', 'pmd'])
@pytest.mark.parametrize('parents,work', [([-1, -1, -1] + [2] * 8, 108),
    ([-1, -1, -1] + list(range(2, 10)), 108), ([-1, 2, 3, -1, 2, 4], 48),
    ([-1, 999, -1], 12)])
@pytest.mark.parametrize('valid', [False, True])
def test_ik_work_counts_link_subtrees_including_forward_parents(tmp_path, kind, parents, work, valid):
    raw = mmd_runtime_bytes(kind, parents=parents, iterations=[12])
    entry = 'Avatar.' + kind
    path = make_native_bundle(tmp_path, kind='mmd', entry=entry, files={entry: raw})
    limits = FormatLimits(mmd_ik_work=work if valid else work - 1)
    if valid:
        assert validate_model_bundle(path, format_limits=limits).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=limits)


@pytest.mark.parametrize('kind', ['pmx', 'pmd'])
def test_default_ik_budget_rejects_wide_subtree_amplification(tmp_path, kind):
    raw = mmd_runtime_bytes(kind, parents=[-1] * 24 + [2] * 1000, iterations=[4096] * 24)
    entry = 'Avatar.' + kind
    path = make_native_bundle(tmp_path, kind='mmd', entry=entry, files={entry: raw})
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize('budget,valid', [(23, False), (24, True)])
def test_pmd_duplicate_ik_adds_descendant_bones(tmp_path, budget, valid):
    raw = mmd_runtime_bytes('pmd', parents=[-1, 0, 0], iterations=[3, 3], link=0)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmd', files={'Avatar.pmd': raw})
    if valid:
        assert validate_model_bundle(path, format_limits=FormatLimits(mmd_ik_work=budget)).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=FormatLimits(mmd_ik_work=budget))


@pytest.mark.parametrize('kind', ['pmx', 'pmd'])
def test_ik_hierarchy_cycle_is_rejected_without_recursive_walk(tmp_path, kind):
    raw = mmd_runtime_bytes(kind, parents=[2, -1, 0], iterations=[1])
    entry = 'Avatar.' + kind
    path = make_native_bundle(tmp_path, kind='mmd', entry=entry, files={entry: raw})
    with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('forward', [False, True])
@pytest.mark.parametrize('budget,valid', [(8, False), (9, True)])
def test_group_morph_work_counts_repeated_and_forward_vertex_offsets(tmp_path, forward, budget, valid):
    records = [(1, range(3)), (0, [0, 0])]
    if forward: records = [(0, [1, 1]), (1, range(3))]
    raw = mmd_runtime_bytes('pmx', morph_records=records)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    limits = FormatLimits(mmd_morph_work=budget)
    if valid:
        assert validate_model_bundle(path, format_limits=limits).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=limits)


def test_default_group_morph_budget_rejects_large_expansion(tmp_path):
    records = [(1, range(900))] * 900 + [(0, range(900))] * 900
    raw = mmd_runtime_bytes('pmx', vertices=900, morph_records=records)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    assert path.stat().st_size < 250000
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


def test_group_morph_targeting_group_is_not_recursively_expanded(tmp_path):
    raw = mmd_runtime_bytes('pmx', morph_records=[(0, [1]), (0, [2]), (1, range(3))])
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    assert validate_model_bundle(path, format_limits=FormatLimits(mmd_morph_work=6)).model_format == 'mmd'


@pytest.mark.parametrize('forward', [False, True])
@pytest.mark.parametrize('budget,valid', [(8, False), (9, True)])
def test_material_morph_work_includes_direct_group_expansion(tmp_path, forward, budget, valid):
    records = [(8, [0, -1, 0]), (0, [0, 0])]
    if forward: records = [(0, [1, 1]), (8, [0, -1, 0])]
    raw = mmd_runtime_bytes('pmx', morph_records=records, materials=1)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    if valid:
        assert validate_model_bundle(path, format_limits=FormatLimits(mmd_morph_work=budget)).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=FormatLimits(mmd_morph_work=budget))


@pytest.mark.parametrize('grouped', [False, True])
@pytest.mark.parametrize('index,valid', [(-1, True), (0, True), (1, False), (-2, False)])
def test_material_morph_indices_follow_loader_skip_semantics(tmp_path, grouped, index, valid):
    records = [(8, [index])] + ([(0, [0])] if grouped else [])
    raw = mmd_runtime_bytes('pmx', morph_records=records, materials=1)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    if valid: assert validate_model_bundle(path).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


def test_default_material_morph_work_budget_rejects_repeated_expansion(tmp_path):
    raw = mmd_runtime_bytes('pmx', morph_records=[(8, [0] * 1000)] + [(0, [0] * 20)] * 1000, materials=1)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


@pytest.mark.parametrize('owners', [(0, 1, 1, 0), (1, 0, 1, 0)])
@pytest.mark.parametrize('budget,valid', [(11, False), (12, True)])
def test_pmd_duplicate_slots_use_bone_order_even_when_ik_records_are_reordered(tmp_path, owners, budget, valid):
    raw = mmd_runtime_bytes('pmd', parents=[-1, -1, 3], iterations=[1] * 4, owners=owners, link=0)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmd', files={'Avatar.pmd': raw})
    if valid:
        assert validate_model_bundle(path, format_limits=FormatLimits(mmd_ik_work=budget)).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=FormatLimits(mmd_ik_work=budget))


@pytest.mark.parametrize('parents', [(0, -1, -1), (1, 0, -1)])
@pytest.mark.parametrize('iterations', [(), (0,), (1,)])
def test_pmd_parent_cycles_are_checked_without_active_ik(tmp_path, parents, iterations):
    raw = mmd_runtime_bytes('pmd', parents=parents, iterations=iterations)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmd', files={'Avatar.pmd': raw})
    with pytest.raises(ModelFormatError): validate_model_bundle(path)


def test_pmd_acyclic_tree_without_ik_still_loads(tmp_path):
    raw = mmd_runtime_bytes('pmd', parents=[2, -1, 1], iterations=())
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmd', files={'Avatar.pmd': raw})
    assert validate_model_bundle(path).model_format == 'mmd'


@pytest.mark.parametrize('semantics', [('POSITION',), ('NORMAL',), ('COLOR_0',), ('POSITION', 'NORMAL'), ('POSITION', 'NORMAL', 'COLOR_0')])
@pytest.mark.parametrize('valid', [False, True])
def test_vrm_morph_texture_charges_each_layer_and_semantic_slot(tmp_path, semantics, valid):
    slots = 3 if 'COLOR_0' in semantics else 2 if 'NORMAL' in semantics else 1
    budget = 3 * slots * 32 * 2
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3}]
        target = {"POSITION": 0, **{semantic: 0 for semantic in semantics}}
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 0, 'COLOR_0': 0}, 'targets': [target, {}]}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path, format_limits=FormatLimits(vrm_morph_bytes=budget)).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=FormatLimits(vrm_morph_bytes=budget - 1))


def test_vrm_shared_morph_accessors_cannot_hide_multi_gigabyte_texture(tmp_path):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 1000000}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}, 'targets': [{'POSITION': 0}] * 1000}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    assert path.stat().st_size < 20000
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


def test_vrm_morph_texture_is_not_charged_again_for_shared_mesh_nodes(tmp_path):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}, 'targets': [{'POSITION': 0}]}]}]
        data['nodes'] = [{'mesh': 0} for _ in range(20)]
        data['scenes'][0]['nodes'] = list(range(20))
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    assert validate_model_bundle(path, format_limits=FormatLimits(vrm_morph_bytes=96)).model_format == 'vrm'
    assert validate_vrm_bundle(path, format_limits=FormatLimits(vrm_morph_weights=21)).model_format == "vrm"
    with pytest.raises(ModelFormatError, match="^model_accessor_budget_exceeded$"):
        validate_vrm_bundle(path, format_limits=FormatLimits(vrm_morph_weights=20))


@pytest.mark.parametrize("scope", ["mesh", "node"])
@pytest.mark.parametrize("weights,targets,accepted", [
    ([0.5], 1, True),
    ([], 0, True),
    ([], 1, False),
    ([0.5, 0], 1, False),
    ([0.5], 0, False),
    ([True], 1, False),
    (["0.5"], 1, False),
    (None, 1, False),
])
def test_vrm_explicit_morph_weights_match_the_primitive_targets(tmp_path, scope, weights, targets, accepted):
    def graph(data):
        data["accessors"] = [{"componentType": 5126, "type": "VEC3", "count": 3}]
        primitive = {"attributes": {"POSITION": 0}}
        if targets:
            primitive["targets"] = [{"POSITION": 0}]
        mesh = {"primitives": [primitive]}
        node = data["nodes"][0]
        data["meshes"] = [mesh]
        node["mesh"] = 0
        (mesh if scope == "mesh" else node)["weights"] = weights

    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    if accepted:
        assert validate_vrm_bundle(path).model_format == "vrm"
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_vrm_bundle(path)


def test_vrm_node_weights_require_a_mesh(tmp_path):
    def graph(data):
        data["nodes"][0]["weights"] = []

    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
        validate_vrm_bundle(path)


@pytest.mark.parametrize('table', ['light', 'mesh', 'node', 'camera', 'scene'])
@pytest.mark.parametrize('name,valid', [(5, False), (True, False), ([], False), ({}, False),
    ('Avatar', True), ('', True), (None, True), (False, True), (0, True)])
def test_gltf_names_preserve_loader_defaults_and_reject_crashing_types(tmp_path, table, name, valid):
    def graph(data):
        if table == 'light':
            data['extensionsUsed'].append('KHR_lights_punctual')
            data['extensions']['KHR_lights_punctual'] = {'lights': [{'type': 'point', 'name': name}]}
            data['nodes'][0]['extensions'] = {'KHR_lights_punctual': {'light': 0}}
        elif table == 'mesh':
            data['meshes'] = [{'name': name, 'primitives': [{'attributes': {'POSITION': 0}}]}]
            data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3}]
            data['nodes'][0]['mesh'] = 0
        elif table == 'camera':
            data['cameras'] = [{'name': name, 'type': 'perspective', 'perspective': {'yfov': 1, 'znear': 0.1}}]
            data['nodes'][0]['camera'] = 0
        else:
            data[{'node': 'nodes', 'scene': 'scenes'}[table]][0]['name'] = name
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid: assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('parents,valid', [([1, 0, -1], False), ([2, -1, 0], False),
    ([0, -1, -1], True), ([2, -1, 1], True)])
def test_pmx_parent_graph_is_checked_without_ik(tmp_path, parents, valid):
    raw = mmd_runtime_bytes('pmx', parents=parents)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    if valid: assert validate_model_bundle(path).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('mode', [4, 5, 6])
@pytest.mark.parametrize('semantic', ['POSITION', 'NORMAL', 'COLOR_0'])
@pytest.mark.parametrize('count,valid', [(2, False), (3, True), (4, False)])
def test_morph_attribute_count_matches_base_positions(tmp_path, mode, semantic, count, valid):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3},
                             {'componentType': 5126, 'type': 'VEC3', 'count': count}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 0, 'COLOR_0': 0},
                                         'targets': [{'POSITION': 0, semantic: 1}, {}], 'mode': mode}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid: assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


def test_morph_texture_copy_cannot_hide_billion_iterations_in_mismatched_accessor(tmp_path):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3},
                             {'componentType': 5126, 'type': 'VEC3', 'count': 1000000}]
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0}, 'targets': [{'POSITION': 1}] * 1000}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    assert path.stat().st_size < 20000
    with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('kind', ['vrm', 'pmx', 'pmd'])
@pytest.mark.parametrize('depth,valid', [(64, True), (65, False)])
def test_model_hierarchy_depth_boundary(tmp_path, kind, depth, valid):
    if kind == 'vrm':
        def graph(data):
            data['nodes'] = [{'children': [i + 1]} if i + 1 < depth else {} for i in range(depth)]
        raw, entry = vrm_bytes(change=graph), 'Avatar.vrm'
    else:
        raw = mmd_runtime_bytes(kind, parents=[i + 1 if i + 1 < depth else -1 for i in range(depth)])
        entry = 'Avatar.' + kind
    path = make_native_bundle(tmp_path, kind='vrm' if kind == 'vrm' else 'mmd', entry=entry, files={entry: raw})
    if valid: assert validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=64)).model_format
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=64))


@pytest.mark.parametrize('kind', ['vrm', 'pmx', 'pmd'])
def test_model_default_hierarchy_limit_rejects_deep_flat_references(tmp_path, kind):
    depth = 1025
    if kind == 'vrm':
        def graph(data):
            data['nodes'] = [{'children': [i + 1]} if i + 1 < depth else {} for i in range(depth)]
        raw, entry = vrm_bytes(change=graph), 'Avatar.vrm'
    else:
        raw = mmd_runtime_bytes(kind, parents=[i + 1 if i + 1 < depth else -1 for i in range(depth)])
        entry = 'Avatar.' + kind
    path = make_native_bundle(tmp_path, kind='vrm' if kind == 'vrm' else 'mmd', entry=entry, files={entry: raw})
    with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'): validate_model_bundle(path)


def test_vrm_shared_descendants_keep_longest_path_even_when_already_checked(tmp_path):
    def graph(data):
        data['nodes'] = [{}, {'children': [0]}, {'children': [1]}, {'children': [2]}, {'children': [1]}]
        data['scenes'][0]['nodes'] = [3, 4]
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    with pytest.raises(ModelFormatError): validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=3))
    assert validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=4)).model_format == 'vrm'


@pytest.mark.parametrize('semantic', ['NORMAL', 'COLOR_0'])
@pytest.mark.parametrize('position', [False, True])
def test_vrm_rendered_morphs_need_a_position_layer(tmp_path, semantic, position):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3}]
        target = {semantic: 0}
        if position: target['POSITION'] = 0
        data['meshes'] = [{'primitives': [{'attributes': {'POSITION': 0, 'NORMAL': 0, 'COLOR_0': 0}, 'targets': [target, {}]}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if position: assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


def test_vrm_common_multi_expression_face_keeps_padding_allowance(tmp_path):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 12000}]
        primitive = {'attributes': {'POSITION': 0, 'NORMAL': 0}, 'targets': [{'POSITION': 0, 'NORMAL': 0}] * 60}
        data['meshes'] = [{'primitives': [primitive] * 6}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    assert validate_model_bundle(path).model_format == 'vrm'


@pytest.mark.parametrize('semantic', ['NORMAL', 'COLOR_0'])
@pytest.mark.parametrize('base', [False, True])
@pytest.mark.parametrize('complete', [False, True])
def test_vrm_partial_morph_attributes_require_a_base_fallback(tmp_path, semantic, base, complete):
    def graph(data):
        data['accessors'] = [{'componentType': 5126, 'type': 'VEC3', 'count': 3}]
        attributes = {'POSITION': 0}
        if base: attributes[semantic] = 0
        targets = [{'POSITION': 0, semantic: 0}, {'POSITION': 0}]
        if complete: targets[1][semantic] = 0
        data['meshes'] = [{'primitives': [{'attributes': attributes, 'targets': targets}]}]
        data['nodes'][0]['mesh'] = 0
    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if base or complete: assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)


@pytest.mark.parametrize('flag', [256, 512, 384])
@pytest.mark.parametrize('parent', [-1, 3, 999])
def test_pmx_append_parent_matches_the_solver_read_path(tmp_path, flag, parent):
    raw = mmd_runtime_bytes('pmx', grants={1: parent}, append_flag=flag)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    if flag == 256:
        with pytest.raises(ModelFormatError): validate_model_bundle(path)
    else:
        assert validate_model_bundle(path).model_format == 'mmd'


@pytest.mark.parametrize('depth,valid', [(64, True), (65, False), (1025, False)])
@pytest.mark.parametrize('kind', ['pmx', 'pmd'])
def test_mmd_append_chain_depth_is_bounded_independently_of_bone_parents(tmp_path, depth, valid, kind):
    entry = f'Avatar.{kind}'
    raw = mmd_runtime_bytes(kind, parents=[-1] * (depth + 1), grants={i: i - 1 for i in range(1, depth + 1)})
    path = make_native_bundle(tmp_path, kind='mmd', entry=entry, files={entry: raw})
    if valid: assert validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=64)).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match='model_accessor_budget_exceeded'):
            validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=64))


@pytest.mark.parametrize('grants', [{0: 0}, {0: 1, 1: 0, 2: 1}, {1: 0, 2: 1}])
def test_pmx_preserves_safe_append_chains_and_loader_ignored_cycles(tmp_path, grants):
    raw = mmd_runtime_bytes('pmx', grants=grants)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    assert validate_model_bundle(path).model_format == 'mmd'


def test_pmx_wide_append_graph_does_not_count_as_a_deep_chain(tmp_path):
    raw = mmd_runtime_bytes('pmx', parents=[-1] * 1026, grants={i: 0 for i in range(1, 1026)})
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmx', files={'Avatar.pmx': raw})
    assert validate_model_bundle(path, format_limits=FormatLimits(hierarchy_depth=2)).model_format == 'mmd'


@pytest.mark.parametrize('parent,valid', [(-1, False), (4, False), (0, True), (3, True)])
def test_pmd_rotation_grant_parent_uses_converted_bone_slots(tmp_path, parent, valid):
    # Three original bones plus one extra slot from the repeated IK owner.
    raw = mmd_runtime_bytes('pmd', iterations=[1, 1], grants={1: parent}, link=2)
    path = make_native_bundle(tmp_path, kind='mmd', entry='Avatar.pmd', files={'Avatar.pmd': raw})
    if valid:
        assert validate_model_bundle(path).model_format == 'mmd'
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_model_bundle(path)


@pytest.mark.parametrize('semantic', ['NORMAL', 'COLOR_0'])
@pytest.mark.parametrize('base_count,valid', [(2, False), (3, True)])
def test_vrm_morph_fallback_covers_the_position_vertices(tmp_path, semantic, base_count, valid):
    def graph(data):
        data['accessors'] = [
            {'componentType': 5126, 'type': 'VEC3', 'count': count}
            for count in (3, base_count)
        ]
        data['meshes'] = [{'primitives': [{
            'attributes': {'POSITION': 0, semantic: 1},
            'targets': [{'POSITION': 0, semantic: 0}, {'POSITION': 0}],
        }]}]
        data['nodes'][0]['mesh'] = 0

    path = make_native_bundle(tmp_path, kind='vrm', entry='Avatar.vrm', files={'Avatar.vrm': vrm_bytes(change=graph)})
    if valid:
        assert validate_model_bundle(path).model_format == 'vrm'
    else:
        with pytest.raises(ModelFormatError, match="^invalid_model_structure$"):
            validate_model_bundle(path)
