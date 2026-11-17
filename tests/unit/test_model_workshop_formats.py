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
    data["accessors"] = [dict(accessor) for _ in range(copies)]


@pytest.mark.parametrize("count,copies,backed,budget", [(2_000_000_000, 1, False, None),
    (4, 2, False, 64), (4, 2, True, 112)])
def test_vrm_sparse_allocations_are_bounded_individually_and_in_total(tmp_path, count, copies, backed, budget):
    """Count and stride bound actual zero-filled arrays and sparse buffer clones."""
    raw = vrm_bytes(change=lambda data: sparse_accessors(data, count=count, copies=copies, backed=backed))
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": raw})
    options = {} if budget is None else {"format_limits": FormatLimits(vrm_accessor_bytes=budget)}
    with pytest.raises(ModelFormatError, match="model_accessor_budget_exceeded"):
        validate_vrm_bundle(path, **options)


def test_vrm_zero_accessor_allocation_accepts_the_exact_budget(tmp_path):
    """The budget is a byte limit, not a rejection of legitimate zero-filled data."""
    raw = vrm_bytes(change=lambda data: data.update(accessors=[{"componentType": 5126, "count": 1, "type": "MAT4"}]))
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


def test_vrm_extra_documentation_is_rejected_instead_of_discarded(tmp_path):
    path = make_native_bundle(tmp_path, kind="vrm", entry="Avatar.vrm", files={"Avatar.vrm": vrm_bytes(), "LICENSE.txt": b"Read me"})
    with pytest.raises(ModelFormatError, match="unsupported_model_resource"):
        validate_vrm_bundle(path)


def pmx_bytes(texture="../Textures/face.png", *, shared=True, version=2.0, broken_weight=False):
    """Build every PMX section, retaining the real texture/material byte layout."""
    def text(value):
        raw = value.encode("utf-8")
        return struct.pack("<i", len(raw)) + raw
    raw = b"PMX " + struct.pack("<f", version) + bytes([8, 1, 0, 1, 1, 1, 1, 1, 1])
    raw += text("Fixture") + text("") * 3
    raw += struct.pack("<i", 1 if broken_weight else 0)
    if broken_weight:
        raw += bytes(32) + bytes([99]) + bytes(5)
    raw += struct.pack("<2i", 0, 1) + text(texture)
    raw += struct.pack("<i", 1) + text("Material") + text("") + bytes(65)
    raw += struct.pack("<bbBBB", 0, -1, 0, 1 if shared else 0, 0)
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
