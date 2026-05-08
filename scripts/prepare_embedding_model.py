# -*- coding: utf-8 -*-
"""Download local embedding model assets for development and packaging.

The runtime uses an anonymous profile id (for example
``local-text-retrieval-v1``). This script maps a concrete model repository
onto that profile folder at build time, so source, PyInstaller and Nuitka
builds all share the same on-disk layout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_PROFILE_ID = "local-text-retrieval-v1"
DEFAULT_OUTPUT_ROOT = Path("data") / "embedding_models"
PREPARED_MARKER = ".prepared.json"
# 40-char lowercase hex git SHA. Tags / branch refs / short SHAs are rejected
# so the profile id stays a strict compatibility contract — anything that can
# move under our feet, even tags (which can be force-pushed), is excluded.
_SHA40_RE = re.compile(r"^[0-9a-f]{40}$")

FILES_BY_VARIANT = {
    "fp32": (
        "tokenizer.json",
        "onnx/model.onnx",
        "onnx/model.onnx_data",
    ),
    "int8": (
        "tokenizer.json",
        "onnx/model_quantized.onnx",
        "onnx/model_quantized.onnx_data",
    ),
}


def _iter_files(variant: str) -> list[str]:
    if variant == "both":
        files: list[str] = []
        for group in FILES_BY_VARIANT.values():
            for item in group:
                if item not in files:
                    files.append(item)
        return files
    return list(FILES_BY_VARIANT[variant])


def _download(url: str, dest: Path, *, force: bool) -> None:
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"[embedding-model] keep existing {dest}")
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    print(f"[embedding-model] download {url}")
    try:
        with urllib.request.urlopen(url, timeout=120) as response:
            with tmp.open("wb") as f:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
    except urllib.error.URLError as exc:
        if tmp.exists():
            tmp.unlink()
        raise RuntimeError(f"failed to download {url}: {exc}") from exc
    os.replace(tmp, dest)
    print(f"[embedding-model] wrote {dest} ({dest.stat().st_size} bytes)")


def _verify(profile_dir: Path, files: list[str]) -> None:
    missing = [
        str(profile_dir / rel)
        for rel in files
        if not (profile_dir / rel).exists() or (profile_dir / rel).stat().st_size <= 0
    ]
    if missing:
        raise RuntimeError("embedding model asset check failed; missing: " + ", ".join(missing))


def _read_marker(profile_dir: Path) -> dict | None:
    marker = profile_dir / PREPARED_MARKER
    if not marker.exists():
        return None
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write_marker(profile_dir: Path, repo: str, revision: str) -> None:
    profile_dir.mkdir(parents=True, exist_ok=True)
    marker = profile_dir / PREPARED_MARKER
    marker.write_text(
        json.dumps({"repo": repo, "revision": revision}, indent=2),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        required=True,
        help="Concrete Hugging Face repo to mirror into the anonymous profile folder.",
    )
    parser.add_argument(
        "--revision",
        required=True,
        help=(
            "Pinned upstream commit SHA (40 lowercase hex chars). Branch refs "
            "and tags are rejected: the profile id is the compatibility "
            "contract, so anything that can move — including tags, which can "
            "be force-pushed upstream — is excluded."
        ),
    )
    parser.add_argument("--profile-id", default=DEFAULT_PROFILE_ID)
    parser.add_argument(
        "--output-root",
        default=str(DEFAULT_OUTPUT_ROOT),
        help="Directory containing embedding profile subdirectories.",
    )
    parser.add_argument(
        "--variant",
        choices=("fp32", "int8", "both"),
        default="both",
        help="Which ONNX weights to download.",
    )
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args(argv)

    if not _SHA40_RE.match(args.revision):
        parser.error(
            "--revision must be a 40-char lowercase hex commit SHA "
            f"(got {args.revision!r}); branch refs like 'main'/'dev' and "
            "tags are rejected because the profile id must stay reproducible."
        )

    files = _iter_files(args.variant)
    profile_dir = Path(args.output_root) / args.profile_id

    # Force re-download whenever the (repo, revision) pair changed since the
    # last successful prepare for this profile. Without this, a second run
    # against a different revision would silently keep the old non-empty
    # files (size>0 satisfies _download's skip), and ship weights that don't
    # match the revision the build claims to be pinned to.
    existing = _read_marker(profile_dir)
    revision_changed = bool(
        existing
        and (existing.get("repo") != args.repo or existing.get("revision") != args.revision)
    )
    if revision_changed:
        print(
            f"[embedding-model] profile previously prepared from "
            f"{existing.get('repo')}@{existing.get('revision')}; "
            f"forcing re-download for {args.repo}@{args.revision}",
        )

    for rel in files:
        url = f"https://huggingface.co/{args.repo}/resolve/{args.revision}/{rel}"
        _download(url, profile_dir / rel, force=args.force or revision_changed)
    _verify(profile_dir, files)
    _write_marker(profile_dir, args.repo, args.revision)
    print(f"[embedding-model] profile ready: {profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
