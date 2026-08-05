from __future__ import annotations

from knowledge.diagnostics import (
    clear_knowledge_route_diagnostics,
    list_recent_knowledge_routes,
    record_knowledge_route,
)


def test_route_diagnostics_are_bounded_and_do_not_store_conversation_text():
    clear_knowledge_route_diagnostics()
    assert list_recent_knowledge_routes() == ()

    for index in range(25):
        record_knowledge_route(
            collection_id="reference",
            entry_title=f"entry-{index}",
            source_tag="source:fixture",
            match_mode="strong",
            card_delivered=True,
            result="hit",
        )

    records = list_recent_knowledge_routes()
    assert len(records) == 20
    assert records[0]["entry_title"] == "entry-24"
    assert records[-1]["entry_title"] == "entry-5"
    assert "user_text" not in records[0]
    assert "card" not in records[0]
    assert "response" not in records[0]

    clear_knowledge_route_diagnostics()
