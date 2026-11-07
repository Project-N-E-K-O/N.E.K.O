#!/usr/bin/env python3
"""Unpack the built-in Live2D models from ``assets/`` with the standard library only.

This mirrors the ``unpack_live2d`` step of ``build_frontend.bat`` for machines
that cannot run that script: Windows 7 ships neither ``tar.exe`` nor ``uv``.
``setup_win7.bat`` calls it with the project venv interpreter.

Each ``assets/<model>.tar.gz`` is extracted into a temporary directory under
``static/``, checked for the files the frontend loads, marked complete with
``.unpacked`` and then swapped into ``static/<model>/``. A model is skipped
when the marker and required files exist and the archive is not newer than
the marker, matching ``build_frontend.bat``.
"""

from __future__ import annotations

import shutil
import tarfile
import uuid
from pathlib import Path, PurePosixPath


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ASSETS_ROOT = PROJECT_ROOT / "assets"
STATIC_ROOT = PROJECT_ROOT / "static"
MODELS = ("yui-origin", "yui-lolita")
COMPLETE_MARKER = ".unpacked"
REQUIRED_FILES = (
    "{model}.moc3",
    "{model}.model3.json",
    "{model}.physics3.json",
    "{model}.vtube.json",
    "{model}.4096/texture_00.png",
)


def _missing_files(model_dir: Path, model: str) -> list[str]:
    return [
        name.format(model=model)
        for name in REQUIRED_FILES
        if not (model_dir / name.format(model=model)).is_file()
    ]


def _is_up_to_date(archive: Path, model_dir: Path, model: str) -> bool:
    marker = model_dir / COMPLETE_MARKER
    if not marker.is_file() or _missing_files(model_dir, model):
        return False
    return archive.stat().st_mtime <= marker.stat().st_mtime


def _safe_members(archive: tarfile.TarFile, model: str) -> list[tarfile.TarInfo]:
    members = archive.getmembers()
    for member in members:
        path = PurePosixPath(member.name.replace("\\", "/"))
        if (
            path.is_absolute()
            or not path.parts
            or path.parts[0] != model
            or any(part in ("", ".", "..") for part in path.parts)
        ):
            raise ValueError(f"unsafe archive path in {model}: {member.name}")
        if not (member.isfile() or member.isdir()):
            raise ValueError(f"unsupported archive member type in {model}: {member.name}")
    return members


def unpack_model(
    model: str,
    assets_root: Path = ASSETS_ROOT,
    static_root: Path = STATIC_ROOT,
) -> Path:
    archive_path = assets_root / f"{model}.tar.gz"
    model_dir = static_root / model
    if not archive_path.is_file():
        raise FileNotFoundError(f"{model} archive missing: {archive_path}")
    if _is_up_to_date(archive_path, model_dir, model):
        print(f"[unpack_live2d] {model} up to date, skip")
        return model_dir

    print(f"[unpack_live2d] unpacking {model}...")
    static_root.mkdir(parents=True, exist_ok=True)
    suffix = uuid.uuid4().hex
    temp_root = static_root / f".{model}.extract-{suffix}"
    backup_dir = static_root / f".{model}.backup-{suffix}"
    temp_root.mkdir()
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = _safe_members(archive, model)
            extract_kwargs = {"filter": "data"} if hasattr(tarfile, "data_filter") else {}
            archive.extractall(temp_root, members=members, **extract_kwargs)

        extracted = temp_root / model
        missing = _missing_files(extracted, model)
        if missing:
            raise ValueError(f"{model} missing after unpack: {', '.join(missing)}")
        (extracted / COMPLETE_MARKER).touch()

        if model_dir.exists():
            model_dir.rename(backup_dir)
        try:
            extracted.rename(model_dir)
        except Exception:
            if backup_dir.exists() and not model_dir.exists():
                backup_dir.rename(model_dir)
            raise
        # The new model is already in place: a cleanup failure (for example an
        # antivirus scanner holding a file) must not fail the whole unpack.
        shutil.rmtree(backup_dir, ignore_errors=True)
        if backup_dir.exists():
            # find_models() walks static/ recursively and would list the
            # leftover copy as an extra model, so ask the user to remove it.
            print(
                f"[unpack_live2d] WARN: could not remove old copy {backup_dir}, "
                "please delete it manually"
            )
        print(f"[unpack_live2d] {model} done: {model_dir}")
        return model_dir
    finally:
        if temp_root.exists():
            shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    for model in MODELS:
        unpack_model(model)


if __name__ == "__main__":
    main()
