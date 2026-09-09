# -*- coding: utf-8 -*-
"""Measure the real shared CAMPPlus host process count, latency, and RSS.

Run with::

    uv run --no-sync python scripts/measure_shared_campplus_host.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import math
from pathlib import Path
import sys
import time

import numpy as np
import psutil

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from main_logic.asr_client.speaker_shadow.asset_manifest import (
    CAMPPLUS_MODEL_ID,
    CAMPPLUS_MODEL_REVISION,
)
from main_logic.asr_client.speaker_shadow.campplus import CAMPPLUS_EMBEDDING_DIM
from main_logic.asr_client.speaker_shadow.shared_host import (
    SpeakerScoringLane,
    SpeakerScoringMode,
)
from main_logic.voice_identity.contracts import SpeakerModelIdentity
from main_logic.voice_identity.profile import SpeakerProfile
from main_logic.voice_identity.reference import SpeakerReference
from main_logic.voice_identity_service.shared_campplus_host import (
    SharedCampPlusScoringHost,
)

_MIB = 1024 * 1024
_MEMORY_LIMIT_MIB = 250.0
_SAMPLE_RATE_HZ = 16_000


def _process_tree_rss_bytes() -> int:
    process = psutil.Process()
    total = process.memory_info().rss
    for child in process.children(recursive=True):
        try:
            total += child.memory_info().rss
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue
    return total


def _sine_pcm(duration_seconds: float) -> bytes:
    sample_count = int(_SAMPLE_RATE_HZ * duration_seconds)
    timeline = np.arange(sample_count, dtype=np.float32) / _SAMPLE_RATE_HZ
    samples = np.sin(2.0 * math.pi * 220.0 * timeline) * 8_000.0
    return samples.astype("<i2").tobytes()


def _synthetic_profile() -> SpeakerProfile:
    identity = SpeakerModelIdentity(
        CAMPPLUS_MODEL_ID,
        CAMPPLUS_MODEL_REVISION,
        CAMPPLUS_EMBEDDING_DIM,
    )
    embedding = np.zeros(CAMPPLUS_EMBEDDING_DIM, dtype=np.float32)
    embedding[0] = 1.0
    reference = SpeakerReference(identity, embedding)
    try:
        return SpeakerProfile("memory-benchmark-profile", reference)
    finally:
        reference.close()
        embedding.fill(0.0)


async def _measure() -> dict[str, float | int | bool]:
    baseline_bytes = _process_tree_rss_bytes()
    peak_bytes = baseline_bytes
    max_physical_hosts = 0
    stop_sampling = asyncio.Event()
    owner = SharedCampPlusScoringHost()
    profile = _synthetic_profile()

    async def sample_resources() -> None:
        nonlocal peak_bytes, max_physical_hosts
        while not stop_sampling.is_set():
            peak_bytes = max(peak_bytes, _process_tree_rss_bytes())
            max_physical_hosts = max(
                max_physical_hosts,
                owner.snapshot()["physical_host_count"],
            )
            await asyncio.sleep(0.01)

    sampler = asyncio.create_task(sample_resources())
    started_at = time.perf_counter()
    try:
        generation = await owner.activate(
            profile,
            "memory-benchmark-config",
            absolute_deadline=asyncio.get_running_loop().time() + 30.0,
        )
        loaded_at = time.perf_counter()
        prewire = owner.lease(
            lane=SpeakerScoringLane.PREWIRE,
            mode=SpeakerScoringMode.SHORT_PROBE,
            timeout_seconds=10.0,
        )
        shadow = owner.lease(
            lane=SpeakerScoringLane.SHADOW,
            mode=SpeakerScoringMode.STANDARD,
            timeout_seconds=10.0,
        )
        short_pcm = _sine_pcm(0.5)
        standard_pcm = _sine_pcm(1.6)
        score_started_at = time.perf_counter()
        short_score, standard_score = await asyncio.gather(
            prewire.score_async(short_pcm, _SAMPLE_RATE_HZ),
            shadow.score_async(standard_pcm, _SAMPLE_RATE_HZ),
        )
        scored_at = time.perf_counter()
        reload_started_at = time.perf_counter()
        reloaded_generation = await owner.activate(
            profile,
            "memory-benchmark-config-reloaded",
            absolute_deadline=asyncio.get_running_loop().time() + 30.0,
        )
        reloaded_at = time.perf_counter()
        if prewire.alive or shadow.alive or reloaded_generation == generation:
            raise RuntimeError("reload did not invalidate the old leases")
        deactivate_started_at = time.perf_counter()
        if not await owner.deactivate(
            absolute_deadline=asyncio.get_running_loop().time() + 30.0
        ):
            raise RuntimeError("shared host deactivation failed")
        deactivated_at = time.perf_counter()
        if owner.snapshot()["physical_host_count"] != 0:
            raise RuntimeError("physical host survived deactivation")
        reactivation_started_at = time.perf_counter()
        reactivated_generation = await owner.activate(
            profile,
            "memory-benchmark-config-reactivated",
            absolute_deadline=asyncio.get_running_loop().time() + 30.0,
        )
        reactivated_at = time.perf_counter()
        final_lease = owner.lease(
            lane=SpeakerScoringLane.SHADOW,
            mode=SpeakerScoringMode.STANDARD,
            timeout_seconds=10.0,
        )
        final_score = await final_lease.score_async(standard_pcm, _SAMPLE_RATE_HZ)
        max_physical_hosts = max(
            max_physical_hosts,
            owner.snapshot()["physical_host_count"],
        )
        if reactivated_generation != owner.generation:
            raise RuntimeError("reactivated generation is not current")
    finally:
        await owner.close()
        profile.close()
        stop_sampling.set()
        await sampler
        peak_bytes = max(peak_bytes, _process_tree_rss_bytes())

    result: dict[str, float | int | bool] = {
        "baseline_tree_rss_mib": round(baseline_bytes / _MIB, 2),
        "peak_tree_rss_mib": round(peak_bytes / _MIB, 2),
        "incremental_peak_rss_mib": round((peak_bytes - baseline_bytes) / _MIB, 2),
        "load_ms": round((loaded_at - started_at) * 1000.0, 2),
        "two_scores_ms": round((scored_at - score_started_at) * 1000.0, 2),
        "reload_ms": round((reloaded_at - reload_started_at) * 1000.0, 2),
        "deactivate_ms": round((deactivated_at - deactivate_started_at) * 1000.0, 2),
        "reactivate_ms": round((reactivated_at - reactivation_started_at) * 1000.0, 2),
        "short_score": round(short_score, 6),
        "standard_score": round(standard_score, 6),
        "score_after_reactivate": round(final_score, 6),
        "max_physical_host_count": max_physical_hosts,
        "physical_host_count_after_close": owner.snapshot()["physical_host_count"],
    }
    result["within_memory_limit"] = bool(
        result["incremental_peak_rss_mib"] <= _MEMORY_LIMIT_MIB
    )
    result["single_host_only"] = bool(
        max_physical_hosts == 1 and result["physical_host_count_after_close"] == 0
    )
    return result


async def _main() -> int:
    result = await _measure()
    print(json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if result["within_memory_limit"] and result["single_host_only"] else 1


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        raise SystemExit(asyncio.run(_main()))
