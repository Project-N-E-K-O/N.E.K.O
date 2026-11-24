# -*- coding: utf-8 -*-
"""Inspect self-contained Spine ZIP packages without interpreting vendor metadata."""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
import unicodedata
import zipfile
import zlib
from pathlib import PurePosixPath
from typing import Any

EMOTE_TEMPLATE_SCHEMA_VERSION = 2
MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5000
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MODEL_BYTES = 64 * 1024 * 1024
MAX_METADATA_FILE_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 128 * 1024 * 1024
MAX_PATH_DEPTH = 16
SPINE_VERSION_FAMILY = "4.3"
NATIVE_MANIFEST = "neko-template.json"
_VERSION = re.compile(rb"(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])")


class EmoteTemplatePackageError(ValueError):
    def __init__(self, message: str, *, code="invalid_package", candidates=None, role=None):
        super().__init__(message)
        self.code = code
        self.candidates = candidates or []
        self.role = role


def _normalize_archive_name(name: str) -> str:
    raw = unicodedata.normalize("NFC", str(name).replace("\\", "/"))
    if raw.startswith("/") or re.match(r"^[A-Za-z]:", raw) or any(ord(c) < 32 for c in raw):
        raise EmoteTemplatePackageError("ZIP 包含不安全路径")
    parts = [part for part in raw.split("/") if part not in ("", ".")]
    if ".." in parts or len(parts) > MAX_PATH_DEPTH or any(":" in p for p in parts):
        raise EmoteTemplatePackageError("ZIP 路径越界或层级超过上限")
    return "/".join(parts)


def _decode_zip_name(info):
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return info.filename


def _collect_entries(archive):
    infos = archive.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise EmoteTemplatePackageError("ZIP 文件数超过上限（5000 个）")
    entries, seen = {}, set()
    total = 0
    for info in infos:
        path = _normalize_archive_name(_decode_zip_name(info))
        if not path:
            continue
        if stat.S_ISLNK((info.external_attr >> 16) & 0xffff):
            raise EmoteTemplatePackageError("ZIP 不允许符号链接")
        if path.casefold() in seen:
            raise EmoteTemplatePackageError(f"ZIP 包含重复或大小写冲突路径：{path}")
        seen.add(path.casefold())
        if info.flag_bits & 1:
            raise EmoteTemplatePackageError("不支持加密 ZIP")
        if info.is_dir():
            continue
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES or info.file_size > MAX_MODEL_BYTES:
            raise EmoteTemplatePackageError("ZIP 资源大小超过上限")
        if info.file_size > max(1, info.compress_size) * 1000:
            raise EmoteTemplatePackageError("ZIP 压缩率超过安全上限")
        entries[path] = info
    return entries


def _read(archive, info, limit):
    if info.file_size > limit:
        raise EmoteTemplatePackageError(f"资源过大：{info.filename}")
    try:
        with archive.open(info) as stream:
            content = stream.read(limit + 1)
    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise EmoteTemplatePackageError(f"无法读取资源：{info.filename}") from exc
    if len(content) > limit:
        raise EmoteTemplatePackageError(f"资源过大：{info.filename}")
    return content


def _stem(path):
    return re.sub(r"\.(skel\.bytes|atlas\.txt|skel|atlas|json)$", "", PurePosixPath(path).name, flags=re.I)


def _choose(candidates, role, selected):
    if selected:
        matches = [item for item in candidates if item["path"] == selected]
        if len(matches) != 1:
            raise EmoteTemplatePackageError("所选资源不在候选列表中")
        return matches[0]
    if not candidates:
        raise EmoteTemplatePackageError(f"ZIP 缺少可识别的 {role} 资源")
    if len(candidates) > 1:
        raise EmoteTemplatePackageError(
            "ZIP 中存在多组资源，请选择要导入的模型或图集",
            code="selection_required", role=role,
            candidates=[{"path": item["path"]} for item in candidates],
        )
    return candidates[0]


