"""Immutable, bounded Live2D transfer copies. Source models are never rewritten."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from uuid import uuid4
import zipfile

from PIL import Image

from services.chat_sync.model_protocol import MAX_RESOURCE_BYTES as MAX_MODEL_BYTES, decode, encode, relative_path

TEXTURE_MAX_EDGE = 2048


def dependencies(raw):
    if len(raw) > 1024 * 1024:
        raise ValueError("invalid_cloud_model")
    model = json.loads(raw)
    refs = model.get("FileReferences") if isinstance(model, dict) else None
    if (not isinstance(refs, dict) or not isinstance(refs.get("Moc"), str)
            or not isinstance(refs.get("Textures"), list)
            or not all(isinstance(item, str) for item in refs["Textures"])):
        raise ValueError("invalid_cloud_model")
    result = []

    def visit(value, key=""):
        if isinstance(value, str) and key != "Name" and value:
            result.append(relative_path(value))
        elif isinstance(value, dict):
            for field, item in value.items():
                visit(item, field)
        elif isinstance(value, list):
            for item in value:
                visit(item, key)
    visit(refs)
    return list(dict.fromkeys(result)), set(refs["Textures"])


def package_model(model_file, destination):
    source = Path(model_file).resolve(strict=True)
    if not source.name.endswith(".model3.json") or source.stat().st_size > 1024 * 1024:
        raise ValueError("invalid_cloud_model")
    root = source.parent
    for ancestor in [root, *list(root.parents)[:8]]:
        receipt, copy = ancestor / ".cloud-model.json", ancestor / ".cloud-model.zip"
        if receipt.is_file() and copy.is_file() and receipt.stat().st_size <= 131072:
            saved = json.loads(receipt.read_bytes())
            binding = saved if "schemaVersion" in saved else {"schemaVersion": 1, "model": saved}
            model = decode(encode(binding))["model"]
            if model and source == ancestor / model["modelFile"]:
                checked = install_model(copy, binding, ancestor.parent)
                if checked.resolve() != source:
                    raise ValueError("invalid_cloud_model_cache")
                resource = model["package"]["resource"]
                if "url" in resource:
                    return binding, None
                identity = resource["resourceId"]
                return binding, {"resourceId": identity, "purpose": "model", "path": str(copy), "size": model["package"]["size"],
                                 "sha256": model["package"]["sha256"], "contentType": "application/zip"}
    paths, textures = dependencies(source.read_bytes())
    # Some shipped expression/motion files are selected by existing runtime catalogs
    # instead of FileReferences. Retain those model resources too, excluding device settings.
    resource_suffixes = (".moc3", ".motion3.json", ".exp3.json", ".physics3.json", ".pose3.json",
                         ".cdi3.json", ".userdata3.json", ".motionsync3.json", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".wav", ".mp3", ".ogg")
    extra = [path.relative_to(root).as_posix() for path in root.rglob("*")
             if path.is_file() and path.name.lower().endswith(resource_suffixes)]
    paths = list(dict.fromkeys([source.name, *paths, *sorted(extra)]))
    if len(paths) > 512 or len({path.lower() for path in paths}) != len(paths):
        raise ValueError("invalid_cloud_model")
    target = Path(destination)
    target.mkdir(parents=True, exist_ok=True)
    identity, entries, total = uuid4().hex, [], 0
    package = target / (identity + ".zip")
    created = False
    try:
        with package.open("xb") as package_stream:
            created = True
            with zipfile.ZipFile(package_stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
                for path in paths:
                    candidate = (root / path).resolve(strict=True)
                    if not candidate.is_relative_to(root) or not candidate.is_file() or candidate.stat().st_size > MAX_MODEL_BYTES:
                        raise ValueError("invalid_cloud_model")
                    raw = candidate.read_bytes()
                    total += len(raw)
                    if total > MAX_MODEL_BYTES:
                        raise ValueError("cloud_model_too_large")
                    if path in textures:
                        with Image.open(io.BytesIO(raw)) as image:
                            # An 8K atlas is reduced before it enters the transfer package.
                            if image.width * image.height > 8192 * 8192:
                                raise ValueError("cloud_model_texture_too_large")
                            if max(image.size) > TEXTURE_MAX_EDGE:
                                texture = image.convert("RGBA" if image.format != "JPEG" else "RGB")
                                texture.thumbnail((TEXTURE_MAX_EDGE, TEXTURE_MAX_EDGE), Image.Resampling.LANCZOS)
                                buffer = io.BytesIO()
                                texture.save(buffer, format=image.format, optimize=True, **({"quality": 75} if image.format == "JPEG" else {}))
                                raw = buffer.getvalue()
                    # Stable ZIP metadata prevents retries from changing a prepared artifact.
                    info = zipfile.ZipInfo(path, (1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    archive.writestr(info, raw, compresslevel=9)
                    entries.append({"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        if package.stat().st_size > MAX_MODEL_BYTES:
            raise ValueError("cloud_model_too_large")
        with package.open("rb") as stream:
            package_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        descriptor = {"modelFile": source.name,
                      "package": {"resource": {"resourceId": identity}, "size": package.stat().st_size,
                                  "sha256": package_hash},
                      "files": entries, "compression": {"version": 1, "format": "zip-deflate", "textureMaxEdge": TEXTURE_MAX_EDGE}}
        binding = decode(encode({"schemaVersion": 1, "model": descriptor}))
        return binding, {"resourceId": identity, "purpose": "model", "path": str(package), "size": package.stat().st_size,
                         "sha256": descriptor["package"]["sha256"], "contentType": "application/zip"}
    except BaseException:
        if created:
            package.unlink(missing_ok=True)
        raise


def install_model(package, binding, destination):
    validated = decode(encode(binding))
    model = validated["model"]
    if model is None:
        raise ValueError("invalid_cloud_model")
    archive_path = Path(package)
    meta = model["package"]
    with archive_path.open("rb") as stream:
        if archive_path.stat().st_size != meta["size"] or hashlib.file_digest(stream, "sha256").hexdigest() != meta["sha256"]:
            raise ValueError("invalid_cloud_model_hash")
    root = Path(destination) / meta["sha256"]
    entries = {item["path"]: item for item in model["files"]}
    if any(path in entries for path in (".cloud-model.json", ".cloud-model.zip")):
        raise ValueError("invalid_cloud_model_files")
    # Validate everything before installing under an immutable version path.
    raw_files, total = {}, 0
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.infolist()) != len(entries):
            raise ValueError("invalid_cloud_model_files")
        for info in archive.infolist():
            path = relative_path(info.filename)
            entry = entries.get(path)
            if (not entry or path in raw_files or info.is_dir() or info.flag_bits & 1
                    or (info.external_attr >> 16) & 0o170000 == 0o120000
                    or info.file_size != entry["size"] or info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}):
                raise ValueError("invalid_cloud_model_files")
            total += info.file_size
            if total > MAX_MODEL_BYTES:
                raise ValueError("cloud_model_too_large")
            raw = archive.read(info)
            if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                raise ValueError("invalid_cloud_model_hash")
            raw_files[path] = raw
    deps, _ = dependencies(raw_files[model["modelFile"]])
    model_parent = Path(model["modelFile"]).parent
    if any((model_parent / path).as_posix() not in entries for path in deps):
        raise ValueError("invalid_cloud_model_dependencies")
    if root.exists():
        for path, entry in entries.items():
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root.resolve()) or not candidate.is_file() or candidate.stat().st_size != entry["size"]:
                raise ValueError("invalid_cloud_model_cache")
            with candidate.open("rb") as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != entry["sha256"]:
                    raise ValueError("invalid_cloud_model_cache")
        return root / model["modelFile"]
    stage = root.parent / (meta["sha256"] + "." + uuid4().hex + ".part")
    import shutil
    try:
        stage.mkdir(parents=True)
        for path, raw in raw_files.items():
            target = stage / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        # This installation receipt reuses the received artifact on later round trips.
        (stage / ".cloud-model.json").write_bytes(encode(binding))
        shutil.copyfile(archive_path, stage / ".cloud-model.zip")
        try:
            stage.rename(root)
        except OSError:
            if not root.is_dir():
                raise
            # Another installer won; validate its completed immutable directory.
            return install_model(package, binding, destination)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return root / model["modelFile"]
