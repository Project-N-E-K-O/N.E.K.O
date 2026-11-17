"""Native model admission against the files and loaders actually used by NEKO.

This module never extracts files, fetches dependencies, rewrites models, or
certifies rendering. Container integrity and native admission share one input.
"""

import json
import base64
import binascii
import math
import posixpath
import re
import struct
import unicodedata
from dataclasses import dataclass
from urllib.parse import quote, unquote, urljoin, urlsplit

from .bundle import BundleError, BundleLimits, inspect_bundle


class ModelFormatError(BundleError):
    """Carry a stable error code and package-relative location for the UI."""

    def __init__(self, code: str, path: str):
        super().__init__(code)
        self.code = code
        self.path = path


@dataclass(frozen=True)
class FormatLimits:
    """Bound decoded JSON separately from archive and binary resource budgets."""

    json_bytes: int = 8 * 1024 * 1024
    json_nodes: int = 200000
    json_depth: int = 64
    binary_records: int = 2000000
    binary_text_bytes: int = 1024 * 1024


def _require(condition, path: str, code: str = "invalid_model_structure") -> None:
    """Use one rejection shape for malformed structure and unsupported resources."""
    if not condition:
        raise ModelFormatError(code, path)


def _number(value) -> bool:
    """Booleans are not animation values, and JSON overflow must not become inf."""
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def _json_object(raw: bytes, path: str, limits: FormatLimits) -> dict:
    """Decode bounded JSON, rejecting duplicate keys and unsafe scalar values."""
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, path)
            result[key] = value
        return result

    def constant(_value):
        raise ModelFormatError("invalid_model_structure", path)

    _require(len(raw) <= limits.json_bytes, path, "model_json_too_large")
    try:
        value = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=pairs, parse_constant=constant)
        pending = [(value, 0)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            _require(count <= limits.json_nodes and depth <= limits.json_depth, path, "model_json_too_complex")
            if isinstance(item, dict):
                pending.extend((part, depth + 1) for pair in item.items() for part in pair)
            elif isinstance(item, list):
                pending.extend((part, depth + 1) for part in item)
            elif isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, float):
                _require(math.isfinite(item), path)
        _require(isinstance(value, dict), path)
        return value
    except (UnicodeError, ValueError, RecursionError, OverflowError) as exc:
        if isinstance(exc, ModelFormatError):
            raise
        raise ModelFormatError("invalid_model_structure", path) from exc


class _Resources:
    """Exact inventory lookup; never use host filesystem case-folding as proof."""

    def __init__(self, bundle, archive, limits: FormatLimits):
        self.bundle, self.archive, self.limits = bundle, archive, limits
        self.files = {item.path: item for item in bundle.files}

    def json(self, path: str) -> dict:
        with self.archive.open("model/" + path) as handle:
            return _json_object(handle.read(self.limits.json_bytes + 1), path, self.limits)

    def prefix(self, path: str, size: int) -> bytes:
        with self.archive.open("model/" + path) as handle:
            return handle.read(size)

    def dependency(self, reference, *, base: str = "", suffixes=(), manager: bool = False) -> str:
        """Resolve like a browser, then require the same exact package resource.

        MMD references are relative to the main file's parent. Archive members
        forbid '..', but an internal reference may use it while staying inside.
        """
        _require(isinstance(reference, str) and bool(reference), self.bundle.entrypoint, "invalid_model_reference")
        ref = reference.replace("\\", "/")
        _require(
            not ref.startswith("/") and ":" not in ref
            and not any(unicodedata.category(char).startswith("C") for char in ref),
            reference, "invalid_model_reference",
        )
        # EmotionMapping's existing resolver treats 'static/' as a shared URL.
        _require(not (manager and ref.startswith("static/")), reference, "external_model_reference")
        target = posixpath.normpath(posixpath.join(base, ref))
        _require(target != ".." and not target.startswith("../"), reference, "external_model_reference")
        try:
            url = urlsplit(urljoin("https://model.invalid/__root__/" + quote(base, safe="/") + ("/" if base else ""), ref))
            decoded = unquote(url.path, encoding="utf-8", errors="strict")
        except (ValueError, UnicodeError) as exc:
            raise ModelFormatError("invalid_model_reference", reference) from exc
        _require(
            url.scheme == "https" and url.netloc == "model.invalid" and not url.query and not url.fragment
            and posixpath.normpath(decoded) == "/__root__/" + target,
            reference, "incompatible_model_reference",
        )
        _require(target in self.files, reference, "missing_model_resource")
        _require(not suffixes or target.lower().endswith(suffixes), reference, "unsupported_model_resource")
        return target


