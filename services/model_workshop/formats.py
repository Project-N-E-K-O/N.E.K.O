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
from collections import Counter
from dataclasses import dataclass, replace
from graphlib import CycleError, TopologicalSorter
from urllib.parse import quote, unquote, urljoin, urlsplit

from .bundle import BundleError, BundleLimits, CompatibilityWarning, inspect_bundle, _key


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
    hierarchy_depth: int = 1024
    # Bound bufferViews, private accessors and converted geometry independently of GLB size.
    vrm_accessor_bytes: int = 256 * 1024 * 1024
    # Reserve worst-case row padding without rejecting ordinary multi-expression faces.
    vrm_morph_bytes: int = 512 * 1024 * 1024
    vrm_morph_weights: int = 8000000
    vrm_skin_weight_work: int = 10000000
    vrm_instanced_vertices: int = 5000000
    vrm_spring_bone_work: int = 500000
    live2d_physics_work: int = 3000000
    mmd_morph_bytes: int = 256 * 1024 * 1024
    mmd_tga_bytes: int = 512 * 1024 * 1024
    mmd_morph_work: int = 10000000
    mmd_ik_iterations: int = 4096
    mmd_ik_work: int = 100000
    pmd_ik_scan_work: int = 4000000


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
        self.file_keys = {_key(name): name for name in self.files}
        self.warnings = []
        self.warning_count = 0

    def warn(self, code: str, path: str) -> None:
        self.warning_count += 1
        if len(self.warnings) < 100:
            self.warnings.append(CompatibilityWarning(code, path))

    def admitted(self):
        return replace(self.bundle, compatibility_warnings=tuple(self.warnings),
                       compatibility_warning_count=self.warning_count)

    def json(self, path: str) -> dict:
        with self.archive.open("model/" + path) as handle:
            return _json_object(handle.read(self.limits.json_bytes + 1), path, self.limits)

    def prefix(self, path: str, size: int) -> bytes:
        with self.archive.open("model/" + path) as handle:
            return handle.read(size)

    def dependency(self, reference, *, base: str = "", suffixes=(), manager: bool = False,
                   optional: bool = False) -> str:
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
        runtime_path = posixpath.normpath(decoded)
        compatible = (url.scheme == "https" and url.netloc == "model.invalid" and not url.query and not url.fragment
                      and runtime_path == "/__root__/" + target)
        if optional:
            # Missing effects and literal '#' filenames do not prevent rendering.
            # Still prove that the URL the loader will request stays in this model.
            _require(url.scheme == "https" and url.netloc == "model.invalid"
                     and runtime_path.startswith("/__root__/"), reference, "external_model_reference")
            if not compatible or target not in self.files:
                self.warn("live2d_optional_resource_unavailable", reference)
            # The browser strips fragments/queries and decodes URL spelling.
            # Case-insensitive filesystems can also open an inventory alias.
            # Validate the object the loader can open. If neither spelling
            # exists, check the URL path rather than its query or fragment.
            runtime_target = runtime_path.removeprefix("/__root__/")
            target = self.file_keys.get(_key(runtime_target), target if target in self.files else runtime_target)
        else:
            _require(compatible, reference, "incompatible_model_reference")
            _require(target in self.files, reference, "missing_model_resource")
        _require(not suffixes or target.lower().endswith(suffixes), reference, "unsupported_model_resource")
        return target


def _allowed_files(resources: _Resources, suffixes: tuple, *, filenames: tuple = (), paths=()) -> None:
    """Retain model resources and ordinary license/readme files; reject payloads."""
    for name in resources.files:
        lower = name.lower()
        documentation = lower.endswith((".txt", ".md")) or posixpath.basename(lower) in {
            "readme", "license", "licence", "copying", "notice", "authors",
        }
        # Exact companion basenames must not admit similarly suffixed payloads.
        _require(documentation or lower.endswith(suffixes) or posixpath.basename(lower) in filenames or name in paths,
                 name, "unsupported_model_resource")


def _motion(data: dict, path: str) -> tuple[str, ...]:
    """Separate loader-tolerated metadata from unsafe layout; never rewrite curves."""
    meta, curves = data.get("Meta"), data.get("Curves")
    # The bundled Cubism parser also loads Version 0 motions used by yui.
    _require(type(data.get("Version")) is int and data["Version"] in (0, 3)
             and isinstance(meta, dict) and isinstance(curves, list), path)
    _require(_number(meta.get("Duration")) and meta["Duration"] >= 0, path)
    _require(_number(meta.get("Fps")) and meta["Fps"] > 0, path)
    _require("Loop" not in meta or type(meta["Loop"]) is bool, path)
    event_count = meta.get("UserDataCount", 0)
    events = data.get("UserData", [])
    if events is None and event_count == 0:
        events = []
    _require(type(event_count) is int and isinstance(events, list) and 0 <= event_count <= len(events), path)
    for event in events[:event_count]:
        _require(isinstance(event, dict) and _number(event.get("Time")) and isinstance(event.get("Value"), str), path)
    segments = points = slots = 0
    warnings = set()
    for curve in curves:
        _require(isinstance(curve, dict) and isinstance(curve.get("Target"), str) and bool(curve["Target"]), path)
        if curve["Target"] not in ("Model", "Parameter", "PartOpacity"):
            warnings.add("live2d_unknown_motion_target")
        _require(isinstance(curve.get("Id"), str) and bool(curve["Id"]), path)
        values = curve.get("Segments")
        _require(isinstance(values, list) and all(_number(v) for v in values), path)
        if not values:
            warnings.add("live2d_empty_motion_curve")
            continue
        _require(len(values) >= 5, path)
        slots += len(values)
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
    # The bundled evaluator falls back to points[0] for an empty curve.
    # A mixed motion can render; an entirely empty point table cannot.
    if "live2d_empty_motion_curve" in warnings:
        _require(points > 0, path)
    _require(type(meta.get("CurveCount")) is int and meta["CurveCount"] == len(curves), path)
    for key, expected in (("TotalSegmentCount", segments), ("TotalPointCount", points)):
        # static/libs/index.min.js: CubismMotion.parse writes points[index] into
        # a plain JS array; segments use Array.from({length: TotalSegmentCount}).
        # The bundled JS parser grows points dynamically, but preallocates
        # segments using Meta. Reject underallocation and bound both declarations
        # by the actual numeric input rather than trusting claimed allocation sizes.
        _require(type(meta.get(key)) is int and 0 <= meta[key] <= slots, path)
        if key == "TotalSegmentCount":
            _require(meta[key] >= segments, path)
        if meta[key] != expected:
            warnings.add("live2d_motion_count_mismatch")
    return tuple(sorted(warnings))


def _expression(data: dict, path: str) -> None:
    """Check the parameter form consumed by the existing expression manager."""
    _require(data.get("Type") == "Live2D Expression" and isinstance(data.get("Parameters"), list), path)
    for parameter in data["Parameters"]:
        _require(isinstance(parameter, dict) and isinstance(parameter.get("Id"), str) and bool(parameter["Id"]), path)
        _require(_number(parameter.get("Value")) and parameter.get("Blend", "Add") in ("Add", "Multiply", "Overwrite"), path)


def _physics_entries_compatible(data: dict) -> bool:
    """Malformed optional effects warn; the model's original bytes stay intact."""
    def vector(value):
        return isinstance(value, dict) and all(_number(value.get(key)) for key in ("X", "Y"))

    forces = data["Meta"].get("EffectiveForces", {})
    if not isinstance(forces, dict) or not all(vector(forces.get(key)) for key in ("Gravity", "Wind")):
        return False
    for setting in data["PhysicsSettings"]:
        if not setting["Vertices"]:
            return False
        normalization = setting.get("Normalization", {})
        if not isinstance(normalization, dict):
            return False
        for key in ("Position", "Angle"):
            bounds = normalization.get(key)
            if not isinstance(bounds, dict) or not all(_number(bounds.get(field)) for field in ("Minimum", "Maximum", "Default")):
                return False
        for key, destination in (("Input", "Source"), ("Output", "Destination")):
            for item in setting[key]:
                if not isinstance(item, dict) or not isinstance(item.get(destination), dict):
                    return False
                if not isinstance(item[destination].get("Id"), str) or not item[destination]["Id"]:
                    return False
                if not _number(item.get("Weight")) or type(item.get("Reflect", False)) is not bool or item.get("Type") not in ("X", "Y", "Angle"):
                    return False
                if key == "Output" and (not _number(item.get("Scale")) or type(item.get("VertexIndex")) is not int
                                        or not 1 <= item["VertexIndex"] < len(setting["Vertices"])):
                    return False
        for vertex in setting["Vertices"]:
            if not isinstance(vertex, dict) or not vector(vertex.get("Position")):
                return False
            if not all(_number(vertex.get(key)) for key in ("Mobility", "Delay", "Acceleration", "Radius")):
                return False
    return True


