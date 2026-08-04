"""Prepare pinned Smart Turn/Silero assets and verify every byte before use."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import os
import shutil
import sys
import time
import urllib.request
from urllib.error import URLError
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_asset_manifest_module():
    """Load asset_manifest without importing the heavy ASR client facade.

    Docker builds run this script before ``uv sync`` installs project
    dependencies. ``asset_manifest`` itself is stdlib-only, so load it
    directly by file path when the canonical package import is unavailable.
    """
    module_name = "main_logic.asr_client.endpointing.asset_manifest"
    existing = sys.modules.get(module_name)
    if existing is not None:
        return existing
    try:
        # Prefer the ordinary package import: whenever project dependencies
        # exist it yields the canonical module with proper parent linkage, so
        # a later canonical package import cannot
        # trip over a child registered without its parent package.
        return importlib.import_module(module_name)
    except ImportError:
        pass
    # Bare interpreter (Docker asset step runs before ``uv sync``): the
    # package imports are unavailable, so load the stdlib-only module by path.
    module_path = (
        PROJECT_ROOT
        / "main_logic"
        / "asr_client"
        / "endpointing"
        / "asset_manifest.py"
    )
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load asset manifest module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    # Register under the canonical dotted name so a later full-package import
    # reuses this module object and exception identity stays consistent.
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


_asset_manifest = _load_asset_manifest_module()
AssetManifestError = _asset_manifest.AssetManifestError
load_manifest = _asset_manifest.load_manifest
verify_asset = _asset_manifest.verify_asset
require_downloadable_source = _asset_manifest.require_downloadable_source


def _download_source_candidates(source: str) -> tuple[str, ...]:
    """Prefer HF_ENDPOINT / hf-mirror when the pinned source is on huggingface.co.

    Direct huggingface.co downloads often time out from mainland networks; the
    bytes are still SHA-256 verified against the manifest, so mirrors are safe.
    """

    candidates: list[str] = [source]
    if "huggingface.co/" not in source:
        return tuple(candidates)

    endpoint = (os.environ.get("HF_ENDPOINT") or "").strip().rstrip("/")
    if endpoint:
        rewritten = source.replace("https://huggingface.co", endpoint, 1)
        if rewritten not in candidates:
            candidates.insert(0, rewritten)

    mirror = source.replace("https://huggingface.co", "https://hf-mirror.com", 1)
    if mirror not in candidates:
        candidates.append(mirror)
    return tuple(candidates)


def _download_verified(source: str, destination: Path, expected_sha256: str) -> None:
    temporary = destination.with_suffix(destination.suffix + ".part")
    last_error: Exception | None = None
    for candidate in _download_source_candidates(source):
        request = urllib.request.Request(
            candidate, headers={"User-Agent": "NEKO-asset-preparer/1"}
        )
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
            _download_verified(
                require_downloadable_source(spec),
                directory / spec.filename,
                spec.sha256,
            )
        prepared.append(verify_asset(directory, spec))
    return prepared


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--asset-dir",
        type=Path,
        default=PROJECT_ROOT / "main_logic" / "asr_client" / "endpointing" / "models",
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