def _allowed_files(resources: _Resources, suffixes: tuple) -> None:
    """Retain model resources and ordinary license/readme files; reject payloads."""
    for name in resources.files:
        lower = name.lower()
        documentation = lower.endswith((".txt", ".md")) or posixpath.basename(lower) in {
            "readme", "license", "licence", "copying", "notice", "authors",
        }
        _require(documentation or lower.endswith(suffixes), name, "unsupported_model_resource")


def _motion(data: dict, path: str) -> None:
    """Validate Cubism segment layout without changing lip-sync or emotion data."""
    meta, curves = data.get("Meta"), data.get("Curves")
    _require(data.get("Version") == 3 and isinstance(meta, dict) and isinstance(curves, list), path)
    _require(_number(meta.get("Duration")) and meta["Duration"] >= 0, path)
    _require(_number(meta.get("Fps")) and meta["Fps"] > 0 and type(meta.get("Loop")) is bool, path)
    segments = points = 0
    for curve in curves:
        _require(isinstance(curve, dict) and curve.get("Target") in ("Model", "Parameter", "PartOpacity"), path)
        _require(isinstance(curve.get("Id"), str) and bool(curve["Id"]), path)
        values = curve.get("Segments")
        _require(isinstance(values, list) and len(values) >= 2 and all(_number(v) for v in values), path)
        points += 1
        offset = 2
        while offset < len(values):
            opcode = values[offset]
            _require(opcode in (0, 1, 2, 3), path)
            width = 7 if opcode == 1 else 3
            _require(offset + width <= len(values), path)
            points += 3 if opcode == 1 else 1
            segments += 1
            offset += width
    for key, expected in (("CurveCount", len(curves)), ("TotalSegmentCount", segments), ("TotalPointCount", points)):
        _require(type(meta.get(key)) is int and meta[key] == expected, path)


def _expression(data: dict, path: str) -> None:
    """Check the parameter form consumed by the existing expression manager."""
    _require(data.get("Type") == "Live2D Expression" and isinstance(data.get("Parameters"), list), path)
    for parameter in data["Parameters"]:
        _require(isinstance(parameter, dict) and isinstance(parameter.get("Id"), str) and bool(parameter["Id"]), path)
        _require(_number(parameter.get("Value")) and parameter.get("Blend", "Add") in ("Add", "Multiply", "Overwrite"), path)


def _auxiliary(data: dict, path: str) -> None:
    """Check required containers of non-animation Cubism JSON resources."""
    lower = path.lower()
    # NEKO's bundled mao_pro pose uses Type instead of Version; its loader accepts it.
    legacy_pose = lower.endswith(".pose3.json") and data.get("Type") == "Live2D Pose" and "Version" not in data
    _require(data.get("Version") == 3 or legacy_pose, path)
    if lower.endswith(".physics3.json"):
        meta, settings = data.get("Meta"), data.get("PhysicsSettings")
        _require(isinstance(meta, dict) and isinstance(settings, list), path)
        _require(type(meta.get("PhysicsSettingCount")) is int and meta["PhysicsSettingCount"] == len(settings), path)
        for setting in settings:
            _require(isinstance(setting, dict), path)
            for key in ("Input", "Output", "Vertices"):
                _require(isinstance(setting.get(key), list), path)
    elif lower.endswith(".pose3.json"):
        _require(isinstance(data.get("Groups"), list), path)
        for group in data["Groups"]:
            _require(isinstance(group, list), path)
            for part in group:
                _require(isinstance(part, dict) and isinstance(part.get("Id"), str), path)
                _require(isinstance(part.get("Link", []), list) and all(isinstance(v, str) for v in part.get("Link", [])), path)
    elif lower.endswith(".cdi3.json"):
        for key in ("Parameters", "ParameterGroups", "Parts"):
            _require(isinstance(data.get(key, []), list), path)
            for item in data.get(key, []):
                _require(isinstance(item, dict) and isinstance(item.get("Id"), str), path)
    else:
        _require(isinstance(data.get("Meta"), dict) and isinstance(data.get("UserData"), list), path)