def _auxiliary(data: dict, path: str, *, kind: str = "", limits: FormatLimits) -> bool:
    """Check required containers of non-animation Cubism JSON resources."""
    lower = kind or "." + posixpath.basename(path).lower()
    # NEKO's bundled mao_pro pose uses Type instead of Version; its loader accepts it.
    legacy_pose = lower.endswith(".pose3.json") and data.get("Type") == "Live2D Pose" and "Version" not in data
    _require(data.get("Version") == 3 or legacy_pose, path)
    if lower.endswith(".physics3.json"):
        meta, settings = data.get("Meta"), data.get("PhysicsSettings")
        _require(isinstance(meta, dict) and isinstance(settings, list), path)
        _require(type(meta.get("PhysicsSettingCount")) is int and meta["PhysicsSettingCount"] == len(settings), path)
        # CubismPhysics.evaluate in static/libs/index.min.js catches up at most
        # five seconds. Admission caps this at 1200 simulation steps (240 Hz).
        fps = meta.get("Fps") or 0
        _require(_number(fps) and 0 <= fps <= 240, path, "model_physics_budget_exceeded")
        for setting in settings:
            _require(isinstance(setting, dict), path)
            for key in ("Input", "Output", "Vertices"):
                _require(isinstance(setting.get(key), list), path)
        # evaluate() slices each setting's remaining input/output/particle arrays
        # on every step. Each entry is copied once for each preceding rig plus
        # its own rig. Bound steady-frame work, retaining room for 300 ordinary
        # rigs even at 240 Hz; the separate Fps cap bounds catch-up step count.
        entries = sum((index + 1) * (len(setting["Input"]) + 2 * len(setting["Output"]) + len(setting["Vertices"]))
                      for index, setting in enumerate(settings))
        _require(entries * max(1, fps / 60) <= limits.live2d_physics_work,
                 path, "model_physics_budget_exceeded")
        # static/libs/index.min.js: CubismPhysics.parse walks the actual arrays;
        # inputs/particles use push, outputs use plain array indexing. The
        # TotalInputCount, TotalOutputCount and VertexCount getters are never called;
        # PhysicsSettingCount above bounds its only metadata-driven parse loop.
        return _physics_entries_compatible(data)
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
    return True


@dataclass(frozen=True)
class _TgaLayout:
    pixels: int
    pixel_bytes: int
    offset: int
    rle: bool

    @property
    def decoded_bytes(self) -> int:
        # TGALoader keeps both its decoded source pixels and RGBA output for RLE.
        return self.pixels * (4 + (self.pixel_bytes if self.rle else 0))


def _image(resources: _Resources, path: str) -> _TgaLayout | None:
    """Check the image signature without reading its pixel payload."""
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
        _require(len(header) == 18, path, "invalid_model_image")
        width, height = struct.unpack_from("<HH", header, 12)
        kind, bits = header[2], header[16]
        palette_length = 0
        if kind in (1, 9):
            palette_length = struct.unpack_from("<H", header, 5)[0]
            valid = bits == 8 and header[1] == 1 and header[7] == 24 and 0 < palette_length <= 256
        elif kind in (3, 11):
            valid = bits in (8, 16) and header[1] == 0
        else:
            valid = kind in (2, 10) and bits in (16, 24, 32) and header[1] == 0
        _require(valid and width > 0 and height > 0, path, "invalid_model_image")
        pixels, pixel_bytes = width * height, bits // 8
        offset = 18 + header[0] + palette_length * 3
        _require(offset <= resources.files[path].byte_size, path, "invalid_model_image")
        return _TgaLayout(pixels, pixel_bytes, offset, kind in (9, 10, 11))
    else:
        valid = header.startswith(b"RIFF") and header[8:12] == b"WEBP"
    _require(valid, path, "invalid_model_image")
    return None


def _validate_tga_pixels(resources: _Resources, path: str, layout: _TgaLayout) -> None:
    if not layout.rle:
        _require(resources.files[path].byte_size - layout.offset >= layout.pixels * layout.pixel_bytes,
                 path, "invalid_model_image")
        return
    with resources.archive.open("model/" + path) as handle:
        handle.seek(layout.offset)
        block_size = 64 * 1024
        max_packet_size = 1 + 128 * layout.pixel_bytes
        payload = b""
        cursor = 0
        eof = False
        unread_pixels = layout.pixels
        while unread_pixels:
            if not eof and len(payload) - cursor < max_packet_size:
                block = handle.read(block_size)
                payload = payload[cursor:] + block
                cursor = 0
                eof = len(block) < block_size
            _require(cursor < len(payload), path, "invalid_model_image")
            packet = payload[cursor]
            count = (packet & 0x7f) + 1
            cursor += 1 + (layout.pixel_bytes if packet & 0x80 else count * layout.pixel_bytes)
            _require(count <= unread_pixels and cursor <= len(payload),
                     path, "invalid_model_image")
            unread_pixels -= count


def _require_morph_weights(weights, count: int, entry: str) -> None:
    _require(isinstance(weights, list) and len(weights) == count and all(_number(value) for value in weights), entry)


