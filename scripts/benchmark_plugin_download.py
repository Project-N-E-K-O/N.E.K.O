"""Compare one route with the full ranked route list in the package downloader.

Run with ``uv run --no-sync python scripts/benchmark_plugin_download.py``.
Synthetic HTTP routes model slow, failing and dropping sources without
starting a service. ``--plugin-id 40 --version 1.0.3`` uses a public Market
package instead. Results are measurements, never a throughput pass/fail gate.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
from pathlib import Path
import re
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402

from plugin.server.infrastructure import package_sources  # noqa: E402
from plugin.server.infrastructure.package_download import aclose_package_http_client  # noqa: E402
from plugin.server.infrastructure.package_sources import DownloadPreferences, download_candidates  # noqa: E402
from plugin.server.infrastructure.package_transfer import download_package  # noqa: E402

SCENARIOS = ("fast_primary", "slow_headers", "unavailable", "mid_stream_drop", "slow_body")
RATE = 4 * 1024 * 1024


class Network:
    """Two synthetic routes: ``first.test`` misbehaves per scenario, ``second.test`` is healthy."""

    def __init__(self, body: bytes, scenario: str):
        self.body = body
        self.scenario = scenario
        self.requests = 0
        self.dropped = False

    async def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        first = request.url.host == "first.test"
        if first and self.scenario == "unavailable":
            return httpx.Response(503)
        await asyncio.sleep(1.0 if first and self.scenario == "slow_headers" else 0.05)
        match = re.fullmatch(r"bytes=(\d+)-", request.headers.get("range", ""))
        start = int(match[1]) if match else 0
        rate = 64 * 1024 if first and self.scenario == "slow_body" else RATE
        # The first route drops its connection once, halfway through.
        drop_at = len(self.body) // 2 if first and self.scenario == "mid_stream_drop" and not self.dropped else None
        network = self

        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                began = time.monotonic()
                for offset in range(start, len(network.body), 65536):
                    if drop_at is not None and offset >= drop_at:
                        network.dropped = True
                        raise httpx.ReadError("connection dropped")
                    delay = began + (offset - start) / rate - time.monotonic()
                    if delay > 0:
                        await asyncio.sleep(delay)
                    yield network.body[offset:offset + 65536]

        headers = {"content-length": str(len(self.body) - start)}
        if match:
            headers["content-range"] = f"bytes {start}-{len(self.body) - 1}/{len(self.body)}"
            return httpx.Response(206, headers=headers, stream=Body())
        return httpx.Response(200, headers=headers, stream=Body())


async def measure(directory: Path, routes, expected: str, size: int) -> dict:
    milestones: dict[str, float] = {}
    used: list[str] = []
    started = time.perf_counter()

    def progress(received, total):
        if received:
            milestones.setdefault("first_data", time.perf_counter())

    path = await download_package(
        routes, directory, maximum_bytes=500 * 1024 * 1024, total_timeout=180,
        report_progress=progress, report_route=used.append, check_cancelled=lambda: None,
    )
    downloaded_at = time.perf_counter()

    def verify_and_remove():
        try:
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            if digest != expected:
                raise ValueError("Downloaded bytes do not match the expected package SHA256")
        finally:
            path.unlink(missing_ok=True)

    await asyncio.to_thread(verify_and_remove)
    return {
        "routes": len(routes), "bytes": size, "used_routes": used,
        "download_seconds": round(downloaded_at - started, 4),
        "first_data_seconds": round(milestones.get("first_data", downloaded_at) - started, 4),
    }


async def public_package(args) -> tuple[list[tuple[str, str]], str, int, str]:
    async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
        response = await client.get(
            f"https://market.project-neko.cn/api/v1/plugins/{args.plugin_id}/versions",
            params={"channel": "stable", "include_yanked": "false"},
        )
        response.raise_for_status()
        release = next(row for row in response.json() if row.get("package_url")
                       and not row.get("yanked_at") and (not args.version or row["version"] == args.version))
        candidates = download_candidates(release["package_url"], DownloadPreferences())
        for _, url in candidates:
            async with client.stream("GET", url, headers={"Range": "bytes=0-0", "Accept-Encoding": "identity"}) as metadata:
                match = re.fullmatch(r"bytes 0-0/(\d+)", metadata.headers.get("content-range", ""))
                if metadata.status_code == 206 and match:
                    return candidates, release["package_sha256"], int(match[1]), release["package_url"]
    raise ValueError("Benchmark package does not provide usable size metadata")


async def benchmark(args, directory: Path) -> dict:
    logging.getLogger("httpx").setLevel(logging.WARNING)
    report: dict = {"results": []}
    # ABBA: single route, then every route, then the reverse, per repeat block.
    modes = ["single", "ranked", "ranked", "single"] * args.repeats
    if args.plugin_id:
        candidates, expected, size, report["package_url"] = await public_package(args)
        for mode in modes:
            # Cold every run: no pooled connections, no learned routes.
            await package_sources.reset_transfer_history()
            try:
                result = await measure(directory, candidates[:1] if mode == "single" else candidates, expected, size)
            finally:
                await aclose_package_http_client()
            result.update(scenario="public_market", mode=mode)
            report["results"].append(result)
            print(json.dumps(result), flush=True)
        return report
    body = await asyncio.to_thread(lambda: hashlib.shake_256(b"neko-download-benchmark").digest(args.mib * 1024 * 1024))
    expected = await asyncio.to_thread(lambda: hashlib.sha256(body).hexdigest())
    original = httpx.AsyncClient
    for scenario in args.scenario or SCENARIOS:
        for mode in modes:
            network = Network(body, scenario)
            module = SimpleNamespace(
                AsyncClient=lambda **kwargs: original(transport=httpx.MockTransport(network.respond), **kwargs),
                Timeout=httpx.Timeout, Limits=httpx.Limits, URL=httpx.URL, HTTPError=httpx.HTTPError,
            )
            # The baseline is the healthy route alone, not an artificially bad one.
            routes = [("second", "https://second.test/package")]
            if mode == "ranked":
                routes.insert(0, ("first", "https://first.test/package"))
            await package_sources.reset_transfer_history()
            with patch("plugin.server.infrastructure.package_download.ensure_httpx", AsyncMock(return_value=module)), \
                    patch("plugin.server.infrastructure.package_transfer.ensure_httpx", AsyncMock(return_value=module)):
                try:
                    result = await measure(directory, routes, expected, len(body))
                finally:
                    await aclose_package_http_client()
            result.update(scenario=scenario, mode=mode, requests=network.requests)
            report["results"].append(result)
            print(json.dumps(result), flush=True)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plugin-id", type=int)
    parser.add_argument("--version")
    parser.add_argument("--mib", type=int, default=16)
    parser.add_argument("--repeats", type=int, default=1, help="Number of ABBA blocks")
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.mib <= 500 or not 1 <= args.repeats <= 10:
        parser.error("Use 1..500 MiB and 1..10 repeat blocks")
    with tempfile.TemporaryDirectory(prefix="neko-download-benchmark-") as temporary:
        # Learned routes stay in the scratch directory, never the user's state.
        stats = Path(temporary) / "routes.json"
        with patch.object(package_sources, "route_stats_path", lambda: stats):
            report = asyncio.run(benchmark(args, Path(temporary)))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
