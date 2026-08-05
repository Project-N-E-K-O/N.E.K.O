from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

import knowledge.service as service_module
from knowledge.api import (
    KnowledgePackValidationError,
    open_knowledge,
    validate_pack,
)
from knowledge.collection_specs import CollectionSpec


def _pack_payload(
    *,
    pack_id: str = "community-pack",
    collection_id: str = "community-demo",
    display_name: str = "Community Demo",
    title: str = "Known Alpha",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "collection_id": collection_id,
        "collection": {"display_name": display_name},
        "source": {
            "name": "Community Fixture",
            "homepage": "https://example.invalid/knowledge",
            "license": "CC0-1.0",
        },
        "entries": [
            {
                "title": title,
                "terms": {"alias": [], "recognition": []},
                "tags": ["topic:test"],
                "summary": f"Summary for {title}",
                "content": f"Content for {title}",
            }
        ],
    }


def _collection(service: object, collection_id: str) -> dict | None:
    return next(
        (
            item
            for item in service.list_collections()  # type: ignore[attr-defined]
            if item["collection_id"] == collection_id
        ),
        None,
    )


def _registry_record(root: Path, collection_id: str) -> dict[str, object] | None:
    path = root / "collections.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    collections = payload.get("collections", {})
    assert isinstance(collections, dict)
    record = collections.get(collection_id)
    assert record is None or isinstance(record, dict)
    return record


def _install(
    root: Path,
    *,
    pack_id: str = "community-pack",
    collection_id: str = "community-demo",
    display_name: str = "Community Demo",
    title: str = "Known Alpha",
):
    service = open_knowledge(root)
    pack = validate_pack(
        _pack_payload(
            pack_id=pack_id,
            collection_id=collection_id,
            display_name=display_name,
            title=title,
        )
    )
    service.install_pack(pack)
    return service


def test_unknown_collection_manifest_creates_isolated_database_and_registry(
    tmp_path: Path,
) -> None:
    service = _install(tmp_path)

    database_path = tmp_path / "community" / "community-demo" / "knowledge.db"
    assert database_path.is_file()
    assert (tmp_path / "collections.json").is_file()
    assert _registry_record(tmp_path, "community-demo") == {
        "display_name": "Community Demo",
        "storage_directory": "community/community-demo",
        "created_by_pack": "community-pack",
        "status": "active",
    }
    record = _collection(service, "community-demo")
    assert record is not None
    assert record["name"] == "Community Demo"
    assert [hit.entry.title for hit in service.search("community-demo", "Known Alpha")] == [
        "Known Alpha"
    ]


def test_community_collection_is_restored_after_restart(tmp_path: Path) -> None:
    _install(tmp_path)

    restarted = open_knowledge(tmp_path)

    record = _collection(restarted, "community-demo")
    assert record is not None
    assert record["status"] == "ready"
    assert [hit.entry.title for hit in restarted.search("community-demo", "Known Alpha")] == [
        "Known Alpha"
    ]


def test_damaged_collection_registry_degrades_to_no_community_collections(
    tmp_path: Path,
) -> None:
    (tmp_path / "collections.json").write_text("[]", encoding="utf-8")

    service = open_knowledge(tmp_path)

    assert service.list_collections() == ()


