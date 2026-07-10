from __future__ import annotations

from main_logic.facts_sync.sync_worker import _select_unsynced_facts


def test_select_unsynced_facts_skips_private_and_redacted_entries() -> None:
    selected = _select_unsynced_facts(
        [
            {"hash": "public-12345678", "text": "safe", "importance": 7},
            {"hash": "private-12345678", "text": "secret", "importance": 9, "private": True},
            {"hash": "redacted-12345678", "text": "[redacted]", "importance": 9, "redacted": True},
        ],
        already_synced_hashes=set(),
    )

    assert selected == [
        {
            "fact_hash": "public-12345678",
            "text": "safe",
            "importance": 0.7,
            "redacted": False,
        }
    ]


def test_select_unsynced_facts_skips_malformed_importance() -> None:
    selected = _select_unsynced_facts(
        [
            {"hash": "bad-imp-12345678", "text": "dirty", "importance": "unknown"},
            {"hash": "ok-imp-12345678", "text": "fine", "importance": 8},
        ],
        already_synced_hashes=set(),
    )

    assert [row["fact_hash"] for row in selected] == ["ok-imp-12345678"]
