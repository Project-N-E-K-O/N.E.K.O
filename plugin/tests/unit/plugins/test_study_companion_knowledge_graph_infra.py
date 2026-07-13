from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins.study_companion.entry_tutor_context_support import (
    _TutorContextSupportMixin,
)
from plugin.plugins.study_companion._graph_utils import topic_id, topic_label
from plugin.plugins.study_companion.knowledge_graph_guidance import (
    build_knowledge_guidance_payload,
)
from plugin.plugins.study_companion.knowledge_seed_validator import (
    validate_knowledge_seed_manifest,
)

pytestmark = pytest.mark.unit


def test_bundled_legacy_seed_validates_with_quality_gaps() -> None:
    seed = (
        Path(__file__).resolve().parents[3]
        / "plugins"
        / "study_companion"
        / "static"
        / "knowledge_graph_seed.json"
    )

    result = validate_knowledge_seed_manifest(seed)

    assert result.is_valid
    assert len(result.topics) == 457
    assert result.report["schema_ready_topics"] == len(result.topics)


def test_compact_confusion_labels_use_related_topic_label() -> None:
    payload = build_knowledge_guidance_payload(
        topics=[
            {
                "id": "focus",
                "name": "Focus Topic",
                "subject": "math",
                "stage": "junior_high",
                "chapter": "chapter",
                "unit": "unit",
                "prerequisites": [],
                "related": [{"id": "other", "relation": "confusable"}],
            },
            {
                "id": "other",
                "name": "Other Topic",
                "subject": "math",
                "stage": "junior_high",
                "chapter": "chapter",
                "unit": "unit",
                "prerequisites": [],
                "related": [],
            },
        ],
        topic_id="focus",
    )

    assert payload["model_context"]["confusions"] == ["Other Topic"]


def test_graph_topic_helpers_skip_blank_candidates() -> None:
    assert topic_id({"id": "   ", "topic_id": "fallback_id"}) == "fallback_id"
    assert (
        topic_label(
            {"name": "   ", "label": "", "topic_id": "topic_key"},
            fallback="Fallback Label",
        )
        == "topic_key"
    )
    assert topic_label(None, fallback="  Fallback Label  ") == "Fallback Label"


def test_knowledge_guidance_cache_can_be_invalidated() -> None:
    class Host(_TutorContextSupportMixin):
        pass

    host = Host()
    host._knowledge_guidance_topics_cache = {"all:5000": [{"id": "stale"}]}

    host._invalidate_knowledge_guidance_cache()

    assert host._knowledge_guidance_topics_cache == {}