def _live2d(resources: _Resources) -> None:
    """Cover FileReferences, EmotionMapping, and recursively enumerated assets."""
    entry = resources.bundle.entrypoint
    data = resources.json(entry)
    refs = data.get("FileReferences")
    _require(data.get("Version") == 3 and isinstance(refs, dict), entry)
    known = {"Moc", "Textures", "Physics", "PhysicsV2", "Pose", "DisplayInfo", "UserData", "Expressions", "Motions"}
    _require(not (set(refs) - known), entry, "unsupported_model_reference")
    moc = resources.dependency(refs.get("Moc"), suffixes=(".moc3",))
    _require(resources.prefix(moc, 4) == b"MOC3", moc)
    textures = refs.get("Textures")
    _require(isinstance(textures, list) and bool(textures), entry)
    for ref in textures:
        resources.dependency(ref, suffixes=(".png", ".jpg", ".jpeg", ".webp"))
    # Generic JSON names are admitted only for the exact resources referenced in
    # this model, and then checked using their declared role, not their filename.
    roles, audio_files = {}, set()
    tool_effects = False
    for key, suffix in (("Physics", ".physics3.json"), ("Pose", ".pose3.json"),
                        ("DisplayInfo", ".cdi3.json"), ("UserData", ".userdata3.json")):
        # Exporters may serialize an unused optional resource as null or "".
        # Nonempty optional references remain confined even if the file is absent.
        if key in refs and refs[key] is not None and refs[key] != "":
            target = resources.dependency(refs[key], suffixes=(".json",), optional=True)
            roles.setdefault(target, set()).add(suffix)
    physics_v2 = refs.get("PhysicsV2")
    if physics_v2 is not None and physics_v2 != "":
        # The bundled SDK does not consume this third-party extension. Preserve
        # it as data, while still checking any declared local physics resource.
        _require(isinstance(physics_v2, dict), entry)
        tool_effects = True
        if physics_v2.get("File") is not None and physics_v2.get("File") != "":
            target = resources.dependency(physics_v2["File"], suffixes=(".json",), optional=True)
            roles.setdefault(target, set()).add("PhysicsV2")
    expressions, motions = refs.get("Expressions", []), refs.get("Motions", {})
    _require(isinstance(expressions, list) and isinstance(motions, dict), entry)
    for ref in expressions:
        _require(isinstance(ref, dict) and isinstance(ref.get("Name"), str), entry)
        target = resources.dependency(ref.get("File"), suffixes=(".json",), optional=True)
        roles.setdefault(target, set()).add(".exp3.json")
    for group in motions.values():
        _require(isinstance(group, list), entry)
        for ref in group:
            _require(isinstance(ref, dict), entry)
            if ref.get("File") is None or ref.get("File") == "":
                # Some exporters store menus and commands alongside real motions.
                # NEKO does not execute those commands or synthesize a motion.
                tool_effects = True
            else:
                target = resources.dependency(ref["File"], suffixes=(".json",), optional=True)
                roles.setdefault(target, set()).add(".motion3.json")
            if ref.get("Sound") is not None and ref.get("Sound") != "":
                audio_files.add(resources.dependency(ref["Sound"], suffixes=(".mp3", ".wav", ".ogg"), optional=True))
    if tool_effects:
        resources.warn("live2d_unsupported_tool_effect", entry)
    mapping = data.get("EmotionMapping", {})
    _require(isinstance(mapping, dict), entry)
    for key, suffix in (("expressions", ".exp3.json"), ("motions", ".motion3.json")):
        groups = mapping.get(key, {})
        _require(isinstance(groups, dict), entry)
        for group in groups.values():
            _require(isinstance(group, list), entry)
            for ref in group:
                target = resources.dependency(ref, suffixes=(suffix,), manager=True, optional=True)
                roles.setdefault(target, set()).add(suffix)
    allowed = (".model3.json", ".moc3", ".png", ".jpg", ".jpeg", ".webp", ".motion3.json",
               ".exp3.json", ".physics3.json", ".pose3.json", ".cdi3.json", ".userdata3.json", ".vtube.json", ".xyplugin.json")
    _allowed_files(resources, allowed, filenames=("items_pinned_to_model.json", "parameters.json",
                                                "physics3.json", "pose3.json", "cdi3.json", "userdata3.json"),
                   paths=roles.keys() | audio_files)
    # Even assets absent from FileReferences are loaded by NEKO's recursive scan.
    for name in sorted(resources.files):
        # Suffix detection is case-insensitive; actual dependency lookup stays exact.
        lower = name.lower()
        kinds = roles.get(name, set()) | {suffix for suffix in (".motion3.json", ".exp3.json", ".physics3.json",
                                                               ".pose3.json", ".cdi3.json", ".userdata3.json")
                                        if ("." + posixpath.basename(lower)).endswith(suffix)}
        if kinds:
            contents = resources.json(name)
            for kind in sorted(kinds):
                if kind == ".motion3.json":
                    for code in _motion(contents, name):
                        resources.warn(code, name)
                elif kind == ".exp3.json":
                    _expression(contents, name)
                elif kind != "PhysicsV2" and not _auxiliary(contents, name, kind=kind, limits=resources.limits):
                    resources.warn("live2d_optional_resource_unavailable", name)
        if posixpath.basename(lower) == "parameters.json":
            # NEKO's parameter editor saves finite numeric overrides keyed by
            # model ID or legacy param_N. Keys never become paths or commands.
            parameters = resources.json(name)
            _require(all(_number(value) for value in parameters.values()), name)
        elif lower.endswith((".vtube.json", ".xyplugin.json")) or posixpath.basename(lower) == "items_pinned_to_model.json":
            # Preserve external tool settings verbatim as data. Reuse bounded JSON
            # checks without interpreting commands, paths or URLs as dependencies.
            resources.json(name)
        elif name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            _image(resources, name)
        elif lower.endswith((".mp3", ".wav", ".ogg")):
            header = resources.prefix(name, 12)
            if lower.endswith(".mp3"):
                valid = header.startswith(b"ID3") or (len(header) >= 4 and header[0] == 0xff
                         and header[1] & 0xe0 == 0xe0 and header[1] & 0x18 != 0x08 and header[1] & 0x06 != 0
                         and header[2] & 0xf0 != 0xf0 and header[2] & 0x0c != 0x0c)
            elif lower.endswith(".wav"):
                valid = header.startswith(b"RIFF") and header[8:12] == b"WAVE"
            else:
                valid = header.startswith(b"OggS\x00")
            _require(valid, name, "invalid_model_structure")


def validate_live2d_bundle(path, *, limits: BundleLimits = BundleLimits(),
                           format_limits: FormatLimits = FormatLimits()):
    """Admit a Live2D bundle using the container's open, stable source object."""
    with inspect_bundle(path, limits=limits) as (bundle, archive):
        _require(bundle.model_format == "live2d", bundle.entrypoint, "invalid_model_format")
        resources = _Resources(bundle, archive, format_limits)
        _live2d(resources)
    return resources.admitted()


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


def _vrm_spring_joint_work(indices, group_sizes: list, entry: str) -> int:
    _require(indices is None or isinstance(indices, list), entry)
    # Each joint visits every group and collider reference, including repeats.
    return 1 + sum(1 + group_sizes[_index(index, group_sizes, entry)] for index in indices or [])


def _validate_vrm_spring_bones(definition: dict, nodes: list, entry: str, work_limit: int) -> None:
    """Validate VRM 1.0 spring dependencies and bound work for each physics update."""
    colliders, groups, springs = (definition.get(key) for key in ("colliders", "colliderGroups", "springs"))
    _require(all(items is None or isinstance(items, list) for items in (colliders, groups, springs)), entry)
    loaded_colliders = set()
    for index, collider in enumerate(colliders or []):
        _require(isinstance(collider, dict), entry)
        node = collider.get("node")
        _require(node is None or type(node) is int, entry)
        # The plugin skips colliders attached to missing nodes.
        if node is not None and 0 <= node < len(nodes):
            shape = collider.get("shape")
            _require(isinstance(shape, dict) and any(isinstance(shape.get(kind), dict)
                                                     for kind in ("sphere", "capsule")), entry)
            loaded_colliders.add(index)
    group_sizes = []
    for group in groups or []:
        _require(isinstance(group, dict), entry)
        indices = group.get("colliders")
        # Missing colliders are skipped; numeric indices cannot alias array properties.
        _require(indices is None or (isinstance(indices, list) and all(type(index) is int for index in indices)), entry)
        group_sizes.append(sum(index in loaded_colliders for index in indices or []))
    work = 0
    for spring in springs or []:
        _require(isinstance(spring, dict), entry)
        indices, center, joints = (spring.get(key) for key in ("colliderGroups", "center", "joints"))
        joint_work = _vrm_spring_joint_work(indices, group_sizes, entry)
        _require(center is None or type(center) is int, entry)
        _require(isinstance(joints, list) and all(isinstance(joint, dict) for joint in joints), entry)
        # Each adjacent pair creates a spring joint. Its head must exist; an
        # absent final tail is supported by the plugin's default direction.
        for joint in joints[:-1]:
            _index(joint.get("node"), nodes, entry)
        if len(joints) > 1:
            tail = joints[-1].get("node")
            _require(tail is None or type(tail) is int, entry)
            work += (len(joints) - 1) * joint_work
            _require(work <= work_limit, entry, "model_physics_budget_exceeded")


