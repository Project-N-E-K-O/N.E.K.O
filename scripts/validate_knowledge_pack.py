#!/usr/bin/env python3
"""Validate a data-only N.E.K.O. knowledge pack."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable


REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from knowledge.packs import canonical_pack_bytes, load_pack  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate a .neko-knowledge.json data pack.",
    )
    parser.add_argument("pack", type=Path, help="knowledge pack to validate")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="also require canonical UTF-8 JSON bytes for publishing",
    )
    return parser


def _issues(error: Exception) -> Iterable[tuple[str, str, str]]:
    structured = getattr(error, "issues", ())
    if structured:
        for issue in structured:
            severity = str(getattr(issue, "severity", "error")).upper()
            path = str(getattr(issue, "path", "knowledge pack"))
            message = str(getattr(issue, "message", "validation failed"))
            yield severity, path, message
        return
    yield "ERROR", "knowledge pack", str(error) or "validation failed"


def _read_payload(path: Path) -> tuple[bytes, object]:
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("knowledge pack is not valid UTF-8") from exc
    try:
        return raw, json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("knowledge pack is not valid JSON") from exc


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        pack = load_pack(args.pack)
    except OSError as exc:
        print(f"[FAIL] file: {type(exc).__name__}", file=sys.stderr)
        return 1
    except ValueError as exc:
        for severity, path, message in _issues(exc):
            label = "WARN" if severity == "WARNING" else "FAIL"
            print(f"[{label}] {path}: {message}", file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"[FAIL] internal: {type(exc).__name__}", file=sys.stderr)
        return 1

    print(f"[PASS] pack_id: {pack.pack_id}")
    print(f"[PASS] collection_id: {pack.collection_id}")
    print(f"[PASS] entries: {len(pack.entries)}")

    if args.strict:
        try:
            raw, payload = _read_payload(args.pack)
        except OSError as exc:  # pragma: no cover - already read by load_pack
            print(f"[FAIL] file: {type(exc).__name__}", file=sys.stderr)
            return 1
        except ValueError as exc:  # pragma: no cover - already parsed by load_pack
            print(f"[FAIL] knowledge pack: {exc}", file=sys.stderr)
            return 2
        warnings = False
        if not pack.source.homepage:
            warnings = True
            print(
                "[WARN] source.homepage: recommended for published packs",
                file=sys.stderr,
            )
        if raw != canonical_pack_bytes(payload):
            warnings = True
            print(
                "[WARN] canonical_json: publish the canonical UTF-8 JSON form",
                file=sys.stderr,
            )
        else:
            print("[PASS] canonical_json: reproducible UTF-8 bytes")
        if warnings:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
