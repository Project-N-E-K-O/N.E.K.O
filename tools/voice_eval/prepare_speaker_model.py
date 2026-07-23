"""Prepare the pinned CAM++ speaker model and verify every byte before use."""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import URLError


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from main_logic.voice_identity.campplus import (  # noqa: E402
    CampPlusAssetError,
    load_campplus_manifest,
    verify_campplus_asset,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_transfer(path: Path, *, expected_size: int, expected_sha256: str) -> None:
    actual_size = path.stat().st_size
    if actual_size != expected_size:
        raise CampPlusAssetError(
            f"CAM++ transfer size mismatch: expected {expected_size}, got {actual_size}"
        )
    actual_sha256 = _sha256_file(path)
    if actual_sha256.lower() != expected_sha256.lower():
        raise CampPlusAssetError(
            "CAM++ transfer SHA-256 mismatch: "
            f"expected {expected_sha256}, got {actual_sha256}"
        )


def _download_verified(
    source: str,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(
        source,
        headers={"User-Agent": "NEKO-speaker-model-preparer/1"},
    )
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open(
                "wb"
            ) as output:
                shutil.copyfileobj(response, output, length=1024 * 1024)
            _verify_transfer(
                temporary,
                expected_size=expected_size,
                expected_sha256=expected_sha256,
            )
            os.replace(temporary, destination)
            return
        except CampPlusAssetError:
            temporary.unlink(missing_ok=True)
            raise
        except (OSError, TimeoutError, URLError) as exc:
            last_error = exc
            temporary.unlink(missing_ok=True)
            if attempt < 2:
                time.sleep(1 << attempt)
    raise CampPlusAssetError(f"cannot download CAM++ model: {last_error}") from last_error


def _copy_verified(
    source: Path,
    destination: Path,
    *,
    expected_size: int,
    expected_sha256: str,
) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    try:
        shutil.copyfile(source, temporary)
        _verify_transfer(
            temporary,
            expected_size=expected_size,
            expected_sha256=expected_sha256,
        )
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_speaker_model(
    directory: Path,
    *,
    offline: bool = False,
    source_cache: Path | None = None,
) -> Path:
    """Prepare one pinned asset; release callers use ``offline`` to hard-fail."""

    manifest = load_campplus_manifest(directory)
    directory.mkdir(parents=True, exist_ok=True)
    try:
        return verify_campplus_asset(directory)
    except CampPlusAssetError:
        if offline:
            raise

    destination = directory / manifest.filename
    cached = source_cache / manifest.filename if source_cache is not None else None
    if cached is not None and cached.is_file():
        _copy_verified(
            cached,
            destination,
            expected_size=manifest.size_bytes,
            expected_sha256=manifest.sha256,
        )
    else:
        _download_verified(
            manifest.source,
            destination,
            expected_size=manifest.size_bytes,
            expected_sha256=manifest.sha256,
        )
    return verify_campplus_asset(directory)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "speaker_models",
        help="directory containing manifest.json and the prepared CAM++ model",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="verify the existing model without making network requests",
    )
    parser.add_argument(
        "--source-cache",
        type=Path,
        help="optional directory containing the manifest filename; still fully verified",
    )
    args = parser.parse_args(argv)
    try:
        path = prepare_speaker_model(
            args.asset_dir.resolve(),
            offline=args.offline,
            source_cache=args.source_cache.resolve() if args.source_cache else None,
        )
    except CampPlusAssetError as exc:
        parser.error(str(exc))
    print(f"verified {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
