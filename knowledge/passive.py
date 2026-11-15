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

"""Passive selection: which cards a chat message brings in unasked.

The query tool answers a question the model chose to ask, so its bars are
loose. Passive turn context answers nobody, so a card goes in only on strong
evidence, judged per material type (knowledge and corpus each take at most
``TURN_CONTEXT_MAX_ITEMS_PER_KIND``):

1. the message names the card (title, alias or recognition phrase;
   ``named_in_text``) - always;
2. keywords and meaning agree: a top keyword candidate whose cosine clears
   the type's dual bar;
3. meaning alone: the best vector match of its type, clearing the higher bar
   and leading the next match by the calibrated margin.

A keyword match alone never qualifies. Vector bars are calibrated on the
loaded model against small talk (``utils.semantic_calibration``); until that
calibration exists only rule 1 applies.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Collection, Mapping, Sequence

import numpy as np

from config.turn_context_settings import (
    TURN_CONTEXT_CALIBRATION_BASE_QUANTILE,
    TURN_CONTEXT_CALIBRATION_BATCH,
    TURN_CONTEXT_CALIBRATION_BATCH_PAUSE_SECONDS,
    TURN_CONTEXT_CALIBRATION_HIGH_QUANTILE,
    TURN_CONTEXT_CALIBRATION_MIN_SAMPLES,
    TURN_CONTEXT_CALIBRATION_PROBES,
    TURN_CONTEXT_CORPUS_EXTRA_STEPS,
    TURN_CONTEXT_DUAL_STEPS,
    TURN_CONTEXT_KNOWLEDGE_FLOOR,
    TURN_CONTEXT_LEAD_FLOOR,
    TURN_CONTEXT_LEAD_STEPS,
    TURN_CONTEXT_MAX_ITEMS_PER_KIND,
    TURN_CONTEXT_NAME_MIN_CJK_CHARS,
    TURN_CONTEXT_NAME_MIN_LATIN_CHARS,
    TURN_CONTEXT_SEMANTIC_STEPS,
)
from utils.semantic_calibration import (
    Background,
    PassiveThresholds,
    ProbeVectors,
    background_from_scores,
    leading_take,
    passive_thresholds,
    probe_best_scores,
)

from .retrieval import SemanticMatch, named_in_text
from .store import StoredEntry, VectorSnapshot


logger = logging.getLogger(__name__)

# A keyword candidate counts as "keywords agree" only near the top of the
# BM25 order: deep in a 48-row candidate list a shared common word is noise.
PASSIVE_LEXICAL_TOP = 8
# Raw vector candidates kept per lookup, below every bar, so the lead of the
# best match over the next one is measured against real neighbours.
PASSIVE_SEMANTIC_CANDIDATES = 24
# Backgrounds kept at once (per vector snapshot, material type and pack set).
MAX_BACKGROUNDS = 16


@dataclass(frozen=True, slots=True)
class PassiveHit:
    entry_id: int
    strength: int  # 3 named, 2 keywords + meaning, 1 meaning alone
    score: float
    semantic_chunk: int | None


def thresholds_for(material_type: str, background: Background | None) -> PassiveThresholds | None:
    extra = TURN_CONTEXT_CORPUS_EXTRA_STEPS if material_type == "corpus" else 0.0
    return passive_thresholds(
        background,
        floor=TURN_CONTEXT_KNOWLEDGE_FLOOR,
        dual_steps=TURN_CONTEXT_DUAL_STEPS,
        semantic_steps=TURN_CONTEXT_SEMANTIC_STEPS,
        extra_steps=extra,
        lead_steps=TURN_CONTEXT_LEAD_STEPS,
        lead_floor=TURN_CONTEXT_LEAD_FLOOR,
    )


def select_hits(
    text: str,
    *,
    entries: Mapping[int, StoredEntry],
    usable: Collection[int],
    exact_ids: Sequence[int],
    lexical_ids: Sequence[int],
    semantic: Sequence[SemanticMatch],
    thresholds: PassiveThresholds | None,
    named_ids: Sequence[int] = (),
    limit: int = TURN_CONTEXT_MAX_ITEMS_PER_KIND,
) -> list[PassiveHit]:
    """The passive hits among one material type's candidates, strongest first.

    Every id passed must already be of that type, usable and not excluded.
    """
    chosen: dict[int, PassiveHit] = {}
    semantic_by_id = {match.entry_id: match for match in semantic if match.entry_id in usable}

    def offer(hit: PassiveHit) -> None:
        current = chosen.get(hit.entry_id)
        if current is None or (hit.strength, hit.score) > (current.strength, current.score):
            chosen[hit.entry_id] = hit

    candidate_ids = list(dict.fromkeys([*exact_ids, *named_ids, *lexical_ids, *semantic_by_id]))
    exact = set(exact_ids)
    for entry_id in candidate_ids:
        entry = entries.get(entry_id)
        if entry is None or entry_id not in usable:
            continue
        if entry_id in exact or named_in_text(
            text,
            entry,
            min_cjk_chars=TURN_CONTEXT_NAME_MIN_CJK_CHARS,
            min_latin_chars=TURN_CONTEXT_NAME_MIN_LATIN_CHARS,
        ):
            match = semantic_by_id.get(entry_id)
            offer(PassiveHit(entry_id, 3, match.score if match else 0.0, None))

    if thresholds is not None and semantic_by_id:
        lexical_top = {entry_id for entry_id in lexical_ids[:PASSIVE_LEXICAL_TOP] if entry_id in usable}
        for entry_id, match in semantic_by_id.items():
            if entry_id in lexical_top and match.score >= thresholds.dual:
                offer(PassiveHit(entry_id, 2, match.score, None))
        ordered = sorted(semantic_by_id.values(), key=lambda match: -match.score)
        take = leading_take(
            [match.score for match in ordered],
            threshold=thresholds.semantic,
            lead=thresholds.lead,
            max_take=1,
        )
        for match in ordered[:take]:
            offer(PassiveHit(match.entry_id, 1, match.score, match.chunk_index))

    ranked = sorted(chosen.values(), key=lambda hit: (-hit.strength, -hit.score))
    return ranked[:limit]


class Calibration:
    """Probe vectors per embedding model, and the backgrounds built from them.

    Nothing here blocks a lookup: a missing piece is scheduled in the
    background and the lookup goes on without vector bars this time.
    """

    def __init__(self) -> None:
        self._probes = ProbeVectors()
        self._backgrounds: dict[tuple[Any, ...], Background | None] = {}
        self._pending: set[tuple[Any, ...]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()

    def probes(self, model_id: str | None) -> np.ndarray | None:
        return self._probes.get(model_id)

    def ensure_probes(self, model_id: str | None, embed_batch: Callable[[list[str]], Awaitable[list[Any]]]) -> None:
        """Embed the probe sentences once per model, in the background."""
        self._probes.ensure(
            model_id,
            embed_batch,
            TURN_CONTEXT_CALIBRATION_PROBES,
            batch=TURN_CONTEXT_CALIBRATION_BATCH,
            pause=TURN_CONTEXT_CALIBRATION_BATCH_PAUSE_SECONDS,
            min_samples=TURN_CONTEXT_CALIBRATION_MIN_SAMPLES,
            # A new model makes every background stale.
            on_ready=self._backgrounds.clear,
        )

    def background(
        self,
        snapshot: VectorSnapshot | None,
        snapshot_key: Any,
        pack_ids: Sequence[str],
        material_type: str,
        run_in_thread: Callable[..., Awaitable[Any]],
        excluded_entry_ids: Collection[int] = frozenset(),
    ) -> Background | None:
        """The background of ``pack_ids``' vectors in ``snapshot``, or None
        (computed in the background) when not known yet.

        ``snapshot_key`` names that snapshot for good (the service's vector
        generation and model): an object id could be reused by the next
        snapshot once the old one is collected.
        """
        if snapshot is None or not pack_ids:
            return None
        probes = self._probes.get(snapshot.model_id)
        if probes is None:
            return None
        excluded = frozenset(excluded_entry_ids)
        # Disabling a card changes neither the snapshot nor its generation:
        # the excluded set is part of the key.
        key = (
            snapshot_key, snapshot.model_id, material_type, tuple(sorted(pack_ids)),
            hash(excluded),
        )
        if key in self._backgrounds:
            return self._backgrounds[key]
        if key in self._pending:
            return None
        self._pending.add(key)
        wanted = set(pack_ids)
        allowed = [index for index, pack_id in enumerate(snapshot.pack_ids) if pack_id in wanted]

        def compute() -> Background | None:
            if not allowed or probes.shape[1] != snapshot.matrix.shape[1]:
                return None
            mask = np.isin(snapshot.chunk_pack_index, np.asarray(allowed, dtype=np.int32))
            if excluded:
                mask &= ~np.isin(snapshot.entry_ids, np.fromiter(excluded, dtype=np.int64))
            if not mask.any():
                return None
            best = probe_best_scores(probes, snapshot.matrix[mask])
            return background_from_scores(
                best,
                high_quantile=TURN_CONTEXT_CALIBRATION_HIGH_QUANTILE,
                base_quantile=TURN_CONTEXT_CALIBRATION_BASE_QUANTILE,
                min_samples=TURN_CONTEXT_CALIBRATION_MIN_SAMPLES,
            )

        async def run() -> None:
            try:
                result = await run_in_thread(compute)
            except Exception as exc:
                logger.warning("[Knowledge] calibration failed: %s", type(exc).__name__)
                result = None
            finally:
                self._pending.discard(key)
            if len(self._backgrounds) >= MAX_BACKGROUNDS:
                self._backgrounds.clear()
            self._backgrounds[key] = result
            if result is not None:
                logger.info(
                    "[Knowledge] passive calibration %s/%s: high=%.3f base=%.3f n=%d",
                    snapshot.model_id, material_type, result.high, result.base, result.samples,
                )

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return None

    def cancel(self) -> None:
        self._probes.cancel()
        for task in list(self._tasks):
            task.cancel()