def _image(resources: _Resources, path: str) -> None:
    """Reject obvious binary masquerades; decoder/rendering remains a later test."""
    header = resources.prefix(path, 18)
    lower = path.lower()
    if lower.endswith(".png"):
        valid = header.startswith(b"\x89PNG\r\n\x1a\n")
    elif lower.endswith((".jpg", ".jpeg")):
        valid = header.startswith(b"\xff\xd8\xff")
    elif lower.endswith(".bmp"):
        valid = header.startswith(b"BM")
    elif lower.endswith((".sph", ".spa")):
        # Sphere maps keep their MMD suffix while the browser detects the image.
        valid = header.startswith((b"BM", b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff"))
    elif lower.endswith(".tga"):
        valid = len(header) == 18 and header[2] in (1, 2, 3, 9, 10, 11)
        valid = valid and int.from_bytes(header[12:14], "little") > 0 and int.from_bytes(header[14:16], "little") > 0
        valid = valid and header[16] in (8, 16, 24, 32)
    else:
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    _require(valid, path, "invalid_model_image")


def _live2d(resources: _Resources) -> None:
    """Cover FileReferences, EmotionMapping, and recursively enumerated assets."""
    entry = resources.bundle.entrypoint
    data = resources.json(entry)
    refs = data.get("FileReferences")
    _require(data.get("Version") == 3 and isinstance(refs, dict), entry)
    known = {"Moc", "Textures", "Physics", "Pose", "DisplayInfo", "UserData", "Expressions", "Motions"}
    _require(not (set(refs) - known), entry, "unsupported_model_reference")
    moc = resources.dependency(refs.get("Moc"), suffixes=(".moc3",))
    _require(resources.prefix(moc, 4) == b"MOC3", moc)
    textures = refs.get("Textures")
    _require(isinstance(textures, list) and bool(textures), entry)
    for ref in textures:
        resources.dependency(ref, suffixes=(".png", ".jpg", ".jpeg", ".webp"))
    for key, suffix in (("Physics", ".physics3.json"), ("Pose", ".pose3.json"),
                        ("DisplayInfo", ".cdi3.json"), ("UserData", ".userdata3.json")):
        if key in refs:
            resources.dependency(refs[key], suffixes=(suffix,))
    expressions, motions = refs.get("Expressions", []), refs.get("Motions", {})
    _require(isinstance(expressions, list) and isinstance(motions, dict), entry)
    for ref in expressions:
        _require(isinstance(ref, dict) and isinstance(ref.get("Name"), str), entry)
        resources.dependency(ref.get("File"), suffixes=(".exp3.json",))
    for group in motions.values():
        _require(isinstance(group, list), entry)
        for ref in group:
            _require(isinstance(ref, dict), entry)
            resources.dependency(ref.get("File"), suffixes=(".motion3.json",))
            _require(not ref.get("Sound"), entry, "unsupported_model_resource")
    mapping = data.get("EmotionMapping", {})
    _require(isinstance(mapping, dict), entry)
    for key, suffix in (("expressions", ".exp3.json"), ("motions", ".motion3.json")):
        groups = mapping.get(key, {})
        _require(isinstance(groups, dict), entry)
        for group in groups.values():
            _require(isinstance(group, list), entry)
            for ref in group:
                resources.dependency(ref, suffixes=(suffix,), manager=True)
    allowed = (".model3.json", ".moc3", ".png", ".jpg", ".jpeg", ".webp", ".motion3.json",
               ".exp3.json", ".physics3.json", ".pose3.json", ".cdi3.json", ".userdata3.json")
    _allowed_files(resources, allowed)
    # Even assets absent from FileReferences are loaded by NEKO's recursive scan.
    for name in resources.files:
        # Suffix detection is case-insensitive; actual dependency lookup stays exact.
        lower = name.lower()
        if lower.endswith(".motion3.json"):
            _motion(resources.json(name), name)
        elif lower.endswith(".exp3.json"):
            _expression(resources.json(name), name)
        elif lower.endswith((".physics3.json", ".pose3.json", ".cdi3.json", ".userdata3.json")):
            _auxiliary(resources.json(name), name)
        elif name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            _image(resources, name)


def validate_live2d_bundle(path, *, limits: BundleLimits = BundleLimits(),
                           format_limits: FormatLimits = FormatLimits()):
    """Admit a Live2D bundle using the container's open, stable source object."""
    with inspect_bundle(path, limits=limits) as (bundle, archive):
        _require(bundle.model_format == "live2d", bundle.entrypoint, "invalid_model_format")
        _live2d(_Resources(bundle, archive, format_limits))
    return bundle


def _uint(value, path: str) -> int:
    """Reject bool/float lengths before using them as binary offsets or counts."""
    _require(type(value) is int and value >= 0, path)
    return value


def _index(value, items: list, path: str) -> int:
    """Every glTF index is checked before following an internal dependency."""
    _require(_uint(value, path) < len(items), path)
    return value


def _data_uri(uri, path: str, mime_types: tuple) -> int:
    """Only bounded base64 self-contained data is accepted; no network fallback."""
    _require(isinstance(uri, str) and uri.startswith("data:"), path, "external_model_reference")
    prefix, separator, payload = uri.partition(",")
    _require(separator and prefix in tuple("data:" + mime + ";base64" for mime in mime_types), path)
    try:
        return len(base64.b64decode(payload, validate=True))
    except (ValueError, binascii.Error) as exc:
        raise ModelFormatError("invalid_model_structure", path) from exc


def _gltf_extensions(data: dict, path: str) -> None:
    """Match NEKO's GLTFLoader plus VRMLoaderPlugin, without optional decoders."""
    supported = {
        "VRM", "VRMC_vrm", "VRMC_springBone", "VRMC_node_constraint",
        "VRMC_materials_mtoon", "VRMC_materials_hdr_emissiveMultiplier",
        "KHR_materials_unlit", "KHR_texture_transform", "KHR_mesh_quantization", "KHR_lights_punctual",
        "KHR_materials_clearcoat", "KHR_materials_dispersion", "KHR_materials_ior",
        "KHR_materials_sheen", "KHR_materials_specular", "KHR_materials_transmission",
        "KHR_materials_iridescence", "KHR_materials_volume", "KHR_materials_emissive_strength",
        "KHR_materials_anisotropy", "KHR_materials_bump", "EXT_texture_webp", "EXT_texture_avif",
        "EXT_mesh_gpu_instancing",
    }
    for key in ("extensionsUsed", "extensionsRequired"):
        values = data.get(key, [])
        _require(isinstance(values, list) and all(isinstance(v, str) and v in supported for v in values), path,
                 "unsupported_model_extension")
    # Draco, Meshopt and BasisU require decoders which vrm-core.js does not set.
    pending = [data]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            extensions = value.get("extensions", {})
            _require(isinstance(extensions, dict) and not (set(extensions) - supported), path,
                     "unsupported_model_extension")
            # extras and VRM meta contain author text, not loader dependencies.
            pending.extend(part for key, part in value.items() if key not in {"extras", "meta"})
        elif isinstance(value, list):
            pending.extend(value)


def _vrm(resources: _Resources) -> None:
    """Check GLB framing and self-contained glTF/VRM loading dependencies."""
    entry = resources.bundle.entrypoint
    _require(set(resources.files) == {entry}, entry, "unsupported_model_resource")
    size = resources.files[entry].byte_size
    with resources.archive.open("model/" + entry) as handle:
        header = handle.read(20)
        _require(len(header) == 20, entry)
        magic, version, total, json_size, chunk_type = struct.unpack("<4s4I", header)
        _require(magic == b"glTF" and version == 2 and total == size and chunk_type == 0x4E4F534A, entry)
        _require(0 < json_size <= resources.limits.json_bytes and json_size % 4 == 0 and 20 + json_size <= size, entry)
        data = _json_object(handle.read(json_size), entry, resources.limits)
        binary_size = 0
        if 20 + json_size < size:
            binary_header = handle.read(8)
            _require(len(binary_header) == 8, entry)
            binary_size, binary_type = struct.unpack("<2I", binary_header)
            _require(binary_type == 0x004E4942 and binary_size % 4 == 0 and 28 + json_size + binary_size == size, entry)
        # Container verification already read/hash-checked the whole binary chunk.
    asset = data.get("asset")
    _require(isinstance(asset, dict) and asset.get("version") == "2.0" and asset.get("minVersion", "2.0") == "2.0", entry)
    _gltf_extensions(data, entry)
    extensions = data.get("extensions", {})
    kinds = [name for name in ("VRM", "VRMC_vrm") if name in extensions]
    _require(len(kinds) == 1 and isinstance(extensions[kinds[0]], dict), entry, "invalid_vrm_extension")
    vrm = extensions[kinds[0]]
    if kinds[0] == "VRMC_vrm":
        # The bundled three-vrm explicitly supports both 1.0 and 1.0-beta.
        _require(vrm.get("specVersion") in ("1.0", "1.0-beta"), entry, "unsupported_model_extension")
    else:
        _require(vrm.get("specVersion", "0.0") == "0.0", entry, "unsupported_model_extension")
    buffers, views, images, nodes = (data.get(key, []) for key in ("buffers", "bufferViews", "images", "nodes"))
    _require(all(isinstance(value, list) for value in (buffers, views, images, nodes)) and bool(buffers), entry)
    buffer_sizes = []
    for index, buffer in enumerate(buffers):
        _require(isinstance(buffer, dict) and buffer.get("type", "arraybuffer") == "arraybuffer", entry)
        length = _uint(buffer.get("byteLength"), entry)
        if "uri" in buffer:
            actual = _data_uri(buffer["uri"], entry, ("application/octet-stream", "application/gltf-buffer"))
            _require(actual == length, entry)
        else:
            _require(index == 0 and binary_size - 3 <= length <= binary_size, entry)
        buffer_sizes.append(length)
    for view in views:
        _require(isinstance(view, dict), entry)
        index = _index(view.get("buffer"), buffers, entry)
        offset, length = _uint(view.get("byteOffset", 0), entry), _uint(view.get("byteLength"), entry)
        _require(offset + length <= buffer_sizes[index], entry)
        if "byteStride" in view:
            _require(4 <= _uint(view["byteStride"], entry) <= 252 and view["byteStride"] % 4 == 0, entry)
    for image in images:
        _require(isinstance(image, dict) and ("uri" in image) != ("bufferView" in image), entry)
        if "uri" in image:
            _data_uri(image["uri"], entry, ("image/png", "image/jpeg", "image/webp", "image/avif"))
        else:
            _index(image["bufferView"], views, entry)
            _require(image.get("mimeType") in ("image/png", "image/jpeg", "image/webp", "image/avif"), entry)
    humanoid, meta = vrm.get("humanoid"), vrm.get("meta")
    _require(isinstance(humanoid, dict) and isinstance(meta, dict), entry)
    bones = humanoid.get("humanBones")
    if kinds[0] == "VRMC_vrm":
        _require(isinstance(bones, dict) and bool(bones), entry)
        bones = list(bones.values())
    else:
        _require(isinstance(bones, list) and bool(bones), entry)
    for bone in bones:
        _require(isinstance(bone, dict), entry)
        _index(bone.get("node"), nodes, entry)
    # Validate common graph indices; shader/humanoid behavior still needs rendering.
    accessors, meshes, skins, scenes = (data.get(key, []) for key in ("accessors", "meshes", "skins", "scenes"))
    _require(all(isinstance(value, list) for value in (accessors, meshes, skins, scenes)), entry)
    for accessor in accessors:
        _require(isinstance(accessor, dict), entry)
        count = _uint(accessor.get("count"), entry)
        component_type = _uint(accessor.get("componentType"), entry)
        component = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}.get(component_type)
        shape = accessor.get("type")
        _require(component is not None and shape in ("SCALAR", "VEC2", "VEC3", "VEC4", "MAT2", "MAT3", "MAT4"), entry)
        if shape.startswith("MAT"):
            # glTF aligns every matrix column to four bytes for small components.
            columns = int(shape[-1])
            element_size = columns * ((columns * component + 3) // 4 * 4)
        else:
            element_size = component * (1 if shape == "SCALAR" else int(shape[-1]))
        if "bufferView" in accessor:
            view = views[_index(accessor["bufferView"], views, entry)]
            offset = _uint(accessor.get("byteOffset", 0), entry)
            stride = view.get("byteStride", element_size)
            _require(offset % component == 0 and stride >= element_size, entry)
            _require(offset + (count - 1) * stride + element_size <= view["byteLength"] if count else offset <= view["byteLength"], entry)
        else:
            _require("sparse" in accessor, entry)
        if "sparse" in accessor:
            sparse = accessor["sparse"]
            _require(isinstance(sparse, dict), entry)
            sparse_count = _uint(sparse.get("count"), entry)
            _require(sparse_count <= count, entry)
            for key in ("indices", "values"):
                part = sparse.get(key)
                _require(isinstance(part, dict), entry)
                view = views[_index(part.get("bufferView"), views, entry)]
                width = {5121: 1, 5123: 2, 5125: 4}.get(_uint(part.get("componentType"), entry)) if key == "indices" else element_size
                _require(width is not None, entry)
                _require(_uint(part.get("byteOffset", 0), entry) + sparse_count * width <= view["byteLength"], entry)
    for node in nodes:
        _require(isinstance(node, dict) and isinstance(node.get("children", []), list), entry)
        for child in node.get("children", []):
            _index(child, nodes, entry)
        for field, table in (("mesh", meshes), ("skin", skins)):
            if field in node:
                _index(node[field], table, entry)
    # A cyclic child graph cannot become a scene tree in the current loader.
    colors = [0] * len(nodes)
    for start in range(len(nodes)):
        stack = [(start, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                colors[node] = 2
            elif colors[node] != 2:
                _require(colors[node] == 0, entry)
                colors[node] = 1
                stack.append((node, True))
                stack.extend((child, False) for child in nodes[node].get("children", []))
    for mesh in meshes:
        _require(isinstance(mesh, dict) and isinstance(mesh.get("primitives"), list), entry)
        for primitive in mesh["primitives"]:
            _require(isinstance(primitive, dict) and isinstance(primitive.get("attributes"), dict), entry)
            for accessor in primitive["attributes"].values():
                _index(accessor, accessors, entry)
            if "indices" in primitive:
                _index(primitive["indices"], accessors, entry)
    for skin in skins:
        _require(isinstance(skin, dict) and isinstance(skin.get("joints"), list), entry)
        for joint in skin["joints"]:
            _index(joint, nodes, entry)
    for scene in scenes:
        _require(isinstance(scene, dict) and isinstance(scene.get("nodes", []), list), entry)
        for node in scene.get("nodes", []):
            _index(node, nodes, entry)
    if "scene" in data:
        _index(data["scene"], scenes, entry)


def validate_vrm_bundle(path, *, limits: BundleLimits = BundleLimits(),
                        format_limits: FormatLimits = FormatLimits()):
    """Admit exactly one self-contained VRM while preserving its original bytes."""
    with inspect_bundle(path, limits=limits) as (bundle, archive):
        _require(bundle.model_format == "vrm", bundle.entrypoint, "invalid_model_format")
        _vrm(_Resources(bundle, archive, format_limits))
    return bundle


class _MmdReader:
    """Bounded sequential binary reader; counts cannot seek past actual bytes."""

    def __init__(self, handle, size: int, path: str, limits: FormatLimits):
        self.handle, self.size, self.path, self.limits = handle, size, path, limits
        self.offset = self.records = 0
        self.encoding = "utf-8"

    def read(self, size: int) -> bytes:
        _require(0 <= size <= self.size - self.offset, self.path)
        raw = self.handle.read(size)
        _require(len(raw) == size, self.path)
        self.offset += size
        return raw

    def skip(self, size: int) -> None:
        # Large geometry sections are consumed in blocks, not copied into memory.
        _require(0 <= size <= self.size - self.offset, self.path)
        while size:
            block = min(size, 1024 * 1024)
            self.read(block)
            size -= block

    def scalar(self, code: str):
        return struct.unpack("<" + code, self.read(struct.calcsize("<" + code)))[0]

    def count(self, code: str = "i", minimum: int = 1) -> int:
        count = self.scalar(code)
        self.records += max(count, 0)
        _require(0 <= count <= (self.size - self.offset) // minimum, self.path)
        _require(self.records <= self.limits.binary_records, self.path, "model_binary_too_complex")
        return count

    def text(self, fixed: int | None = None) -> str:
        size = self.scalar("i") if fixed is None else fixed
        _require(0 <= size <= self.limits.binary_text_bytes, self.path, "model_text_too_large")
        raw = self.read(size)
        if fixed is not None:
            raw = raw.split(b"\x00", 1)[0]
        try:
            return raw.decode(self.encoding, errors="strict")
        except UnicodeError as exc:
            raise ModelFormatError("invalid_model_encoding", self.path) from exc

    def index(self, width: int, *, signed: bool = True) -> int:
        return self.scalar(({1: "b", 2: "h", 4: "i"} if signed else {1: "B", 2: "H", 4: "I"})[width])


_MMD_IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tga", ".sph", ".spa")


def _mmd_texture(resources: _Resources, value: str) -> None:
    """Use the main file's parent, retaining legal internal parent references."""
    resources.dependency(value, base=posixpath.dirname(resources.bundle.entrypoint), suffixes=_MMD_IMAGES)


def _pmx(resources: _Resources, reader: _MmdReader) -> None:
    """Walk the PMX layout used by the bundled PmxReader, without rewriting it."""
    path = reader.path
    _require(reader.read(4) == b"PMX ", path)
    version = reader.scalar("f")
    _require(abs(version - 2.0) < 0.00001 or abs(version - 2.1) < 0.00001, path)
    _require(reader.scalar("B") == 8, path, "unsupported_model_structure")
    encoding, additional = reader.scalar("B"), reader.scalar("B")
    widths = [reader.scalar("B") for _ in range(6)]
    _require(encoding in (0, 1) and additional <= 4 and all(v in (1, 2, 4) for v in widths), path)
    vertex, texture, material, bone, morph, rigid = widths
    reader.encoding = "utf-16le" if encoding == 0 else "utf-8"
    for _ in range(4):
        reader.text()
    vertices = reader.count(minimum=33 + additional * 16 + bone + 4)
    weights = {0: bone, 1: 2 * bone + 4, 2: 4 * bone + 16, 3: 2 * bone + 40, 4: 4 * bone + 16}
    for _ in range(vertices):
        reader.skip(32 + additional * 16)
        kind = reader.scalar("B")
        _require(kind in weights, path)
        reader.skip(weights[kind] + 4)
    indices = reader.count(minimum=vertex)
    _require(indices % 3 == 0, path)
    for _ in range(indices):
        _require(reader.index(vertex, signed=False) < vertices, path)
    textures = [reader.text() for _ in range(reader.count(minimum=4))]

    def dependency(index: int, *, active: bool = True) -> None:
        _require(-1 <= index < len(textures), path)
        if active and index != -1:
            _mmd_texture(resources, textures[index])

    covered = 0
    for _ in range(reader.count(minimum=79)):
        reader.text()
        reader.text()
        # Diffuse/specular/shininess/ambient, draw flag, edge color and edge size.
        reader.skip(65)
        diffuse, sphere = reader.index(texture), reader.index(texture)
        sphere_mode, shared = reader.scalar("B"), reader.scalar("B")
        _require(sphere_mode in (0, 1, 2, 3) and shared in (0, 1), path)
        dependency(diffuse)
        dependency(sphere, active=sphere_mode in (1, 2))
        if shared:
            _require(reader.scalar("B") < 10, path)
        else:
            # A custom PMX toon named toon01.bmp is still a package dependency.
            dependency(reader.index(texture))
        reader.text()
        count = reader.scalar("i")
        _require(0 <= count <= indices and count % 3 == 0, path)
        covered += count
    _require(covered == indices, path)
    for _ in range(reader.count(minimum=26 + bone)):
        reader.text()
        reader.text()
        reader.skip(12 + bone + 4)
        flags = reader.scalar("H")
        reader.skip(bone if flags & 1 else 12)
        if flags & (256 | 512):
            reader.skip(bone + 4)
        if flags & 1024:
            reader.skip(12)
        if flags & 2048:
            reader.skip(24)
        if flags & 8192:
            reader.skip(4)
        if flags & 32:
            reader.skip(bone + 8)
            for _ in range(reader.count(minimum=bone + 1)):
                reader.skip(bone)
                limited = reader.scalar("B")
                _require(limited in (0, 1), path)
                if limited:
                    reader.skip(24)
    # Each entry contains its typed index followed by the loader's fixed payload.
    morph_sizes = {0: morph + 4, 1: vertex + 12, 2: bone + 28,
                   3: vertex + 16, 4: vertex + 16, 5: vertex + 16,
                   6: vertex + 16, 7: vertex + 16, 8: material + 113,
                   9: morph + 4, 10: rigid + 25}
    for _ in range(reader.count(minimum=14)):
        reader.text()
        reader.text()
        reader.skip(1)
        kind = reader.scalar("B")
        _require(kind in morph_sizes, path)
        width = morph_sizes[kind]
        reader.skip(reader.count(minimum=width) * width)
    for _ in range(reader.count(minimum=13)):
        reader.text()
        reader.text()
        reader.skip(1)
        for _ in range(reader.count(minimum=2)):
            kind = reader.scalar("B")
            _require(kind in (0, 1), path)
            reader.skip(bone if kind == 0 else morph)
    for _ in range(reader.count(minimum=8 + bone + 61)):
        reader.text()
        reader.text()
        # Bone index, collision/shape fields, three vectors and physics scalars.
        reader.skip(bone + 61)
    for _ in range(reader.count(minimum=9 + rigid * 2 + 96)):
        reader.text()
        reader.text()
        # Joint kind, two bodies and eight position/rotation/limit/spring vectors.
        reader.skip(1 + rigid * 2 + 96)
    if version > 2.05:
        for _ in range(reader.count(minimum=133 + material)):
            reader.text()
            reader.text()
            # Soft-body header plus fixed config/cluster/iteration/material blocks.
            reader.skip(125 + material)
            reader.skip(reader.count(minimum=rigid + vertex + 1) * (rigid + vertex + 1))
            reader.skip(reader.count(minimum=vertex) * vertex)


def _pmd_normalized(value: str) -> str:
    """Match PmdReader's lowercase path table solely for table-index conversion."""
    parts = []
    for part in value.replace("\\", "/").split("/"):
        if part == "..":
            if parts:
                parts.pop()
        elif part != ".":
            parts.append(part)
    return "/".join(parts).lower()


def _pmd(resources: _Resources, reader: _MmdReader) -> None:
    """Keep PMD's optional tail and toon conversion separate from PMX flags."""
    path = reader.path
    _require(reader.read(3) == b"Pmd" and reader.scalar("f") == 1.0, path)
    reader.encoding = "cp932"
    reader.text(20)
    reader.text(256)
    vertices = reader.count("I", 38)
    reader.skip(vertices * 38)
    indices = reader.count("I", 2)
    _require(indices % 3 == 0, path)
    for _ in range(indices):
        _require(reader.scalar("H") < vertices, path)
    materials = []
    covered = 0
    for _ in range(reader.count("I", 70)):
        # PMD uses a different fixed material layout and a signed toon table index.
        reader.skip(44)
        toon = reader.scalar("b")
        _require(-1 <= toon < 10, path)
        reader.skip(1)
        count = reader.scalar("I")
        _require(count <= indices and count % 3 == 0, path)
        covered += count
        diffuse = sphere = ""
        for value in reader.text(20).split("*"):
            if value.lower().endswith((".sph", ".spa")):
                sphere = value
            else:
                diffuse = value
        materials.append((toon, diffuse, sphere))
    _require(covered == indices, path)
    # The remaining sections are still consumed so truncation cannot hide after textures.
    bones = reader.count("H", 39)
    reader.skip(bones * 39)
    for _ in range(reader.count("H", 11)):
        reader.skip(4)
        links = reader.scalar("B")
        reader.skip(6 + links * 2)
    morphs = reader.count("H", 25)
    for _ in range(morphs):
        reader.text(20)
        count = reader.count("I", 16)
        reader.skip(1 + count * 16)
    reader.skip(reader.count("B", 2) * 2)
    frames = reader.count("B", 50)
    reader.skip(frames * 50)
    reader.skip(reader.count("I", 3) * 3)
    textures = []
    if reader.offset < reader.size:
        english = reader.scalar("B")
        _require(english in (0, 1), path)
        if english:
            reader.skip(276 + bones * 20 + max(0, morphs - 1) * 20 + frames * 50)
        textures = [reader.text(100) for _ in range(10)]
        if reader.offset < reader.size:
            reader.skip(reader.count("I", 83) * 83)
            reader.skip(reader.count("I", 124) * 124)
    # PMD resolves toon indices before appending diffuse/sphere paths to its table.
    shared = []
    for toon, _diffuse, _sphere in materials:
        normalized = _pmd_normalized(textures[toon]) if 0 <= toon < len(textures) else ""
        shared.append(bool(re.search(r"toon(10|0[0-9])\.bmp", normalized)))
    table = {_pmd_normalized(value): index for index, value in enumerate(textures)}
    active = []
    for _toon, diffuse, sphere in materials:
        for value in (diffuse, sphere):
            if value:
                key = _pmd_normalized(value)
                if key not in table:
                    table[key] = len(textures)
                    textures.append(value)
                active.append(table[key])
    for index, (toon, _diffuse, _sphere) in enumerate(materials):
        if toon != -1 and not shared[index]:
            _require(toon < len(textures), path)
            active.append(toon)
    for index in set(active):
        # Check the actual converted table entry, not the original case-folded key.
        _mmd_texture(resources, textures[index])


def _mmd(resources: _Resources) -> None:
    """Validate binary framing plus all texture paths consumed by the loader."""
    _allowed_files(resources, (".pmx", ".pmd") + _MMD_IMAGES)
    entry = resources.bundle.entrypoint
    with resources.archive.open("model/" + entry) as handle:
        reader = _MmdReader(handle, resources.files[entry].byte_size, entry, resources.limits)
        (_pmx if entry.endswith(".pmx") else _pmd)(resources, reader)
        _require(reader.offset == reader.size, entry, "unsupported_model_structure")
    for name in resources.files:
        if name.lower().endswith(_MMD_IMAGES):
            _image(resources, name)


def validate_mmd_bundle(path, *, limits: BundleLimits = BundleLimits(),
                        format_limits: FormatLimits = FormatLimits()):
    """Admit PMX/PMD in place; naming adaptations belong to the installer."""
    with inspect_bundle(path, limits=limits) as (bundle, archive):
        _require(bundle.model_format == "mmd", bundle.entrypoint, "invalid_model_format")
        _mmd(_Resources(bundle, archive, format_limits))
    return bundle


def validate_model_bundle(path, *, limits: BundleLimits = BundleLimits(),
                          format_limits: FormatLimits = FormatLimits()):
    """Single format dispatcher for both final market packages and offline import."""
    with inspect_bundle(path, limits=limits) as (bundle, archive):
        {"live2d": _live2d, "vrm": _vrm, "mmd": _mmd}[bundle.model_format](_Resources(bundle, archive, format_limits))
    return bundle