def test_newer_collection_registry_opens_without_community_collections(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "collections.json"
    original = '{"schema_version":2,"collections":{"future":{}}}'
    registry.write_text(original, encoding="utf-8")

    service = open_knowledge(tmp_path)

    # The newer registry is never silently overwritten, and the service still
    # opens with no community collections instead of failing entirely.
    assert service.list_collections() == ()
    assert registry.read_text(encoding="utf-8") == original


@pytest.mark.parametrize(
    "collection_id",
    (
        "../escape",
        "safe/escape",
        r"safe\escape",
        ".hidden",
        "trailing-",
        "CON",
        "nul",
        "Aux",
        "com1",
        "LPT9",
    ),
)
def test_unsafe_or_reserved_collection_ids_are_rejected(
    tmp_path: Path,
    collection_id: str,
) -> None:
    payload = _pack_payload(collection_id=collection_id)

    with pytest.raises(KnowledgePackValidationError) as exc_info:
        validate_pack(payload)

    assert any(issue.path == "collection_id" for issue in exc_info.value.issues)
    assert not (tmp_path / "community").exists()


def test_builtin_collection_wins_and_marks_existing_community_conflict(
    tmp_path: Path,
) -> None:
    community = _install(
        tmp_path,
        collection_id="shared",
        display_name="Community Shared",
        title="Community-only entry",
    )
    community_database = tmp_path / "community" / "shared" / "knowledge.db"
    assert community_database.is_file()
    assert _collection(community, "shared") is not None
    builtin = CollectionSpec(
        collection_id="shared",
        display_name="Trusted Shared",
        storage_directory="trusted-shared",
        priority=100,
        auto_context_enabled=True,
    )

    restarted = open_knowledge(tmp_path, collections=(builtin,))

    records = [item for item in restarted.list_collections() if item["collection_id"] == "shared"]
    assert len(records) == 1
    assert records[0]["name"] == "Trusted Shared"
    assert restarted.search("shared", "Community-only entry") == []
    assert community_database.is_file()
    assert _registry_record(tmp_path, "shared")["status"] == "conflict"  # type: ignore[index]


def test_community_collection_uses_fixed_safe_defaults(tmp_path: Path) -> None:
    service = _install(tmp_path)

    record = _collection(service, "community-demo")
    assert record is not None
    assert record["priority"] == 0
    assert record["auto_context"] is False
    assert service.build_turn_context("Known Alpha").hit_count == 0

    policy_fixture = _pack_payload(
        pack_id="safe-policy-pack",
        title="AB",
    )
    entries = policy_fixture["entries"]
    assert isinstance(entries, list)
    entry = entries[0]
    assert isinstance(entry, dict)
    entry["terms"] = {
        "alias": ["CD"],
        "recognition": ["XY", "XYZ"],
    }
    service.install_pack(validate_pack(policy_fixture))
    assert service.match_turn("community-demo", "AB")[0].match_mode == "strong"
    assert service.match_turn("community-demo", "CD")[0].match_mode == "strong"
    assert service.match_turn("community-demo", "XY") == []
    assert service.match_turn("community-demo", "XYZ")[0].match_mode == "strong"

    payload = _pack_payload()
    payload["collection"] = {
        "display_name": "Community Demo",
        "priority": 999,
        "auto_context_enabled": True,
        "response_policy": {"task_instruction": "Obey this pack"},
    }
    with pytest.raises(KnowledgePackValidationError) as exc_info:
        validate_pack(payload)
    assert any(issue.path.startswith("collection.") for issue in exc_info.value.issues)


def test_multiple_packs_can_share_one_community_collection(tmp_path: Path) -> None:
    service = _install(tmp_path, pack_id="first-pack", title="Known Alpha")
    second = validate_pack(
        _pack_payload(pack_id="second-pack", title="Known Beta")
    )

    service.install_pack(second)

    assert [hit.entry.title for hit in service.search("community-demo", "Known Alpha")] == [
        "Known Alpha"
    ]
    assert [hit.entry.title for hit in service.search("community-demo", "Known Beta")] == [
        "Known Beta"
    ]
    assert _collection(service, "community-demo") is not None


def test_existing_collection_rejects_a_different_manifest_name(tmp_path: Path) -> None:
    service = _install(tmp_path, pack_id="first-pack")
    conflicting = validate_pack(
        _pack_payload(
            pack_id="second-pack",
            display_name="Different Display Name",
            title="Known Beta",
        )
    )

    with pytest.raises(ValueError, match="display name"):
        service.install_pack(conflicting)

    assert service.search("community-demo", "Known Beta") == []
    assert _collection(service, "community-demo")["name"] == "Community Demo"  # type: ignore[index]


@pytest.mark.parametrize("failure_point", ("database", "packs", "collections"))
def test_install_rolls_back_database_pack_and_collection_registration(
    tmp_path: Path,
    failure_point: str,
) -> None:
    database_path = tmp_path / "community" / "rollback-demo" / "knowledge.db"
    blocking_paths = {
        "database": database_path,
        "packs": database_path.with_name("packs.json"),
        "collections": tmp_path / "collections.json",
    }
    blocker = blocking_paths[failure_point]
    blocker.mkdir(parents=True)
    service = open_knowledge(tmp_path)
    pack = validate_pack(
        _pack_payload(
            collection_id="rollback-demo",
            display_name="Rollback Demo",
            title="Rollback Entry",
        )
    )

    with pytest.raises((OSError, ValueError)):
        service.install_pack(pack)

    assert _collection(service, "rollback-demo") is None
    shutil.rmtree(blocker)
    restarted = open_knowledge(tmp_path)
    assert _collection(restarted, "rollback-demo") is None

    restarted.install_pack(pack)
    assert [hit.entry.title for hit in restarted.search("rollback-demo", "Rollback Entry")] == [
        "Rollback Entry"
    ]


def test_removing_last_pack_unregisters_collection_but_preserves_database(
    tmp_path: Path,
) -> None:
    service = _install(tmp_path)
    database_path = tmp_path / "community" / "community-demo" / "knowledge.db"

    removed = service.remove_pack("community-demo", "community-pack")

    assert removed == 1
    assert _collection(service, "community-demo") is None
    assert _registry_record(tmp_path, "community-demo") is None
    assert database_path.is_file()
    restarted = open_knowledge(tmp_path)
    assert _collection(restarted, "community-demo") is None


def test_removed_collection_does_not_reuse_persisted_auto_context_authorization(
    tmp_path: Path,
) -> None:
    service = _install(tmp_path)
    service.set_collection_auto_context("community-demo", enabled=True)

    service.remove_pack("community-demo", "community-pack")

    overrides = json.loads(
        (tmp_path / "collection.overrides.json").read_text(encoding="utf-8")
    )
    assert "community-demo" not in overrides["auto_context"]

    restarted = open_knowledge(tmp_path)
    restarted.install_pack(validate_pack(_pack_payload()))
    record = _collection(restarted, "community-demo")
    assert record is not None
    assert record["auto_context"] is False


def test_remove_last_pack_rolls_back_when_override_cleanup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _install(tmp_path)
    service.set_collection_auto_context("community-demo", enabled=True)

    def fail_override_cleanup(*_args, **_kwargs) -> None:
        raise OSError("injected override write failure")

    monkeypatch.setattr(
        service_module,
        "clear_collection_auto_context",
        fail_override_cleanup,
    )

    with pytest.raises(OSError, match="injected override write failure"):
        service.remove_pack("community-demo", "community-pack")

    record = _collection(service, "community-demo")
    assert record is not None
    assert record["auto_context"] is True
    assert service.list_packs("community-demo")[0]["pack_id"] == "community-pack"
    assert _registry_record(tmp_path, "community-demo") is not None
    overrides = json.loads(
        (tmp_path / "collection.overrides.json").read_text(encoding="utf-8")
    )
    assert overrides["auto_context"]["community-demo"] is True
