from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest


pytestmark = pytest.mark.unit


SEED_ROOT = (
    Path(__file__).resolve().parents[3]
    / "plugins"
    / "study_companion"
    / "static"
)


def _load_seed_topics() -> list[dict[str, Any]]:
    manifest = json.loads(
        (SEED_ROOT / "knowledge_graph_seed.json").read_text(encoding="utf-8")
    )
    topics: list[dict[str, Any]] = []
    for entry in manifest["files"]:
        payload = json.loads(
            (SEED_ROOT / entry["path"]).read_text(encoding="utf-8")
        )
        topics.extend(
            topic for topic in payload.get("topics", []) if isinstance(topic, dict)
        )
    return topics


def _broad_scope_key(topic: dict[str, Any]) -> tuple[str, str]:
    stage = str(topic.get("stage") or "").strip()
    module = (
        topic.get("course_family") if stage == "college" else topic.get("subject")
    )
    return stage, str(module or "").strip()


def _fine_scope_key(topic: dict[str, Any]) -> tuple[str, str, str, str]:
    return tuple(
        str(topic.get(field) or "").strip()
        for field in ("stage", "subject", "chapter", "unit")
    )


def _cold_start_key(topic: dict[str, Any]) -> tuple[float, float, str]:
    return (
        float(topic.get("depth") or 0.0),
        float(topic.get("difficulty") or 0.0),
        str(topic.get("id") or ""),
    )


def test_public_seed_contains_820_unique_topic_ids() -> None:
    topics = _load_seed_topics()
    topic_ids = [str(topic.get("id") or "").strip() for topic in topics]

    assert len(topics) == 820
    assert all(topic_ids)
    assert len(set(topic_ids)) == 820


def test_every_seed_topic_resolves_as_one_canonical_explicit_topic_scope() -> None:
    from plugin.plugins.study_companion.practice_scope import build_practice_scope

    topics = _load_seed_topics()
    for topic in topics:
        scope = build_practice_scope(
            {
                "mode": "explicit_topic",
                "stage": topic["stage"],
                "subject": topic["subject"],
                "course_family": topic.get("course_family") or "",
                "chapter": topic["chapter"],
                "unit": topic["unit"],
                "topic_id": topic["id"],
            },
            topics,
            revision=1,
        )
        assert scope.eligible_topic_ids == [topic["id"]]


def test_all_349_units_resolve_with_college_course_family_isolation() -> None:
    from plugin.plugins.study_companion.practice_scope import build_practice_scope

    topics = _load_seed_topics()
    requests: dict[tuple[str, ...], dict[str, str]] = {}
    for topic in topics:
        key = (
            str(topic["stage"]),
            str(topic["subject"]),
            str(topic.get("course_family") or ""),
            str(topic["chapter"]),
            str(topic["unit"]),
        )
        requests.setdefault(
            key,
            {
                "mode": "explicit_scope",
                "stage": key[0],
                "subject": key[1],
                "course_family": key[2],
                "chapter": key[3],
                "unit": key[4],
            },
        )

    assert len(requests) == 349
    for request in requests.values():
        scope = build_practice_scope(request, topics, revision=1)
        assert scope.eligible_topic_ids
        assert all(
            str(topic["id"]) in scope.eligible_topic_ids
            for topic in topics
            if all(
                not request.get(field)
                or str(topic.get(field) or "") == request[field]
                for field in ("stage", "subject", "course_family", "chapter", "unit")
            )
        )


def test_public_seed_defines_30_non_empty_broad_course_scopes() -> None:
    topics = _load_seed_topics()
    scopes = {_broad_scope_key(topic) for topic in topics}

    assert len(scopes) == 30
    assert all(stage and module for stage, module in scopes)
    assert all(
        (topic.get("course_family") if topic.get("stage") == "college" else topic.get("subject"))
        for topic in topics
    )


def test_public_seed_defines_349_non_empty_fine_unit_scopes() -> None:
    scopes = {_fine_scope_key(topic) for topic in _load_seed_topics()}

    assert len(scopes) == 349
    assert all(all(scope_part for scope_part in scope) for scope in scopes)


def test_every_scope_has_a_deterministic_cold_start_first_topic() -> None:
    topics = _load_seed_topics()
    scope_groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for topic in topics:
        scope_groups[("broad", *_broad_scope_key(topic))].append(topic)
        scope_groups[("fine", *_fine_scope_key(topic))].append(topic)

    assert len(scope_groups) == 30 + 349
    for scope, eligible_topics in scope_groups.items():
        first_topic = min(eligible_topics, key=_cold_start_key)
        assert first_topic in eligible_topics, scope
        assert str(first_topic.get("id") or "").strip(), scope
        assert first_topic == sorted(eligible_topics, key=_cold_start_key)[0], scope


def test_cross_scope_prerequisites_never_join_the_eligible_topic_set() -> None:
    topics = _load_seed_topics()
    topics_by_id = {str(topic["id"]): topic for topic in topics}
    eligible_by_scope: dict[tuple[str, str, str, str], set[str]] = defaultdict(set)
    for topic in topics:
        eligible_by_scope[_fine_scope_key(topic)].add(str(topic["id"]))

    cross_scope_edges = 0
    for topic in topics:
        topic_scope = _fine_scope_key(topic)
        eligible_topic_ids = eligible_by_scope[topic_scope]
        for prerequisite in topic.get("prerequisites") or []:
            prerequisite_id = str(
                prerequisite.get("id") if isinstance(prerequisite, dict) else prerequisite
            ).strip()
            prerequisite_topic = topics_by_id.get(prerequisite_id)
            if prerequisite_topic is None:
                continue
            if _fine_scope_key(prerequisite_topic) == topic_scope:
                continue
            cross_scope_edges += 1
            assert prerequisite_id not in eligible_topic_ids

    assert cross_scope_edges > 0


def test_real_duplicate_topic_names_are_isolated_by_topic_id_and_scope() -> None:
    topics = _load_seed_topics()
    topics_by_id = {str(topic["id"]): topic for topic in topics}
    duplicate_pairs = (
        ("senior_binomial_distribution", "college_binomial_distribution"),
        (
            "physics_senior_electromagnetic_induction_law",
            "college_physics_faraday_induction",
        ),
    )

    for first_id, second_id in duplicate_pairs:
        first = topics_by_id[first_id]
        second = topics_by_id[second_id]
        first_scope = _fine_scope_key(first)
        second_scope = _fine_scope_key(second)

        assert first["name"] == second["name"]
        assert first_id != second_id
        assert first_scope != second_scope

        first_eligible_ids = {
            str(topic["id"])
            for topic in topics
            if _fine_scope_key(topic) == first_scope
        }
        second_eligible_ids = {
            str(topic["id"])
            for topic in topics
            if _fine_scope_key(topic) == second_scope
        }
        assert first_id in first_eligible_ids
        assert second_id not in first_eligible_ids
        assert second_id in second_eligible_ids
        assert first_id not in second_eligible_ids