def _atlas_pages(text):
    lines = text.lstrip("\ufeff").splitlines()
    pages, regions = [], set()
    for index, line in enumerate(lines):
        value = line.strip()
        if not value or ":" in value:
            continue
        follow = lines[index + 1].strip() if index + 1 < len(lines) else ""
        page_start = index == 0 or not lines[index - 1].strip()
        if page_start:
            if PurePosixPath(value).suffix.lower() != ".png":
                raise EmoteTemplatePackageError("当前 Spine 贴图必须使用 PNG")
            pages.append(value)
        elif re.match(r"^(bounds|offsets|rotate|xy|size|index)\s*:", follow, re.I):
            regions.add(value)
    if not pages:
        raise EmoteTemplatePackageError("图集没有有效的贴图页")
    return pages, regions


def _resolve_image(entries, atlas_path, reference):
    reference = _normalize_archive_name(reference)
    expected = str(PurePosixPath(atlas_path).parent / reference)
    matches = [path for path in entries if path.casefold() == expected.casefold()]
    if not matches:
        matches = [path for path in entries if path.casefold().endswith("/" + reference.casefold()) or path.casefold() == reference.casefold()]
    if len(matches) != 1:
        reason = "缺少" if not matches else "存在同名冲突"
        raise EmoteTemplatePackageError(f"图集贴图{reason}：{reference}")
    return matches[0]


def _json_attachments(data):
    skins = data.get("skins") or []
    if not isinstance(skins, (dict, list)):
        raise EmoteTemplatePackageError("骨架 skins 字段必须是数组或对象")
    skins = skins.values() if isinstance(skins, dict) else skins
    for skin in skins:
        attachments = skin.get("attachments", skin) if isinstance(skin, dict) else {}
        if not isinstance(attachments, dict):
            continue
        for slot in attachments.values():
            if not isinstance(slot, dict):
                continue
            for name, attachment in slot.items():
                if isinstance(attachment, dict) and not attachment.get("sequence") and attachment.get("type", "region") in ("region", "mesh"):
                    yield str(attachment.get("path") or attachment.get("name") or name)


def _parse_native_parts(content):
    try:
        manifest = json.loads(content.decode("utf-8-sig"))
    except (ValueError, UnicodeError) as exc:
        raise EmoteTemplatePackageError("neko-template.json 不是有效的 JSON") from exc
    if (not isinstance(manifest, dict) or manifest.get("format") != "neko.emote-template"
            or type(manifest.get("formatVersion")) is not int or manifest["formatVersion"] != 1):
        raise EmoteTemplatePackageError("不支持的 N.E.K.O. 模板清单格式")
    raw_parts = manifest.get("parts")
    if not isinstance(raw_parts, list) or not 1 <= len(raw_parts) <= 128:
        raise EmoteTemplatePackageError("模板清单应声明 1 至 128 个部件")
    parts, seen = [], set()
    for item in raw_parts:
        if not isinstance(item, dict):
            raise EmoteTemplatePackageError("模板部件必须是对象")
        name, kind = item.get("slot"), item.get("kind")
        if not isinstance(name, str) or not name.strip() or name in seen or kind not in ("toggle", "choice"):
            raise EmoteTemplatePackageError("模板部件的插槽名重复、为空或类型无效")
        seen.add(name)
        labels = item.get("labels", {})
        if not isinstance(labels, dict) or not all(isinstance(value, str) and value.strip() for value in labels.values()):
            raise EmoteTemplatePackageError(f"部件标签无效：{name}")
        options = item.get("attachments")
        if (not isinstance(options, list) or not 1 <= len(options) <= 32
                or not all(isinstance(value, str) and value for value in options) or len(set(options)) != len(options)
                or (kind == "toggle" and len(options) != 1)):
            raise EmoteTemplatePackageError(f"部件附件列表无效：{name}")
        hide_slots = item.get("hideSlots", [])
        if (not isinstance(hide_slots, list) or len(hide_slots) > 16
                or not all(isinstance(value, str) and value and value != name for value in hide_slots)
                or (kind != "toggle" and hide_slots)):
            raise EmoteTemplatePackageError(f"部件替代插槽无效：{name}")
        attachment_labels = item.get("attachmentLabels", {})
        if not isinstance(attachment_labels, dict) or not all(
            key in options and isinstance(value, dict)
            and all(isinstance(label, str) and label.strip() for label in value.values())
            for key, value in attachment_labels.items()
        ):
            raise EmoteTemplatePackageError(f"附件标签无效：{name}")
        parts.append({"id": name, "displayName": labels.get("en", name), "labels": labels,
                      "kind": kind, "attachments": options, "hideSlots": hide_slots,
                      "attachmentLabels": attachment_labels})
    return parts


