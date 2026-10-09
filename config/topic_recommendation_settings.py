"""Deployment defaults for the local, opt-in topic recommendation experiment."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class TopicRecommendationSettings:
    enabled: bool = False
    max_candidates: int = 3
    max_events: int = 24
    event_tokens: int = 500
    debounce_seconds: float = 10.0
    max_batch_wait_seconds: float = 60.0
    global_concurrency: int = 2
    worker_wait_seconds: float = 30.0
    batch_timeout: float = 60.0
    store_timeout: float = 10.0
    candidate_input_tokens: int = 5000
    candidate_output_tokens: int = 1200
    candidate_timeout: float = 15.0
    phase2_text_input_tokens: int = 12000
    feedback_input_tokens: int = 4000
    feedback_output_tokens: int = 700
    feedback_timeout: float = 10.0
    summary_tokens: int = 200
    max_subjects: int = 64
    max_interests: int = 128
    max_deliveries: int = 128
    max_restrictions: int = 128
    max_state_bytes: int = 2 * 1024 * 1024
    expiry_seconds: float = 7 * 86400.0
    retry_delays: tuple[float, ...] = (30.0, 60.0, 120.0)
    close_timeout: float = 10.0


def get_topic_recommendation_settings() -> TopicRecommendationSettings:
    return TopicRecommendationSettings(
        enabled=os.environ.get("NEKO_TOPIC_RECOMMENDATION_ENABLED", "").strip().lower()
        in {"1", "true", "yes"}
    )
