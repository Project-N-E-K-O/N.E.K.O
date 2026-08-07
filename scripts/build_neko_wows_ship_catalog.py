#!/usr/bin/env python3
"""Build and atomically activate a local Neko WoWS ship catalog."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
import tempfile
import urllib.request
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

# Importing through the plugin package initializes the host logger, which emits
# one locale-encoded discovery line on Windows. CLI stdout is a JSON contract,
# so keep parent-package import chatter away from that channel.
with contextlib.redirect_stdout(io.StringIO()):
    from plugin.plugins.neko_wows.ship_data.source_wowsinfo import build_catalog

RAW_BASE = "https://raw.githubusercontent.com/wowsinfo/data"
PINNED_PATHS = (
    "live/app/data/wowsinfo.json",
    "live/app/lang/lang.json",
)
MAX_SOURCE_BYTES = 64 * 1024 * 1024


def download_pinned_sources(
    revision: str,
    destination: Path,
    *,
    opener=urllib.request.urlopen,
    timeout: float = 30.0,
) -> tuple[Path, Path]:
    """Fetch only the two approved paths at an immutable commit SHA."""
    if re.fullmatch(r"[0-9a-fA-F]{40}", str(revision or "")) is None:
        raise ValueError("revision must be a 40-character commit SHA")
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    for source_path in PINNED_PATHS:
        url = f"{RAW_BASE}/{revision.lower()}/{source_path}"
        chunks: list[bytes] = []
        size = 0
        with opener(url, timeout=timeout) as response:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_SOURCE_BYTES:
                    raise ValueError(f"source file exceeds {MAX_SOURCE_BYTES} bytes")
                chunks.append(chunk)
        output = destination / Path(source_path).name
        output.write_bytes(b"".join(chunks))
        outputs.append(output)
    return outputs[0], outputs[1]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build Neko WoWS's immutable offline ship catalog.")
    parser.add_argument("--revision", help="Pinned 40-character wowsinfo/data commit")
    parser.add_argument("--wowsinfo-json", type=Path)
    parser.add_argument("--lang-json", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", default="local")
    parser.add_argument(
        "--source-channel",
        choices=("live",),
        help="Required provenance channel for local source files",
    )
    parser.add_argument("--minimum-ship-count", type=int, default=500)
    parser.add_argument("--language", default="zh-CN")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.revision and (args.wowsinfo_json or args.lang_json):
        parser.error("--revision cannot be combined with local source files")
    if not args.revision and not (args.wowsinfo_json and args.lang_json):
        parser.error("provide --revision or both --wowsinfo-json and --lang-json")
    if not args.revision and args.source_channel is None:
        parser.error("local source files require --source-channel live")
    source_channel = "live" if args.revision else args.source_channel

    if args.revision:
        with tempfile.TemporaryDirectory(prefix="neko-wows-catalog-") as temp_dir:
            wowsinfo_path, lang_path = download_pinned_sources(
                args.revision, Path(temp_dir))
            result = build_catalog(
                wowsinfo_path,
                lang_path,
                args.output_dir,
                source_commit=args.revision.lower(),
                source_channel=source_channel,
                minimum_ship_count=args.minimum_ship_count,
                default_language=args.language,
            )
    else:
        result = build_catalog(
            args.wowsinfo_json,
            args.lang_json,
            args.output_dir,
            source_commit=args.source_commit,
            source_channel=source_channel,
            minimum_ship_count=args.minimum_ship_count,
            default_language=args.language,
        )
    print(json.dumps({
        "database": str(result.database_path),
        "manifest": str(result.manifest_path),
        "catalog_version": result.catalog_version,
        "game_version": result.game_version,
        "content_sha256": result.content_sha256,
        "ship_count": result.ship_count,
        "profile_count": result.profile_count,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