def inspect_spine_zip(data: bytes, *, filename="", model_path="", atlas_path="", part_profile: bytes | None = None) -> dict[str, Any]:
    if not isinstance(data, (bytes, bytearray, memoryview)) or len(data) > MAX_ARCHIVE_BYTES:
        raise EmoteTemplatePackageError("ZIP 超过 64 MiB 上限或内容无效")
    try:
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
    except (zipfile.BadZipFile, OSError) as exc:
        raise EmoteTemplatePackageError("不是有效的 ZIP 文件") from exc
    with archive:
        entries = _collect_entries(archive)
        skeletons, atlases = [], []
        scan_bytes = 0
        for path, info in entries.items():
            lower = path.lower()
            if lower.endswith((".atlas", ".atlas.txt")):
                atlases.append({"path": path})
                continue
            if lower.endswith((".json", ".skel", ".skel.bytes")):
                binary = lower.endswith((".skel", ".skel.bytes"))
                size = min(info.file_size, 256) if binary else info.file_size
                scan_bytes += size
                if scan_bytes > MAX_SCAN_BYTES:
                    raise EmoteTemplatePackageError("ZIP 元数据扫描超过 128 MiB 上限")
                if binary:
                    try:
                        with archive.open(info) as stream:
                            content = stream.read(256)
                    except (OSError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
                        raise EmoteTemplatePackageError(f"无法读取骨架：{path}") from exc
                    match = _VERSION.search(content)
                    if match:
                        skeletons.append({"path": path, "version": match[1].decode(), "data": None})
                elif lower.endswith(".json"):
                    try:
                        parsed = json.loads(_read(archive, info, MAX_MODEL_BYTES).decode("utf-8-sig"))
                    except (ValueError, UnicodeError):
                        continue
                    if isinstance(parsed, dict) and isinstance(parsed.get("bones"), list) and parsed["bones"]:
                        header = parsed.get("skeleton")
                        version = header.get("spine") if isinstance(header, dict) else None
                        if isinstance(version, str):
                            skeletons.append({"path": path, "version": version, "data": parsed})
        skeleton = _choose(skeletons, "model", model_path)
        if ".".join(skeleton["version"].split(".")[:2]) != SPINE_VERSION_FAMILY:
            raise EmoteTemplatePackageError("当前仅支持 Spine 4.3 导出资源")
        matching = [item for item in atlases if _stem(item["path"]).casefold() == _stem(skeleton["path"]).casefold()]
        atlas = _choose(atlases if atlas_path else matching or atlases, "atlas", atlas_path)
        if scan_bytes + entries[atlas["path"]].file_size > MAX_SCAN_BYTES:
            raise EmoteTemplatePackageError("ZIP 元数据扫描超过 128 MiB 上限")
        try:
            text = _read(archive, entries[atlas['path']], MAX_METADATA_FILE_BYTES).decode('utf-8-sig')
        except UnicodeDecodeError as exc:
            raise EmoteTemplatePackageError(f"图集不是有效的 UTF-8 文本：{atlas['path']}") from exc
        page_names, regions = _atlas_pages(text)
        atlas.update(pages=page_names, regions=regions)
        pages = [{"name": name, "path": _resolve_image(entries, atlas["path"], name)} for name in atlas["pages"]]
        model_data = skeleton["data"] or {}
        missing_regions = set(_json_attachments(model_data)) - atlas["regions"]
        if missing_regions:
            raise EmoteTemplatePackageError("图集缺少附件区域：" + ", ".join(sorted(missing_regions)[:5]))
        parts = _parse_native_parts(part_profile) if part_profile is not None else []
        resources = {skeleton["path"], atlas["path"], *(item["path"] for item in pages)}
        # Keep the content identity of archives saved before parts became
        # built-in-only. Uploaded manifest bytes never drive the editor.
        manifest_path = str(PurePosixPath(skeleton["path"]).with_name(NATIVE_MANIFEST))
        if manifest_path in entries:
            resources.add(manifest_path)
        resources = sorted(resources)
        digest = hashlib.sha256()
        for path in resources:
            encoded_path = path.encode("utf-8")
            resource_digest = hashlib.sha256(_read(archive, entries[path], MAX_MODEL_BYTES)).digest()
            digest.update(len(encoded_path).to_bytes(4, "big") + encoded_path)
            digest.update(resource_digest)
        identity = "spine-" + digest.hexdigest()
        raw_animations = model_data.get("animations") or {}
        animations = list(raw_animations) if isinstance(raw_animations, dict) else []
        skin_data = model_data.get("skins") or []
        skins = list(skin_data) if isinstance(skin_data, dict) else [item.get("name") for item in skin_data if isinstance(item, dict)]
        default_skin = "default" if "default" in skins else (skins[0] if skins else None)
        scenes = [spine_animation_scene(name) for name in animations]
        return {
            "schemaVersion": EMOTE_TEMPLATE_SCHEMA_VERSION, "id": identity,
            "source": {"kind": "spine", "packageId": identity, "packageName": PurePosixPath(filename.replace("\\", "/")).name,
                       "spineVersion": skeleton["version"], "assetsPersisted": False},
            "model": {"id": identity, "displayName": PurePosixPath(filename).stem if filename else _stem(skeleton["path"]),
                      "skeleton": {"path": skeleton["path"], "bytes": entries[skeleton["path"]].file_size, "spineVersion": skeleton["version"]},
                      "atlas": {"path": atlas["path"], "pages": pages}},
            "character": {"skins": [{"group": "skin", "value": default_skin}] if default_skin else [],
                          "slots": [{"name": item["id"], "attachmentMode": "auto"} for item in parts]},
            "catalogs": {"animations": [{"id": name, "displayName": name, "labels": {}} for name in animations],
                         "skinGroups": [{"id": "skin", "displayName": "Skin", "labels": {}}] if skins else [],
                         "slotGroups": parts},
            "scenes": scenes,
            "resources": resources,
            "counts": {"atlasPages": len(pages), "animations": len(animations), "skinGroups": int(bool(skins)),
                       "slotGroups": len(parts), "scenes": len(scenes), "activeSkins": int(bool(default_skin))},
            "capabilities": {"metadata": True, "spineRender": False, "staticPngExport": False, "animatedExport": False,
                             "characterSkinSelection": bool(skins), "characterSlotTint": bool(parts),
                             "characterPartSelection": bool(parts)},
            "warnings": [],
        }


def spine_animation_scene(name):
    return {"id": "animation:" + name, "name": name,
            "animation": {"id": name, "time": 0, "speed": 1, "paused": True}}


def spine_resource_archive(data: bytes, package: dict) -> bytes:
    """Keep only selected resources; preserve their internal relative paths."""
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as target:
        entries = _collect_entries(source)
        for path in package["resources"]:
            info = entries[path]
            content = _read(source, info, MAX_MODEL_BYTES)
            compression = info.compress_type
            if compression == zipfile.ZIP_DEFLATED and len(content) > max(1, len(zlib.compress(content)) - 6) * 1000:
                compression = zipfile.ZIP_STORED
            target.writestr(path, content, compress_type=compression)
    if output.tell() > MAX_ARCHIVE_BYTES:
        raise EmoteTemplatePackageError("整理后的 ZIP 超过大小上限")
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as verified:
        _collect_entries(verified)
    return output.getvalue()
