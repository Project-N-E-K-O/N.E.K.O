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

"""Framework-independent proactive-chat source decisions."""

import math
import random
import time

from .state import (
    _RECENT_CHAT_MAX_AGE_SECONDS,
    _get_source_history_entry,
    _half_life_for,
    _recent_proactive_chat_entries,
    _reminiscence_usage_entries,
    _source_skip_probability,
)


def _should_skip_source(url_hash: str) -> bool:
    """Return whether source decay should suppress a stable source hash."""
    entry = _get_source_history_entry(url_hash)
    if not entry:
        return False
    age = time.time() - entry.get('ts', 0.0)
    probability = _source_skip_probability(
        age,
        _half_life_for(entry.get('kind', 'web')),
    )
    if probability >= 1.0:
        return True
    if probability <= 0.0:
        return False
    return random.random() < probability


_SOURCE_WEIGHT_DECAY_LAMBDA = 0.002
_SOURCE_WEIGHT_K = 0.30
_SOURCE_WEIGHT_FLOOR = 0.20
_SOURCE_WEIGHT_WINDOW = _RECENT_CHAT_MAX_AGE_SECONDS


def _compute_source_weights(
    lanlan_name: str,
    candidate_channels: list[str],
) -> dict[str, float]:
    """Compute normalized freshness weights for candidate source channels."""
    channel_count = len(candidate_channels)
    if channel_count == 0:
        return {}

    now = time.time()
    raw_scores: dict[str, float] = {
        channel: 0.0 for channel in candidate_channels
    }

    for timestamp, _message, channel in _recent_proactive_chat_entries(lanlan_name):
        age = now - timestamp
        if age <= _SOURCE_WEIGHT_WINDOW and channel in raw_scores:
            raw_scores[channel] += math.exp(-_SOURCE_WEIGHT_DECAY_LAMBDA * age)

    if 'reminiscence' in raw_scores:
        for timestamp in _reminiscence_usage_entries(lanlan_name):
            age = now - timestamp
            if age <= _SOURCE_WEIGHT_WINDOW:
                raw_scores['reminiscence'] += math.exp(
                    -_SOURCE_WEIGHT_DECAY_LAMBDA * age
                )

    freshness = {
        channel: 1.0 / (1.0 + _SOURCE_WEIGHT_K * raw_scores[channel])
        for channel in candidate_channels
    }
    total = sum(freshness.values())
    if total <= 0:
        return {
            channel: 1.0 / channel_count
            for channel in candidate_channels
        }
    return {
        channel: value / total
        for channel, value in freshness.items()
    }


def _filter_sources_by_weight(weights: dict[str, float]) -> set[str]:
    """Return channels whose normalized weight falls below the dynamic floor."""
    channel_count = len(weights)
    if channel_count <= 1:
        return set()
    threshold = min(_SOURCE_WEIGHT_FLOOR, 1.0 / channel_count)
    return {
        channel
        for channel, weight in weights.items()
        if weight < threshold
    }
