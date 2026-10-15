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

"""Similarity thresholds calibrated against small talk, per embedding model.

A cosine score means nothing on its own: the local embedding model changes
with the machine (its Matryoshka width follows installed RAM), and the scale
of its scores changes with it. Passive retrieval therefore never uses a fixed
cosine. It measures, on the model actually loaded, what unrelated small talk
scores against the material (``TURN_CONTEXT_CALIBRATION_PROBES``): for every
probe sentence its best score over the material, then the high quantile and
the median of those bests. A passive hit must clear that background by a
margin counted in its spread (high quantile minus median), and never fall
below the matching tool's own threshold.

Pure numpy, plus ``embed_probes`` which only paces calls to an embedder it
is handed; no model, file or network access of its own.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class Background:
    """How well small talk matches a body of material."""

    high: float
    base: float
    samples: int

    @property
    def spread(self) -> float:
        return max(self.high - self.base, 0.0)


@dataclass(frozen=True, slots=True)
class PassiveThresholds:
    """Bars for one kind of material: both signals, vector alone, and the
    lead the last accepted vector-only hit must keep over the next one."""

    dual: float
    semantic: float
    lead: float


async def embed_probes(
    embed_batch: Callable[[list[str]], Awaitable[list[Any]]],
    texts: Sequence[str],
    *,
    batch: int,
    pause: float,
) -> list[Any]:
    """Embed ``texts`` in small batches with a pause between them, so the
    probes never hold a shared model for long; one vector (or None) per text."""
    vectors: list[Any] = []
    step = max(int(batch), 1)
    for start in range(0, len(texts), step):
        if start:
            await asyncio.sleep(pause)
        chunk = list(texts[start:start + step])
        result = await embed_batch(chunk)
        result = list(result or [])
        vectors.extend(result[:len(chunk)] + [None] * (len(chunk) - len(result)))
    return vectors


def probe_best_scores(
    probes: np.ndarray, material: np.ndarray, *, block_rows: int = 4096
) -> np.ndarray:
    """Each probe's best cosine over ``material`` (rows are unit vectors).

    ``material`` is scanned in row blocks so a large body never needs a full
    probes-by-material matrix in memory at once.
    """
    if probes.size == 0 or material.size == 0:
        return np.empty(0, dtype=np.float32)
    if probes.shape[1] != material.shape[1]:
        return np.empty(0, dtype=np.float32)
    best = np.full(probes.shape[0], -np.inf, dtype=np.float32)
    for start in range(0, material.shape[0], block_rows):
        block = material[start:start + block_rows]
        np.maximum(best, (block @ probes.T).max(axis=0), out=best)
    return best


def background_from_scores(
    best_scores: Sequence[float] | np.ndarray,
    *,
    high_quantile: float,
    base_quantile: float,
    min_samples: int,
) -> Background | None:
    """The background of a set of per-probe best scores; None when there are
    too few to trust."""
    scores = np.asarray(best_scores, dtype=np.float64)
    scores = scores[np.isfinite(scores)]
    if scores.size < max(int(min_samples), 1):
        return None
    return Background(
        high=float(np.quantile(scores, high_quantile)),
        base=float(np.quantile(scores, base_quantile)),
        samples=int(scores.size),
    )


def passive_thresholds(
    background: Background | None,
    *,
    floor: float,
    dual_steps: float,
    semantic_steps: float,
    extra_steps: float = 0.0,
    lead_steps: float,
    lead_floor: float,
) -> PassiveThresholds | None:
    """The bars for one kind of material, or None without a background.

    Without a calibration the vector signals are not trusted at all: the
    caller falls back to name and keyword evidence alone.
    """
    if background is None:
        return None
    spread = background.spread
    return PassiveThresholds(
        dual=max(floor, background.high + (dual_steps + extra_steps) * spread),
        semantic=max(floor, background.high + (semantic_steps + extra_steps) * spread),
        lead=max(lead_floor, lead_steps * spread),
    )


def leading_take(scores: Sequence[float], *, threshold: float, lead: float, max_take: int) -> int:
    """How many of the best vector-only ``scores`` (sorted best first) to accept.

    At most ``max_take`` from the top, each at or above ``threshold``, and the
    last one taken must lead the next score (if any) by at least ``lead``; when
    it does not, one fewer is tried.
    """
    ordered = list(scores)
    take = min(max(int(max_take), 0), len(ordered))
    while take > 0 and ordered[take - 1] < threshold:
        take -= 1
    while take > 0:
        following = ordered[take] if take < len(ordered) else None
        if following is None or ordered[take - 1] - following >= lead:
            return take
        take -= 1
    return 0
