"""Injected application owner; importing this module never starts a writer."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .service import TopicRecommendationService

_service: TopicRecommendationService | None = None


def configure_recommendation_service(service: TopicRecommendationService | None) -> None:
    global _service
    _service = service


def get_recommendation_service() -> TopicRecommendationService | None:
    return _service
