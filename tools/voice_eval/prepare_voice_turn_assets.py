"""Prepare pinned Smart Turn/Silero assets and verify every byte before use."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
import urllib.request
from urllib.error import URLError
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.voice_turn.asset_manifest import (  # noqa: E402
    AssetManifestError,
    load_manifest,
    verify_asset,
)


def _download_verified(source: str, destination: Path, expected_sha256: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    digest = hashlib.sha256()
    request = urllib.request.Request(source, headers={"User-Agent": "NEKO-asset-preparer/1"})
    last_error: Exception | None = None
    for attempt in range(3):
        digest = hashlib.sha256()
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
                "wb"
            ) as output:
                while chunk := response.read(1024 * 1024):
                    digest.update(chunk)
                    output.write(chunk)
            actual = digest.hexdigest()
            if actual.lower() != expected_sha256.lower():
                raise AssetManifestError(
                    f"download SHA-256 mismatch for {destination.name}: "
                    f"expected {expected_sha256}, got {actual}"
                )
            os.replace(temporary, destination)
            return
        except AssetManifestError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, TimeoutError, URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1 << attempt)
    raise AssetManifestError(f"cannot download {destination.name}: {last_error}") from last_error


def _copy_verified(source: Path, destination: Path, expected_sha256: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        shutil.copyfile(source, temporary)
        actual = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if actual.lower() != expected_sha256.lower():
            raise AssetManifestError(
                f"cache SHA-256 mismatch for {destination.name}: expected {expected_sha256}, got {actual}"
            )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_assets(
    directory: Path, *, offline: bool = False, source_cache: Path | None = None
) -> list[Path]:
    manifest = load_manifest(directory)
    prepared: list[Path] = []
    directory.mkdir(parents=True, exist_ok=True)
    for spec in manifest.assets:
        try:
            prepared.append(verify_asset(directory, spec))
            continue
        except AssetManifestError:
            if offline:
                raise
        cached = source_cache / spec.filename if source_cache is not None else None
        if cached is not None and cached.is_file():
            _copy_verified(cached, directory / spec.filename, spec.sha256)
        else:
            _download_verified(spec.source, directory / spec.filename, spec.sha256)
        prepared.append(verify_asset(directory, spec))
    return prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "vad_models",
        help="directory containing manifest.json and prepared assets",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="verify existing assets without making network requests",
    )
    parser.add_argument(
        "--source-cache",
        type=Path,
        help="optional directory of pre-fetched files; every file is still SHA-256 verified",
    )
    args = parser.parse_args(argv)
    try:
        paths = prepare_assets(
            args.asset_dir.resolve(),
            offline=args.offline,
            source_cache=args.source_cache.resolve() if args.source_cache else None,
        )
    except AssetManifestError as exc:
        parser.error(str(exc))
    for path in paths:
        print(f"verified {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
