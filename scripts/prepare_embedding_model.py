# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Download local embedding model assets for development and packaging.

The runtime uses an anonymous profile id (for example
``local-text-retrieval-v1``). This script maps a concrete model repository
onto that profile folder at build time, so source, PyInstaller and Nuitka
builds all share the same on-disk layout.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


DEFAULT_PROFILE_ID = "local-text-retrieval-v1"
DEFAULT_OUTPUT_ROOT = Path("data") / "embedding_models"
PREPARED_MARKER = ".prepared.json"

# Download resilience. huggingface.co rate-limits anonymous requests per source
# IP by the hour, and CI runs from shared runner / proxy egress IPs that are
# chronically throttled, so a direct fetch routinely hit HTTP 429 and killed the
# whole build with no recovery. Two defenses stack here:
#   1. Mirror fallback: each file is tried against every endpoint in order
#      (huggingface.co first, then the hf-mirror.com reverse proxy). A source
#      that 429s or is unreachable falls through to the next instead of failing
#      the build. Override the list/order via HF_ENDPOINTS (comma-separated) or a
#      single HF_ENDPOINT (the huggingface_hub convention).
#   2. Per-endpoint backoff: within one endpoint, retry transient failures
#      (429 / 5xx / connection errors) with exponential backoff, honoring a
#      numeric Retry-After when the server sends one.
# A bounded ~30s backoff alone can't outwait an hourly per-IP limit; the mirror
# fallback is what actually breaks the deadlock when the runner IP is throttled.
_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 2.0
_BACKOFF_CAP_SECONDS = 60.0
# Mirror endpoints tried in order. hf-mirror.com mirrors the full
# /{repo}/resolve/{revision}/{file} layout and is not under the same per-IP
# throttle, so it recovers builds when huggingface.co rate-limits the shared CI
# egress. Kept as a plain default so neither Dockerfile nor the desktop
# workflows need to pass anything.
DEFAULT_ENDPOINTS = ("https://huggingface.co", "https://hf-mirror.com")
# Some CDNs reject the default "Python-urllib/x.y" agent; send a stable one.
_USER_AGENT = "neko-embedding-prepare/1.0 (+https://github.com/Project-N-E-K-O/N.E.K.O)"
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


def _retry_after_seconds(exc: urllib.error.HTTPError) -> float | None:
    """Return the Retry-After delay in seconds, if the server sent a numeric one.

    Only the delta-seconds form is honored. The HTTP-date form is valid per spec
    but rare from huggingface.co; rather than parse dates we fall back to plain
    exponential backoff for it.
    """
    headers = getattr(exc, "headers", None)
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    raw = raw.strip()
    if raw.isdigit():
        return float(raw)
    return None


def _backoff_seconds(attempt: int) -> float:
    return min(_BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)), _BACKOFF_CAP_SECONDS)


def _endpoints() -> list[str]:
    """Ordered HF-compatible base URLs to try for each file.

    ``HF_ENDPOINTS`` (comma-separated) takes precedence and fully replaces the
    default order; a single ``HF_ENDPOINT`` (the huggingface_hub convention) is
    honored next and pins to that one mirror. Otherwise the built-in
    huggingface.co -> hf-mirror.com fallback is used.
    """
    raw = os.environ.get("HF_ENDPOINTS") or os.environ.get("HF_ENDPOINT")
    if raw:
        eps = [item.strip().rstrip("/") for item in raw.split(",") if item.strip()]
        if eps:
            return eps
    return list(DEFAULT_ENDPOINTS)