def _validate_vrm_legacy_spring_bones(
    definition: dict, nodes: list, meshes: list, node_order: list, entry: str, work_limit: int,
) -> None:
    """Validate legacy spring dependencies and count the objects under each spring root."""
    bone_groups, collider_groups = definition["boneGroups"], definition.get("colliderGroups")
    _require(isinstance(bone_groups, list), entry)
    _require(collider_groups is None or isinstance(collider_groups, list), entry)
    collider_counts = Counter()
    group_sizes = []
    for group in collider_groups or []:
        _require(isinstance(group, dict), entry)
        colliders = group.get("colliders")
        _require(colliders is None or (isinstance(colliders, list)
                                       and all(isinstance(collider, dict) for collider in colliders)), entry)
        size = len(colliders) if colliders is not None else 0
        if size:
            collider_counts[_index(group.get("node"), nodes, entry)] += size
        group_sizes.append(size)

    subtree_sizes = [0] * len(nodes)
    for index in node_order:
        node = nodes[index]
        # Legacy traversal includes children the loader attaches to glTF nodes.
        size = 1 + collider_counts[index] + int("camera" in node)
        if "mesh" in node:
            count = len(meshes[node["mesh"]]["primitives"])
            size += count + int(count > 1)
        if "light" in (node.get("extensions", {}).get("KHR_lights_punctual") or {}):
            size += 2  # Directional and spot lights attach a target object.
        subtree_sizes[index] = size + sum(subtree_sizes[child] for child in node.get("children", []))

    work = 0
    for group in bone_groups:
        _require(isinstance(group, dict), entry)
        bones = group.get("bones")
        if bones in (None, False, ""):
            continue
        _require(isinstance(bones, list), entry)
        if bones:
            joint_work = _vrm_spring_joint_work(group.get("colliderGroups"), group_sizes, entry)
            # Overlapping roots are traversed independently by the plugin.
            work += joint_work * sum(subtree_sizes[_index(bone, nodes, entry)] for bone in bones)
            _require(work <= work_limit, entry, "model_physics_budget_exceeded")


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
        offset, chunk_count = 20 + json_size, 1
        while offset < size:
            _require(chunk_count < resources.limits.binary_records, entry, "model_binary_too_complex")
            chunk_header = handle.read(8)
            _require(len(chunk_header) == 8, entry)
            chunk_size, chunk_type = struct.unpack("<2I", chunk_header)
            offset += 8 + chunk_size
            _require(chunk_size % 4 == 0 and offset <= size and chunk_type != 0x4E4F534A, entry)
            if chunk_type == 0x004E4942:
                _require(chunk_count == 1, entry)
                binary_size = chunk_size
            chunk_count += 1
            # The bundle verifier has already read and hashed all chunk payloads.
            if offset < size:
                handle.seek(offset)
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
    buffers, views, images, nodes, textures = (data.get(key, []) for key in ("buffers", "bufferViews", "images", "nodes", "textures"))
    _require(all(isinstance(value, list) for value in (buffers, views, images, nodes, textures)) and bool(buffers), entry)
    extensions_used = data.get("extensionsUsed")
    spring_bones = legacy_spring_bones = None
    if extensions_used is None or "VRMC_springBone" in extensions_used:
        spring_bones = extensions.get("VRMC_springBone")
        _require(spring_bones is None or isinstance(spring_bones, dict), entry)
    if spring_bones is not None and spring_bones.get("specVersion") in ("1.0", "1.0-beta"):
        _validate_vrm_spring_bones(spring_bones, nodes, entry, resources.limits.vrm_spring_bone_work)
    elif kinds[0] == "VRM" and (extensions_used is None or "VRM" in extensions_used):
        secondary_animation = vrm.get("secondaryAnimation")
        if isinstance(secondary_animation, dict) and secondary_animation.get("boneGroups") not in (None, False, ""):
            legacy_spring_bones = secondary_animation
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
    allocated = 0
    for view in views:
        _require(isinstance(view, dict), entry)
        index = _index(view.get("buffer"), buffers, entry)
        offset, length = _uint(view.get("byteOffset", 0), entry), _uint(view.get("byteLength"), entry)
        _require(offset + length <= buffer_sizes[index], entry)
        # GLTFLoader caches by view index, then slices the full range for each
        # view. Overlapping ranges do not share their decoded backing storage.
        allocated += length
        _require(allocated <= resources.limits.vrm_accessor_bytes, entry, "model_accessor_budget_exceeded")
        if "byteStride" in view:
            _require(4 <= _uint(view["byteStride"], entry) <= 252 and view["byteStride"] % 4 == 0, entry)
    for image in images:
        _require(isinstance(image, dict) and ("uri" in image) != ("bufferView" in image), entry)
        if "uri" in image:
            _data_uri(image["uri"], entry, ("image/png", "image/jpeg", "image/webp", "image/avif"))
        else:
            _index(image["bufferView"], views, entry)
            _require(image.get("mimeType") in ("image/png", "image/jpeg", "image/webp", "image/avif"), entry)
    texture_sources = []
    for texture in textures:
        _require(isinstance(texture, dict), entry)
        if "source" in texture:
            _index(texture["source"], images, entry)
        # These supported extensions select a different image in the same table.
        for name in ("EXT_texture_webp", "EXT_texture_avif"):
            if name in texture.get("extensions", {}):
                extension = texture["extensions"][name]
                _require(isinstance(extension, dict), entry)
                _index(extension.get("source"), images, entry)
        texture_sources.append("source" in texture or any(name in texture.get("extensions", {})
                                                        for name in ("EXT_texture_webp", "EXT_texture_avif")))
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
    accessors, meshes, skins, scenes, animations, cameras = (data.get(key, []) for key in ("accessors", "meshes", "skins", "scenes", "animations", "cameras"))
    _require(all(isinstance(value, list) for value in (accessors, meshes, skins, scenes, animations, cameras)), entry)
    for camera in cameras:
        _require(isinstance(camera, dict) and camera.get("type") in ("perspective", "orthographic"), entry)
        _require(isinstance(camera.get(camera["type"]), dict), entry)
        _require(isinstance(camera.get("name"), str) or camera.get("name") in (None, False, ""), entry)
    light_extension = extensions.get("KHR_lights_punctual", {})
    _require(isinstance(light_extension, dict) and isinstance(light_extension.get("lights", []), list), entry)
    lights = light_extension.get("lights", [])
    component_sizes = {5120: 1, 5121: 1, 5122: 2, 5123: 2, 5125: 4, 5126: 4}
    accessor_clones = []
    for accessor in accessors:
        _require(isinstance(accessor, dict), entry)
        count = _uint(accessor.get("count"), entry)
        _require(count > 0, entry)
        component_type = _uint(accessor.get("componentType"), entry)
        component = component_sizes.get(component_type)
        shape = accessor.get("type")
        _require(component is not None and shape in ("SCALAR", "VEC2", "VEC3", "VEC4", "MAT2", "MAT3", "MAT4"), entry)
        if shape.startswith("MAT"):
            # glTF aligns every matrix column to four bytes for small components.
            columns = int(shape[-1])
            element_size = columns * ((columns * component + 3) // 4 * 4)
            item_bytes = columns * columns * component
        else:
            element_size = component * (1 if shape == "SCALAR" else int(shape[-1]))
            item_bytes = element_size
        _require(count * element_size <= resources.limits.vrm_accessor_bytes, entry, "model_accessor_budget_exceeded")
        view = None
        interleaved = False
        if "bufferView" in accessor:
            view = views[_index(accessor["bufferView"], views, entry)]
            offset = _uint(accessor.get("byteOffset", 0), entry)
            stride = view.get("byteStride", element_size)
            # The loader compares explicit stride with the unpadded item width.
            interleaved = view.get("byteStride", item_bytes) != item_bytes
            _require(offset % component == 0 and stride >= element_size, entry)
            # GLTFLoader.loadAccessor constructs count complete interleaved
            # strides, including padding after the last logical element.
            if interleaved:
                _require((offset // stride) * stride + count * stride <= view["byteLength"], entry)
                _require(offset % stride + element_size <= stride, entry)
            else:
                _require(offset + count * element_size <= view["byteLength"], entry)
        else:
            # glTF and the bundled loader allow pure zero-initialized accessors.
            _require(_uint(accessor.get("byteOffset", 0), entry) == 0, entry)
        # Geometry.clone() copies ordinary arrays independently. Interleaved
        # attributes share a clone of their backing view, including stride padding.
        clone_size = count * (view.get("byteStride", element_size) if view is not None else element_size)
        clone_source = (accessor["bufferView"], (offset // stride) * stride) if interleaved else None
        accessor_clones.append((clone_size, clone_source))
        if view is None or "sparse" in accessor:
            # Sparse data over an existing view clones private backing storage.
            # Sum every private array before checking supported sparse layouts.
            allocated += count * (view.get("byteStride", element_size) if view is not None else element_size)
            _require(allocated <= resources.limits.vrm_accessor_bytes, entry, "model_accessor_budget_exceeded")
        if "sparse" in accessor:
            # GLTFLoader copies a sparse interleaved array into a plain attribute
            # without deinterleaving it, changing its count and vertex layout.
            _require(not interleaved, entry, "unsupported_model_structure")
            # This bundled GLTFLoader writes sparse items through setX/Y/Z/W;
            # matrix widths above four components are explicitly unsupported.
            _require(shape not in ("MAT3", "MAT4"), entry, "unsupported_model_structure")
            sparse = accessor["sparse"]
            _require(isinstance(sparse, dict), entry)
            sparse_count = _uint(sparse.get("count"), entry)
            _require(0 < sparse_count <= count, entry)
            for key in ("indices", "values"):
                part = sparse.get(key)
                _require(isinstance(part, dict), entry)
                view = views[_index(part.get("bufferView"), views, entry)]
                width = {5121: 1, 5123: 2, 5125: 4}.get(_uint(part.get("componentType"), entry)) if key == "indices" else element_size
                _require(width is not None, entry)
                offset = _uint(part.get("byteOffset", 0), entry)
                _require(offset % (width if key == "indices" else component) == 0, entry)
                _require(offset + sparse_count * width <= view["byteLength"], entry)
    # Mesh dependencies are loaded regardless of animation. Validate once per
    # mesh, then reuse cardinalities instead of rescanning for each channel.
    materials = data.get("materials", [])
    _require(isinstance(materials, list), entry)
    for material in materials:
        _require(isinstance(material, dict), entry)
        blocks = [material, material.get("pbrMetallicRoughness", {})]
        blocks.extend(material.get("extensions", {}).values())
        for block in blocks:
            _require(isinstance(block, dict), entry)
            for key, value in block.items():
                if key.endswith("Texture"):
                    _require(isinstance(value, dict), entry)
                    _require(texture_sources[_index(value.get("index"), textures, entry)], entry)
    if kinds[0] == "VRM":
        properties = vrm.get("materialProperties", [])
        _require(isinstance(properties, list), entry)
        for material in properties:
            _require(isinstance(material, dict) and isinstance(material.get("textureProperties", {}), dict), entry)
            for value in material.get("textureProperties", {}).values():
                if value is not None:
                    _require(texture_sources[_index(value, textures, entry)], entry)
    mesh_morph_counts, mesh_draw_counts, mesh_weight_counts = [], [], []
    mesh_supports_instancing = []
    triangle_modes = (4, 5, 6)
    meshes_with_rendered_morphs, meshes_without_node_influences = set(), set()
    morph_allocated = 0
    for mesh_index, mesh in enumerate(meshes):
        _require(isinstance(mesh, dict) and isinstance(mesh.get("primitives"), list), entry)
        _require(isinstance(mesh.get("name"), str) or mesh.get("name") in (None, False, ""), entry)
        counts = []
        draw_count = 0
        supports_instancing = True
        for primitive in mesh["primitives"]:
            _require(isinstance(primitive, dict) and isinstance(primitive.get("attributes"), dict), entry)
            mode = primitive.get("mode", 4)
            _require(type(mode) in (int, float) and mode in range(7), entry)
            supports_instancing = supports_instancing and mode in triangle_modes
            for accessor in primitive["attributes"].values():
                _index(accessor, accessors, entry)
            if "indices" in primitive:
                _index(primitive["indices"], accessors, entry)
                primitive_count = accessors[primitive["indices"]]["count"]
            else:
                primitive_count = max((accessors[index]["count"] for index in primitive["attributes"].values()), default=0)
            # GLTFLoader converts strips/fans into a triangle index list before drawing.
            if mode in (5, 6):
                primitive_count = 3 * max(0, primitive_count - 2)
            draw_count += primitive_count
            if "material" in primitive:
                _index(primitive["material"], materials, entry)
            morphs = primitive.get("targets", [])
            if "targets" in primitive:
                _require(isinstance(morphs, list) and bool(morphs), entry)
                for morph in morphs:
                    _require(isinstance(morph, dict), entry)
                    for index in morph.values():
                        _index(index, accessors, entry)
            # WebGLMorphtargets allocates a separate RGBA float texture layer
            # per target, even for shared/fallback accessors. Row padding is less
            # than one row, hence at most twice the logical pixels for any GPU.
            slots = max((slot for semantic, slot in (("POSITION", 1), ("NORMAL", 2), ("COLOR_0", 3))
                         if any(semantic in morph for morph in morphs)), default=0)
            if slots:
                meshes_with_rendered_morphs.add(mesh_index)
                _require(any("POSITION" in morph for morph in morphs), entry)
                _require("POSITION" in primitive["attributes"], entry)
                position = accessors[primitive["attributes"]["POSITION"]]
                for semantic in ("NORMAL", "COLOR_0"):
                    if any(semantic in morph for morph in morphs) and not all(semantic in morph for morph in morphs):
                        fallback = primitive["attributes"].get(semantic)
                        # glTF requires equal vertex counts within a primitive.
                        _require(fallback is not None and accessors[fallback]["count"] == position["count"], entry)
                for morph in morphs:
                    for semantic in ("POSITION", "NORMAL", "COLOR_0"):
                        if semantic in morph:
                            _require(accessors[morph[semantic]]["count"] == position["count"], entry)
                morph_allocated += position["count"] * slots * 32 * len(morphs)
                _require(morph_allocated <= resources.limits.vrm_morph_bytes, entry, "model_accessor_budget_exceeded")
            elif morphs and mode in triangle_modes:
                # createNodeMesh writes node weights on every triangle mesh.
                meshes_without_node_influences.add(mesh_index)
            if mode in (5, 6):
                # toTrianglesDrawMode clones each primitive's geometry even when
                # its source accessors are shared, then allocates triangle indices.
                clone_indices = list(primitive["attributes"].values())
                for semantic in ("POSITION", "NORMAL", "COLOR_0"):
                    if any(semantic in morph for morph in morphs):
                        clone_indices.extend(morph.get(semantic, primitive["attributes"].get(semantic)) for morph in morphs)
                cloned_views = {}
                for index in clone_indices:
                    if index is None:
                        continue
                    size, clone_source = accessor_clones[index]
                    if clone_source is None:
                        allocated += size
                    else:
                        view_index, start = clone_source
                        # InterleavedBuffer.clone caches the first copy per underlying
                        # buffer, including its slice bounds. Different slices would alias.
                        _require(cloned_views.setdefault(view_index, (start, size)) == (start, size),
                                 entry, "unsupported_model_structure")
                allocated += sum(size for _, size in cloned_views.values()) + primitive_count * 4
                # The original index is also cloned before being replaced. For
                # unindexed geometry the helper first generates a sequential index.
                allocated += accessor_clones[primitive["indices"]][0] if "indices" in primitive else max(
                    (accessors[index]["count"] for index in primitive["attributes"].values()), default=0) * 4
                _require(allocated <= resources.limits.vrm_accessor_bytes, entry, "model_accessor_budget_exceeded")
            counts.append(len(morphs))
        _require(len(set(counts)) <= 1, entry)
        target_count = counts[0] if counts else 0
        if "weights" in mesh:
            _require_morph_weights(mesh["weights"], target_count, entry)
        mesh_morph_counts.append(target_count)
        mesh_draw_counts.append(draw_count)
        mesh_weight_counts.append(sum(counts))
        mesh_supports_instancing.append(supports_instancing)
    weight_animation_nodes = set()
    for animation in animations:
        _require(isinstance(animation, dict), entry)
        samplers, channels = animation.get("samplers"), animation.get("channels")
        _require(isinstance(samplers, list) and bool(samplers) and isinstance(channels, list) and bool(channels), entry)
        parameters = animation.get("parameters")
        _require("parameters" not in animation or isinstance(parameters, dict), entry)
        sampler_accessors = []
        for sampler in samplers:
            _require(isinstance(sampler, dict), entry)
            input_ref, output_ref = sampler.get("input"), sampler.get("output")
            if parameters is not None:
                # Legacy numeric keys must round-trip through JavaScript Number
                # without rounding or changing to exponential notation.
                _require(all(isinstance(ref, str) or (type(ref) is int and 0 <= ref < 2**53)
                             for ref in (input_ref, output_ref)), entry)
                input_ref, output_ref = parameters.get(str(input_ref)), parameters.get(str(output_ref))
            input_index = _index(input_ref, accessors, entry)
            output_index = _index(output_ref, accessors, entry)
            source = accessors[input_index]
            _require(source["type"] == "SCALAR" and source["componentType"] == 5126, entry)
            _require(sampler.get("interpolation", "LINEAR") in ("LINEAR", "STEP", "CUBICSPLINE"), entry)
            sampler_accessors.append((input_index, output_index))
        for channel in channels:
            _require(isinstance(channel, dict), entry)
            _index(channel.get("sampler"), samplers, entry)
            target = channel.get("target")
            _require(isinstance(target, dict) and target.get("path") in ("translation", "rotation", "scale", "weights"), entry)
            if "node" in target:
                _index(target["node"], nodes, entry)
            sample = samplers[channel["sampler"]]
            input_index, output_index = sampler_accessors[channel["sampler"]]
            sample_input, output = accessors[input_index], accessors[output_index]
            path = target["path"]
            shape = {"translation": "VEC3", "scale": "VEC3", "rotation": "VEC4", "weights": "SCALAR"}[path]
            _require(output["type"] == shape, entry)
            # glTF permits normalized integer rotation/weight outputs; forcing
            # every output to float would reject compatible existing models.
            floating = output["componentType"] == 5126 and output.get("normalized", False) is False
            normalized = (path in ("rotation", "weights") and output["componentType"] in (5120, 5121, 5122, 5123)
                          and output.get("normalized") is True)
            _require(floating or normalized, entry)
            if "node" in target:
                # GLTFLoader passes the backing arrays directly to keyframe tracks.
                _require(all(accessor_clones[index][1] is None for index in (input_index, output_index)),
                         entry, "unsupported_model_structure")
                if normalized:
                    allocated += output["count"] * (1 if shape == "SCALAR" else int(shape[-1])) * 4
                    _require(allocated <= resources.limits.vrm_accessor_bytes, entry, "model_accessor_budget_exceeded")
            cubic = sample.get("interpolation", "LINEAR") == "CUBICSPLINE"
            _require(not cubic or sample_input["count"] >= 2, entry)
            expected = sample_input["count"] * (3 if cubic else 1)
            if path == "weights":
                if "node" not in target:
                    # The bundled loader ignores node-less channels; target
                    # cardinality is unknown, but every keyframe must be whole.
                    _require(output["count"] % expected == 0, entry)
                    continue
                node = nodes[target["node"]]
                _require(isinstance(node, dict), entry)
                target_count = mesh_morph_counts[_index(node.get("mesh"), meshes, entry)]
                _require(target_count > 0, entry)
                expected *= target_count
                weight_animation_nodes.add(target["node"])
            _require(output["count"] == expected, entry)
    instanced_vertices = 0
    skinned_meshes = set()
    mesh_references: dict[int, int] = {}
    node_parents: dict[int, int] = {}
    node_constraints: dict[int, tuple[str, int]] = {}
    checks_node_constraints = extensions_used is None or "VRMC_node_constraint" in extensions_used
    for node_index, node in enumerate(nodes):
        _require(isinstance(node, dict) and isinstance(node.get("children", []), list), entry)
        _require(isinstance(node.get("name"), str) or node.get("name") in (None, False, ""), entry)
        for child in node.get("children", []):
            _index(child, nodes, entry)
            # Shared children are reparented by GLTFLoader and invalid in glTF.
            _require(child not in node_parents, entry)
            node_parents[child] = node_index
        for field, table in (("mesh", meshes), ("skin", skins), ("camera", cameras)):
            if field in node:
                _index(node[field], table, entry)
        if "weights" in node:
            _require("mesh" in node, entry)
            _require_morph_weights(node["weights"], mesh_morph_counts[node["mesh"]], entry)
            _require(not node["weights"] or node["mesh"] not in meshes_without_node_influences,
                     entry, "unsupported_model_structure")
        node_extensions = node.get("extensions", {})
        constraint_extension = node_extensions.get("VRMC_node_constraint")
        if checks_node_constraints and constraint_extension is not None:
            _require(isinstance(constraint_extension, dict), entry)
            if constraint_extension.get("specVersion") in ("1.0", "1.0-beta"):
                constraint = constraint_extension.get("constraint")
                _require(isinstance(constraint, dict), entry)
                # The bundled VRM plugin imports only the first non-null type.
                for kind in ("roll", "aim", "rotation"):
                    definition = constraint.get(kind)
                    if definition is not None:
                        _require(isinstance(definition, dict), entry)
                        source = _index(definition.get("source"), nodes, entry)
                        node_constraints[node_index] = (kind, source)
                        break
        light = node_extensions.get("KHR_lights_punctual")
        if light is not None:
            _require(isinstance(light, dict), entry)
            if "light" in light:
                definition = lights[_index(light["light"], lights, entry)]
                _require(isinstance(definition, dict) and definition.get("type") in ("directional", "point", "spot"), entry)
                _require(isinstance(definition.get("name"), str) or definition.get("name") in (None, False, ""), entry)
                if "color" in definition:
                    color = definition["color"]
                    _require(isinstance(color, list) and len(color) == 3 and all(_number(v) for v in color), entry)
                if definition["type"] == "spot":
                    _require(isinstance(definition.get("spot", {}), dict), entry)
        instances = 1
        instancing = node_extensions.get("EXT_mesh_gpu_instancing")
        if instancing is not None and "mesh" in node:
            mesh_index = node["mesh"]
            _require(isinstance(instancing, dict) and isinstance(instancing.get("attributes"), dict), entry)
            instance_counts = set()
            for name, index in instancing["attributes"].items():
                accessor = accessors[_index(index, accessors, entry)]
                shape = {"TRANSLATION": "VEC3", "ROTATION": "VEC4", "SCALE": "VEC3"}.get(name)
                _require(shape is None or accessor["type"] == shape, entry)
                instance_counts.add(accessor["count"])
            _require(len(instance_counts) <= 1, entry)
            # A single line or point primitive makes the loader use ordinary meshes.
            if instance_counts and mesh_supports_instancing[mesh_index]:
                # InstancedMesh has no morph influences or morph texture here.
                _require(mesh_index not in meshes_with_rendered_morphs,
                         entry, "unsupported_model_structure")
                instances = next(iter(instance_counts))
                # GLTFMeshGpuInstancing creates count*16 Float32 values per
                # node and primitive, even when all nodes share one accessor.
                allocated += instances * 64 * len(meshes[mesh_index]["primitives"])
                _require(allocated <= resources.limits.vrm_accessor_bytes, entry, "model_accessor_budget_exceeded")
        if "mesh" in node:
            mesh_index = node["mesh"]
            mesh_references[mesh_index] = mesh_references.get(mesh_index, 0) + 1
            if "skin" in node:
                skinned_meshes.add(mesh_index)
            instanced_vertices += instances * mesh_draw_counts[mesh_index]
            _require(instanced_vertices <= resources.limits.vrm_instanced_vertices, entry, "model_accessor_budget_exceeded")
    # Shared meshes keep their weights and clone them for every node reference.
    morph_weights = sum(mesh_weight_counts[index] * (1 if refs == 1 else 1 + refs)
                        for index, refs in mesh_references.items())
    _require(morph_weights <= resources.limits.vrm_morph_weights, entry, "model_accessor_budget_exceeded")
    # GLTFLoader constructs a SkinnedMesh only for triangle primitives.
    skin_weight_work = 0
    for mesh_index in skinned_meshes:
        for primitive in meshes[mesh_index]["primitives"]:
            if primitive.get("mode", 4) not in triangle_modes:
                continue
            attrs = primitive["attributes"]
            _require("JOINTS_0" in attrs and "WEIGHTS_0" in attrs, entry)
            joints, weights = accessors[attrs["JOINTS_0"]], accessors[attrs["WEIGHTS_0"]]
            _require(joints["type"] == weights["type"] == "VEC4" and joints["count"] == weights["count"], entry)
            if "POSITION" in attrs:
                _require(joints["count"] == accessors[attrs["POSITION"]]["count"], entry)
            # Construction normalizes every primitive even when geometry is shared.
            skin_weight_work += weights["count"]
            _require(skin_weight_work <= resources.limits.vrm_skin_weight_work,
                     entry, "model_accessor_budget_exceeded")
    # A cyclic child graph cannot become a scene tree in the current loader.
    colors, heights = [0] * len(nodes), [1] * len(nodes)
    legacy_node_order = []
    morph_subtrees = set()
    for start in range(len(nodes)):
        stack = [(start, False)]
        while stack:
            node, leaving = stack.pop()
            if leaving:
                children = nodes[node].get("children", [])
                heights[node] = 1 + max((heights[child] for child in children), default=0)
                _require(heights[node] <= resources.limits.hierarchy_depth, entry, "model_accessor_budget_exceeded")
                if legacy_spring_bones is not None:
                    legacy_node_order.append(node)
                if weight_animation_nodes:
                    # The bundled loader binds a weights track to every mesh
                    # with influences in this subtree, including child nodes.
                    descendant_morph = any(child in morph_subtrees for child in children)
                    _require(not descendant_morph or node not in weight_animation_nodes,
                             entry, "unsupported_model_structure")
                    if descendant_morph or nodes[node].get("mesh") in meshes_with_rendered_morphs:
                        morph_subtrees.add(node)
                colors[node] = 2
            elif colors[node] != 2:
                _require(colors[node] == 0, entry)
                colors[node] = 1
                stack.append((node, True))
                stack.extend((child, False) for child in nodes[node].get("children", []))
    if legacy_spring_bones is not None:
        _validate_vrm_legacy_spring_bones(
            legacy_spring_bones, nodes, meshes, legacy_node_order, entry, resources.limits.vrm_spring_bone_work,
        )
    if node_constraints:
        # The plugin visits constraints on each dependency and its ancestors.
        # Separate the inherited node transform from its local constraint so
        # ancestor walks are shared without building all transitive edges.
        graph = TopologicalSorter()
        for child, parent in node_parents.items():
            graph.add(child, parent)
        for destination, (kind, source) in node_constraints.items():
            local_constraint = ("constraint", destination)
            graph.add(destination, local_constraint)
            graph.add(local_constraint, source)
            if kind == "aim" and destination in node_parents:
                graph.add(local_constraint, node_parents[destination])
        try:
            graph.prepare()
        except CycleError as exc:
            raise ModelFormatError("invalid_model_structure", entry) from exc
    for skin in skins:
        _require(isinstance(skin, dict) and isinstance(skin.get("joints"), list), entry)
        for joint in skin["joints"]:
            _index(joint, nodes, entry)
        if "inverseBindMatrices" in skin:
            matrix_index = _index(skin["inverseBindMatrices"], accessors, entry)
            matrices = accessors[matrix_index]
            _require(matrices["type"] == "MAT4" and matrices["componentType"] == 5126
                     and matrices["count"] >= len(skin["joints"]), entry)
            # loadSkin passes the backing array straight to Matrix4.fromArray.
            _require(accessor_clones[matrix_index][1] is None, entry, "unsupported_model_structure")
    for scene in scenes:
        _require(isinstance(scene, dict) and isinstance(scene.get("nodes", []), list), entry)
        _require(isinstance(scene.get("name"), str) or scene.get("name") in (None, False, ""), entry)
        for node in scene.get("nodes", []):
            _require(_index(node, nodes, entry) not in node_parents, entry)
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


def _mmd_texture(resources: _Resources, value: str, tga_references: dict[str, str], *, toon: bool = False) -> None:
    """Use the main file's parent, retaining legal internal parent references."""
    target = resources.dependency(value, base=posixpath.dirname(resources.bundle.entrypoint), suffixes=_MMD_IMAGES)
    if value.lower().endswith(".tga"):
        # Only toon rotation requires a CanvasImageSource. TGALoader returns raw pixels.
        _require(not toon, target, "invalid_model_image")
        # The loader caches the raw URL, even if several spellings resolve to this file.
        tga_references[value] = target


def _mmd_ik_budget(resources: _Resources, parents: list[int], weights: dict[int, int], *, strict_parents=False) -> None:
    """Count recursive matrix updates without recursively walking the bone graph."""
    sizes, children, heights = [1] * len(parents), [0] * len(parents), [1] * len(parents)
    for index, parent in enumerate(parents):
        # PMX Object3D ignores self-add; PMD's ancestor conversion cannot.
        if 0 <= parent < len(parents) and (strict_parents or parent != index):
            children[parent] += 1
    leaves = [index for index, count in enumerate(children) if count == 0]
    visited = 0
    while leaves:
        index = leaves.pop()
        visited += 1
        parent = parents[index]
        if 0 <= parent < len(parents) and (strict_parents or parent != index):
            sizes[parent] += sizes[index]
            heights[parent] = max(heights[parent], heights[index] + 1)
            children[parent] -= 1
            if children[parent] == 0:
                leaves.append(parent)
    _require(visited == len(parents), resources.bundle.entrypoint)
    _require(max(heights, default=0) <= resources.limits.hierarchy_depth,
             resources.bundle.entrypoint, "model_accessor_budget_exceeded")
    _require(sum(weight * sizes[index] for index, weight in weights.items()) <= resources.limits.mmd_ik_work,
             resources.bundle.entrypoint, "model_accessor_budget_exceeded")


def _mmd_grant_depth(resources: _Resources, grants: dict[int, int]) -> None:
    grant_children = {index: [] for index in grants}
    for index, parent in grants.items():
        if parent in grants:
            grant_children[parent].append(index)
    # buildGrants only walks roots; cyclic components and their children are ignored.
    pending = [(index, 1) for index, parent in grants.items() if parent not in grants]
    while pending:
        index, depth = pending.pop()
        _require(depth <= resources.limits.hierarchy_depth, resources.bundle.entrypoint,
                 "model_accessor_budget_exceeded")
        pending.extend((child, depth + 1) for child in grant_children[index])


def _pmx(resources: _Resources, reader: _MmdReader, tga_references: dict[str, str]) -> None:
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

    def dependency(index: int, *, active: bool = True, toon: bool = False) -> None:
        _require(-1 <= index < len(textures), path)
        if active and index != -1:
            _mmd_texture(resources, textures[index], tga_references, toon=toon)

    covered = 0
    material_count = reader.count(minimum=79)
    for _ in range(material_count):
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
            dependency(reader.index(texture), toon=True)
        reader.text()
        count = reader.scalar("i")
        _require(0 <= count <= indices and count % 3 == 0, path)
        covered += count
    _require(covered == indices, path)
    ik_work = 0
    parents, ik_weights, grants = [], {}, {}
    bones = reader.count(minimum=26 + bone)
    for _ in range(bones):
        reader.text()
        reader.text()
        reader.skip(12)
        parents.append(reader.index(bone))
        reader.skip(4)
        flags = reader.scalar("H")
        reader.skip(bone if flags & 1 else 12)
        if flags & (256 | 512):
            append = reader.index(bone)
            # GrantSolver only reads the parent for non-local rotation grants.
            if flags & 256 and not flags & 128:
                _require(0 <= append < bones, path)
            grants[len(parents) - 1] = append
            reader.skip(4)
        if flags & 1024:
            reader.skip(12)
        if flags & 2048:
            reader.skip(24)
        if flags & 8192:
            reader.skip(4)
        if flags & 32:
            effector = reader.index(bone)
            _require(0 <= effector < bones, path)
            iterations = reader.scalar("i")
            reader.skip(4)
            links = reader.count(minimum=bone + 1)
            ik_work += iterations * links
            _require(0 <= iterations <= resources.limits.mmd_ik_iterations
                     and ik_work <= resources.limits.mmd_ik_work, path, "model_accessor_budget_exceeded")
            for _ in range(links):
                index = reader.index(bone)
                _require(0 <= index < bones, path)
                ik_weights[index] = ik_weights.get(index, 0) + iterations
                limited = reader.scalar("B")
                _require(limited in (0, 1), path)
                if limited:
                    reader.skip(24)
    _mmd_ik_budget(resources, parents, ik_weights)
    _mmd_grant_depth(resources, grants)
    # Each entry contains its typed index followed by the loader's fixed payload.
    morph_sizes = {0: morph + 4, 1: vertex + 12, 2: bone + 28,
                   3: vertex + 16, 4: vertex + 16, 5: vertex + 16,
                   6: vertex + 16, 7: vertex + 16, 8: material + 113,
                   9: morph + 4, 10: rigid + 25}
    dense_morphs = 0
    morph_work_counts, group_targets = [], []
    for _ in range(reader.count(minimum=14)):
        reader.text()
        reader.text()
        reader.skip(1)
        kind = reader.scalar("B")
        _require(kind in morph_sizes, path)
        if kind in (0, 1):
            # buildGeometry copies every position for each vertex/group morph,
            # including empty morphs. Sparse offset counts do not bound this cost.
            dense_morphs += 1
            _require(vertices * dense_morphs * 12 <= resources.limits.mmd_morph_bytes,
                     path, "model_accessor_budget_exceeded")
        width = morph_sizes[kind]
        count = reader.count(minimum=width)
        morph_work_counts.append(count if kind in (1, 8) else 0)
        if kind == 0:
            targets = []
            for _ in range(count):
                targets.append(reader.index(morph))
                reader.skip(4)
            group_targets.append(targets)
        elif kind == 8:
            for _ in range(count):
                _require(-1 <= reader.index(material) < material_count, path)
                reader.skip(113)
        else:
            reader.skip(count * width)
    # Geometry and transparency fixes expand direct vertex/material references,
    # even repeated/zero-ratio references. Group-to-group references are ignored.
    morph_work = sum(morph_work_counts)
    _require(morph_work <= resources.limits.mmd_morph_work, path, "model_accessor_budget_exceeded")
    for targets in group_targets:
        for index in targets:
            _require(0 <= index < len(morph_work_counts), path)
            morph_work += morph_work_counts[index]
            _require(morph_work <= resources.limits.mmd_morph_work, path, "model_accessor_budget_exceeded")
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
        rigid_bone = reader.index(bone)
        _require(rigid_bone == -1 or 0 <= rigid_bone < bones, path)
        reader.skip(61)
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


def _pmd(resources: _Resources, reader: _MmdReader, tga_references: dict[str, str]) -> None:
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
    parents, ik_weights, ik_owners, grants = [], {}, {}, {}
    ik_bones = set()
    for index in range(bones):
        reader.skip(20)
        parents.append(reader.scalar("h"))
        tail, kind = reader.scalar("h"), reader.scalar("B")
        if kind == 2:
            ik_bones.add(index)
        if kind == 5:  # PmdReader turns RotateEffect into a non-local rotation grant.
            grants[index] = tail
        reader.skip(14)
    ik_work = 0
    max_ik_target = -1
    ik_count = reader.count("H", 11)
    for _ in range(ik_count):
        owner = reader.scalar("H")
        target = reader.scalar("H")
        if owner < bones:
            max_ik_target = max(max_ik_target, target)
            ik_owners[owner] = ik_owners.get(owner, 0) + 1
        links = reader.scalar("B")
        iterations = reader.scalar("H")
        ik_work += iterations * links
        _require(iterations <= resources.limits.mmd_ik_iterations
                 and ik_work <= resources.limits.mmd_ik_work, path, "model_accessor_budget_exceeded")
        reader.skip(4)
        for _ in range(links):
            index = reader.scalar("H")
            # PmdReader discards missing links and IKs with no owner bone.
            if owner < bones and index < bones:
                ik_weights[index] = ik_weights.get(index, 0) + iterations
    # _ConvertBones scans the table twice for each IK-enabled bone.
    _require(2 * ik_count * len(ik_bones.union(ik_owners)) <= resources.limits.pmd_ik_scan_work,
             path, "model_accessor_budget_exceeded")
    # PmdReader appends duplicate IK children in original bone order. Its later
    # reordering remaps both parents and links by identity, preserving subtree
    # sizes and weights; calculate in that identity space before the permutation.
    owner_order = sorted(ik_owners)
    for owner in owner_order:
        count = ik_owners[owner]
        for _ in range(count - 1):
            if owner in grants:
                grants[len(parents)] = grants[owner]
            parents.append(owner)
    _require(max_ik_target < len(parents), path)
    _require(all(0 <= parent < len(parents) for parent in grants.values()), path)
    _mmd_ik_budget(resources, parents, ik_weights, strict_parents=True)
    _mmd_grant_depth(resources, grants)
    morphs = reader.count("H", 25)
    # PmdReader removes its base morph, then converts the rest to vertex morphs.
    _require(vertices * max(0, morphs - 1) * 12 <= resources.limits.mmd_morph_bytes,
             path, "model_accessor_budget_exceeded")
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
            # Only -1 means unbound. Position normalization needs bone zero
            # for that sentinel, unless the IK reorder remaps it away first.
            reordered = list(ik_owners) != owner_order
            for _ in range(reader.count("I", 83)):
                reader.skip(20)
                rigid_bone = reader.scalar("h")
                _require(0 <= rigid_bone < len(parents)
                         or (rigid_bone == -1 and not reordered and bool(parents)), path)
                reader.skip(61)
            reader.skip(reader.count("I", 124) * 124)
    # static/libs/three-mmd.module.js PmdReader.ParseAsync/_ConvertMaterials leaves a
    # missing toon table empty; it supplies no implicit shared-toon fallback.
    # Resolve toon indices before appending diffuse/sphere paths, as it does.
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
                    # PmdReader uses Map.size, even when toon slots collapse to
                    # duplicate keys. Check the slot the loader will really read.
                    table[key] = len(table)
                    textures.append(value)
                active.append(table[key])
    for index, (toon, _diffuse, _sphere) in enumerate(materials):
        if toon != -1 and not shared[index]:
            _require(toon < len(textures), path)
            _mmd_texture(resources, textures[toon], tga_references, toon=True)
    for index in set(active):
        # Check the actual converted table entry, not the original case-folded key.
        _mmd_texture(resources, textures[index], tga_references)


def _mmd(resources: _Resources) -> None:
    """Validate binary framing plus all texture paths consumed by the loader."""
    _allowed_files(resources, (".pmx", ".pmd") + _MMD_IMAGES)
    entry = resources.bundle.entrypoint
    tga_references: dict[str, str] = {}
    with resources.archive.open("model/" + entry) as handle:
        reader = _MmdReader(handle, resources.files[entry].byte_size, entry, resources.limits)
        (_pmx if entry.endswith(".pmx") else _pmd)(resources, reader, tga_references)
        _require(reader.offset == reader.size, entry, "unsupported_model_structure")
    reference_counts = Counter(tga_references.values())
    tga_layouts = {}
    tga_allocated = 0
    for name in resources.files:
        if name.lower().endswith(_MMD_IMAGES):
            layout = _image(resources, name)
            if layout is not None and name in reference_counts:
                tga_allocated += layout.decoded_bytes * reference_counts[name]
                _require(tga_allocated <= resources.limits.mmd_tga_bytes, name, "model_accessor_budget_exceeded")
                tga_layouts[name] = layout
    # Reject over-budget packages before scanning any active texture packets.
    for name, layout in tga_layouts.items():
        _validate_tga_pixels(resources, name, layout)


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
        resources = _Resources(bundle, archive, format_limits)
        {"live2d": _live2d, "vrm": _vrm, "mmd": _mmd}[bundle.model_format](resources)
    return resources.admitted()
