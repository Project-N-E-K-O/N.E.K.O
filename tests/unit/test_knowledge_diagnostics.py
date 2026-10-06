from __future__ import annotations

from knowledge.diagnostics import (
    clear_knowledge_route_diagnostics,
    list_recent_knowledge_index_batches,
    list_recent_knowledge_queries,
    list_recent_knowledge_routes,
    record_knowledge_index_batch,
    record_knowledge_query,
    record_knowledge_route,
)


def test_route_diagnostics_are_bounded_and_do_not_store_conversation_text():
    clear_knowledge_route_diagnostics()
    for index in range(25):
        record_knowledge_route(
            entry_title=f"entry-{index}",
            source_tag="source:fixture",
            match_mode="strong",
            card_delivered=True,
            result="hit",
            knowledge_hits=1,
            corpus_hits=2,
            elapsed_ms=237,
            oldest_active_age_ms=411,
            deadline_remaining_ms=52,
        )

    records = list_recent_knowledge_routes()
    assert len(records) == 20
    assert records[0]["entry_title"] == "entry-24"
    assert records[-1]["entry_title"] == "entry-5"
    assert "user_text" not in records[0]
    assert "card" not in records[0]
    assert "response" not in records[0]
    assert records[0]["knowledge_hits"] == 1
    assert records[0]["corpus_hits"] == 2
    assert records[0]["elapsed_ms"] == 237
    assert records[0]["oldest_active_age_ms"] == 411
    assert records[0]["deadline_remaining_ms"] == 52


def test_query_diagnostics_keep_counts_but_not_query_content():
    clear_knowledge_route_diagnostics()
    record_knowledge_query(
        retrieval_mode="bm25",
        embedding_service_state="not_ready",
        lexical_candidates=4,
        semantic_candidates=0,
        fallback_reason="not_ready",
        elapsed_ms=12,
    )

    record = list_recent_knowledge_queries()[0]
    assert record["lexical_candidates"] == 4
    assert record["semantic_candidates"] == 0
    assert record["fallback_reason"] == "not_ready"
    assert "query" not in record
    assert "vector" not in record


def test_index_batch_diagnostics_are_content_free():
    clear_knowledge_route_diagnostics()
    record_knowledge_index_batch(
        selected=4,
        stored=3,
        failed=0,
        stale_writebacks=1,
        elapsed_ms=16_500,
        state="slow_batch",
    )

    record = list_recent_knowledge_index_batches()[0]
    assert record["state"] == "slow_batch"
    assert record["selected"] == 4
    assert record["stored"] == 3
    assert record["stale_writebacks"] == 1
    assert "text" not in record
    assert "embedding" not in record