def _download_one(url: str, dest: Path) -> None:
    """Fetch one URL into ``dest`` with bounded exponential-backoff retry.

    Raises ``RuntimeError`` when retries are exhausted or the server returns a
    non-retryable status (e.g. 404), so the caller can fall back to the next
    mirror.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})

    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                with tmp.open("wb") as f:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
            os.replace(tmp, dest)
            print(f"[embedding-model] wrote {dest} ({dest.stat().st_size} bytes)")
            return
        except urllib.error.HTTPError as exc:
            # HTTPError is a subclass of URLError, so this branch must precede it.
            if tmp.exists():
                tmp.unlink()
            retryable = exc.code in _RETRYABLE_STATUS
            if not retryable or attempt == _MAX_ATTEMPTS:
                raise RuntimeError(f"failed to download {url}: {exc}") from exc
            # Cap Retry-After too: a server-sent `Retry-After: 3600` would
            # otherwise sleep past the job timeout instead of failing within
            # the bounded backoff window.
            delay = min(_retry_after_seconds(exc) or _backoff_seconds(attempt), _BACKOFF_CAP_SECONDS)
            reason = f"HTTP {exc.code}"
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            ConnectionError,
            http.client.IncompleteRead,
        ) as exc:
            # Transient network failures. urlopen()'s timeout only covers the
            # connect phase and wraps connect errors in URLError, but a stall
            # during response.read() of a large ONNX file surfaces as
            # socket.timeout / TimeoutError / IncompleteRead — none of which
            # subclass URLError, so they must be caught explicitly or they would
            # abort the build on the first attempt instead of retrying.
            if tmp.exists():
                tmp.unlink()
            if attempt == _MAX_ATTEMPTS:
                raise RuntimeError(f"failed to download {url}: {exc}") from exc
            delay = _backoff_seconds(attempt)
            reason = str(getattr(exc, "reason", exc)) or exc.__class__.__name__

        print(
            f"[embedding-model] attempt {attempt}/{_MAX_ATTEMPTS} for {url} "
            f"failed ({reason}); retrying in {delay:.0f}s"
        )
        time.sleep(delay)


def _download(
    rel: str,
    dest: Path,
    *,
    repo: str,
    revision: str,
    endpoints: list[str],
    force: bool,
) -> None:
    """Download one repo file into ``dest``, trying each mirror in order.

    Each endpoint gets its own bounded backoff retry; a source that exhausts its
    retries (e.g. a persistent 429) or returns a non-retryable status falls
    through to the next mirror. Only when every endpoint fails does this raise.
    """
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"[embedding-model] keep existing {dest}")
        return

    failures: list[str] = []
    for index, base in enumerate(endpoints, 1):
        url = f"{base}/{repo}/resolve/{revision}/{rel}"
        suffix = f" (source {index}/{len(endpoints)})" if len(endpoints) > 1 else ""
        print(f"[embedding-model] download {url}{suffix}")
        try:
            _download_one(url, dest)
            return
        except RuntimeError as exc:
            failures.append(str(exc))
            if index < len(endpoints):
                print(f"[embedding-model] source {base} failed; falling back to next mirror")

    raise RuntimeError(
        f"failed to download {rel} from all {len(endpoints)} source(s): "
        + " | ".join(failures)
    )


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

    # Existing non-empty weights with no .prepared.json marker can't be trusted
    # to match the pin: they may be a developer's runtime-downloaded copy that
    # rode into the Docker build context (.dockerignore no longer excludes
    # data/embedding_models). Otherwise _download would keep them and the marker
    # written below would bless them as the pinned revision, silently packaging
    # the wrong model. Re-fetch instead. CI's prepare step always writes the
    # marker, so the cache-hit path stays fully offline.
    unmarked_existing = existing is None and any(
        (profile_dir / rel).exists() and (profile_dir / rel).stat().st_size > 0
        for rel in files
    )
    if unmarked_existing:
        print(
            "[embedding-model] existing weights present without a .prepared.json "
            f"marker; re-downloading pinned {args.repo}@{args.revision} rather "
            "than trusting unverified files",
        )

    force = args.force or revision_changed or unmarked_existing
    endpoints = _endpoints()
    for rel in files:
        _download(
            rel,
            profile_dir / rel,
            repo=args.repo,
            revision=args.revision,
            endpoints=endpoints,
            force=force,
        )
    _verify(profile_dir, files)
    _write_marker(profile_dir, args.repo, args.revision)
    print(f"[embedding-model] profile ready: {profile_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
