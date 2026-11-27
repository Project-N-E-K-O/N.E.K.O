#!/usr/bin/env python3
"""Package approved YUI animation images without re-encoding from an approved runtime directory.

Run from a checkout: python scripts/pack_yui_chibi.py --source-dir PATH
The source may contain legacy /static/pngtuber/yui-chibi/ URLs. No pixels,
frame timings, mouth geometry or transition settings are changed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import unquote

from unpack_builtin_pngtuber import MAX_FILE_SIZE, _validate_package, parts_fingerprint

PART_SIZE = 64 * 1024 * 1024
PART_FILES = 600


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def build(source: Path, packs: Path, compatibility_dir: Path | None = None):
    source = source.resolve()
    metadata = json.loads(
        (source / "metadata.actions.json").read_text(encoding="utf-8")
    )
    model = json.loads((source / "model.json").read_text(encoding="utf-8"))
    authoring = packs / "yui-chibi-source"
    records = []
    # This temporary directory is owned by this invocation; never clear the source.
    with tempfile.TemporaryDirectory(prefix="yui-pack-") as temp:
        stage = Path(temp)

        def copy_asset(raw, relative):
            raw = unquote(raw)
            prefix = "/static/pngtuber/yui-chibi/"
            if raw.startswith(prefix):
                raw = raw[len(prefix) :]
            origin = (source / raw).resolve()
            origin.relative_to(source)
            if not origin.is_file():
                raise ValueError(f"Missing source asset: {raw}")
            dest = stage / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(origin, dest)
            records.append(
                {"path": relative, "bytes": dest.stat().st_size, "sha256": digest(dest)}
            )
            return relative

        for state in metadata["state_catalog"]:
            key, name = str(state["state_index"]), state["name"]
            if not re.fullmatch(r"[a-z0-9-]+", name):
                raise ValueError("Non-portable action identifier")
            for index, frame in enumerate(
                metadata["tracked_animations"][key]["frames"]
            ):
                extension = Path(unquote(frame["image"])).suffix.lower()
                if extension not in {".png", ".webp"}:
                    raise ValueError("Unsupported reviewed body image format")
                frame["image"] = copy_asset(
                    frame["image"], f"clips/{name}/frames/{index:04}{extension}"
                )
                patch = frame.get("mouth_patch")
                if patch:
                    patch["image"] = copy_asset(
                        patch["image"], f"clips/{name}/mouths/{index:04}.png"
                    )
        metadata["tracked_animation"] = metadata["tracked_animations"]["0"]
        first = metadata["tracked_animation"]["frames"][0]["image"]
        metadata["layers"][0]["image"] = first
        # Keep the old PNG entry URLs valid for persisted character settings.
        # Body frames and mouth plates are always copied byte-for-byte.
        (stage / "assets").mkdir()
        for relative in ("idle.png", "assets/idle-first.png"):
            external = (
                compatibility_dir / relative if compatibility_dir else source / relative
            )
            if external.is_file():
                shutil.copyfile(external, stage / relative)
            elif Path(first).suffix == ".png":
                shutil.copyfile(stage / first, stage / relative)
            else:
                from PIL import Image

                with Image.open(stage / first) as image:
                    image.save(stage / relative, format="PNG")
        model["name"] = "YUI Chibi"
        model["pngtuber"]["idle_image"] = model["pngtuber"]["talking_image"] = (
            "idle.png"
        )
        model["pngtuber"]["layered_metadata"] = "metadata.actions.json"
        write_json(stage / "model.json", model)
        write_json(stage / "metadata.actions.json", metadata)
        _validate_package(stage)
        members = sorted(p for p in stage.rglob("*") if p.is_file())
        groups, group, size = [], [], 0
        for path in members:
            amount = path.stat().st_size
            if amount > MAX_FILE_SIZE:
                raise ValueError(f"Individual asset too large: {path.name}")
            if group and (size + amount > PART_SIZE or len(group) >= PART_FILES):
                groups.append(group)
                group, size = [], 0
            group.append(path)
            size += amount
        if group:
            groups.append(group)
        if len(groups) > 32:
            raise ValueError("Too many archive parts")
        parts = []
        for index, group in enumerate(groups, 1):
            name = f"yui-chibi-{index:03}.zip"
            output = packs / name
            temporary = stage / name
            with zipfile.ZipFile(
                temporary, "w", zipfile.ZIP_DEFLATED, compresslevel=6
            ) as archive:
                for path in group:
                    info = zipfile.ZipInfo(
                        path.relative_to(stage).as_posix(), (2026, 1, 1, 0, 0, 0)
                    )
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o100644 << 16
                    with path.open("rb") as src, archive.open(info, "w") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
            shutil.copyfile(temporary, output)
            parts.append(
                {
                    "archive": name,
                    "archive_sha256": digest(output),
                    "archive_size": output.stat().st_size,
                    "file_count": len(group),
                    "unpacked_size": sum(p.stat().st_size for p in group),
                }
            )
            print(f"Built {name} ({output.stat().st_size:,} bytes)", flush=True)
        entry = {
            "folder": "yui-chibi",
            "archive": parts[0]["archive"],
            "archives": parts,
            "archive_sha256": parts_fingerprint(parts),
            "file_count": sum(p["file_count"] for p in parts),
            "unpacked_size": sum(p["unpacked_size"] for p in parts),
        }
        manifest_path = packs / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["models"] = [
            m for m in manifest["models"] if m["folder"] != "yui-chibi"
        ] + [entry]
        write_json(manifest_path, manifest)
        for name in ("model.json", "metadata.actions.json"):
            shutil.copyfile(stage / name, authoring / name)
        write_json(
            authoring / "reviewed-resource-manifest.json",
            {
                "version": 1,
                "model": "yui-chibi",
                "image_count": len(records),
                "resources": records,
                "package_fingerprint": entry["archive_sha256"],
            },
        )
        return entry


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument(
        "--packs-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "frontend/pngtuber-packs",
    )
    parser.add_argument(
        "--compatibility-dir",
        type=Path,
        help="Optional existing idle.png and assets/idle-first.png to preserve byte-for-byte",
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build(args.source_dir, args.packs_dir, args.compatibility_dir), indent=2
        )
    )
