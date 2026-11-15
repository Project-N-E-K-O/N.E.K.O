"""Exercise durable consent and version decisions with isolated synthetic profiles."""

import copy
import hashlib
import json
import sqlite3

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync.profile_protocol import encode_character_profile
from services.chat_sync.profile_sync import ProfileStore
from services.chat_sync.ledger import ChatDomain


DOMAIN = ChatDomain("00000000-0000-4000-8000-000000000001", "a" * 32).key()
PROFILE = {"schemaVersion": 1, "name": "Synthetic", "nickname": "", "systemPrompt": "Original setting",
           "fields": [{"key": "Background", "value": "Synthetic background"}], "persona": None}


def candidate(text):
    """Model a later explicit edit without sharing mutable input fields."""
    return {**copy.deepcopy(PROFILE), "systemPrompt": text}


def head(profile, revision):
    """Build an independently hashed server head for the store's verified input boundary."""
    return {"profile": profile, "revision": revision,
            "profileHash": hashlib.sha256(encode_character_profile(profile)).hexdigest()}


def receipt(operation):
    """Acknowledge only the approved operation's immutable content and successor version."""
    return {"ownerId": json.loads(DOMAIN)[0], "characterSyncId": json.loads(DOMAIN)[1],
            "operationId": operation["operation_id"], "revision": operation["base_revision"] + 1,
            "profileHash": operation["profile_hash"]}


def approve(store, *, resolve=False):
    """Use the actual local candidate and cloud revision reviewed by a user."""
    state = store.status(DOMAIN)
    return store.approve(DOMAIN, state["local_hash"], state["head_revision"], resolve=resolve)


def test_declined_local_change_survives_restart_without_upload_eligibility(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    store = ProfileStore(path)
    store.observe(DOMAIN, PROFILE)
    restarted = ProfileStore(path)
    assert restarted.status(DOMAIN)["local"] == PROFILE
    assert restarted.status(DOMAIN)["dirty"]
    assert restarted.next_operation(DOMAIN) is None


def test_approved_request_stays_fixed_across_later_edit_and_restart(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    store = ProfileStore(path)
    store.observe(DOMAIN, PROFILE)
    operation_id = approve(store)
    original = store.next_operation(DOMAIN)
    store.observe(DOMAIN, candidate("LATER_LOCAL_EDIT"))
    restarted = ProfileStore(path)
    assert restarted.next_operation(DOMAIN) == original
    assert original["operation_id"] == operation_id
    assert json.loads(original["body"])["profile"] == PROFILE
    restarted.ack(DOMAIN, original, receipt(original))
    assert restarted.status(DOMAIN)["dirty"]
    assert restarted.status(DOMAIN)["local"]["systemPrompt"] == "LATER_LOCAL_EDIT"
    assert restarted.next_operation(DOMAIN) is None


def test_declined_change_keeps_both_sides_of_a_cloud_conflict(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    store.remote(DOMAIN, head(PROFILE, 1))
    store.observe(DOMAIN, candidate("LOCAL_DECLINED_UPLOAD"))
    cloud = candidate("OTHER_DEVICE_EDIT")
    store.remote(DOMAIN, head(cloud, 2))
    state = store.status(DOMAIN)
    assert state["conflict"]
    assert state["local"]["systemPrompt"] == "LOCAL_DECLINED_UPLOAD"
    assert state["head"] == cloud
    assert not state["apply_pending"]
    assert store.next_operation(DOMAIN) is None


def test_clean_local_state_marks_new_cloud_version_for_apply_without_overwriting(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    store.remote(DOMAIN, head(PROFILE, 1))
    cloud = candidate("REMOTE_EDIT")
    store.remote(DOMAIN, head(cloud, 2))
    state = store.status(DOMAIN)
    assert not state["dirty"] and state["apply_pending"]
    assert state["local"] == PROFILE
    store.applied(DOMAIN, state["local_hash"], state["head_revision"])
    assert store.status(DOMAIN)["local"] == cloud
    assert not store.status(DOMAIN)["apply_pending"]


def test_queued_consents_keep_independent_snapshots_and_ordered_cas_bases(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    first_id = approve(store)
    first = store.next_operation(DOMAIN)
    store.observe(DOMAIN, candidate("SECOND_CONSENTED_EDIT"))
    second_id = approve(store)
    assert second_id != first_id
    assert store.next_operation(DOMAIN) == first
    store.ack(DOMAIN, first, receipt(first))
    second = store.next_operation(DOMAIN)
    assert second["operation_id"] == second_id and second["base_revision"] == 1
    assert json.loads(second["body"])["profile"]["systemPrompt"] == "SECOND_CONSENTED_EDIT"
    store.ack(DOMAIN, second, receipt(second))
    assert not store.status(DOMAIN)["dirty"]


def test_rejected_cas_preserves_fixed_operation_until_explicit_new_choice(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    first = approve(store)
    store.conflict(first)
    store.remote(DOMAIN, head(candidate("CLOUD_WINNER"), 1))
    assert store.next_operation(DOMAIN) is None
    second = approve(store, resolve=True)
    pending = store.next_operation(DOMAIN)
    assert second != first and pending["base_revision"] == 1
    assert json.loads(pending["body"])["profile"] == PROFILE


def test_identical_concurrent_cloud_edit_does_not_block_later_approved_upload(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    rejected_id = approve(store)
    rejected = store.next_operation(DOMAIN)
    store.conflict(rejected_id)
    # Another device published the same content; convergence needs no user choice.
    store.remote(DOMAIN, head(PROFILE, 1))
    assert not store.status(DOMAIN)["conflict"]
    store.observe(DOMAIN, candidate("NEXT_APPROVED_EDIT"))
    next_id = approve(store)
    pending = store.next_operation(DOMAIN)
    assert pending is not None and pending["operation_id"] == next_id
    assert pending["base_revision"] == 1
    assert json.loads(pending["body"])["profile"] == candidate("NEXT_APPROVED_EDIT")
    # Superseding a stale operation must retain the exact bytes already approved.
    with sqlite3.connect(store.path) as connection:
        row = connection.execute("SELECT body FROM profile_outbox WHERE operation_id=?", (rejected_id,)).fetchone()
    assert row[0] == rejected["body"]


@pytest.mark.parametrize("choice", ["approve", "choose_remote"])
def test_third_cloud_edit_invalidates_the_previously_reviewed_choice(tmp_path, choice):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    store.remote(DOMAIN, head(candidate("CLOUD_VERSION_1"), 1))
    reviewed = store.status(DOMAIN)
    store.remote(DOMAIN, head(candidate("CLOUD_VERSION_2"), 2))
    with pytest.raises(ValueError, match="character_profile_changed"):
        if choice == "approve":
            store.approve(DOMAIN, reviewed["local_hash"], reviewed["head_revision"], resolve=True)
        else:
            store.choose_remote(DOMAIN, reviewed["local_hash"], reviewed["head_revision"])
    assert store.status(DOMAIN)["conflict"]
    assert store.status(DOMAIN)["local"] == PROFILE


def test_new_local_edit_blocks_an_older_remote_apply_commit(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    store.remote(DOMAIN, head(PROFILE, 1))
    store.remote(DOMAIN, head(candidate("REMOTE"), 2))
    reviewed = store.status(DOMAIN)
    store.observe(DOMAIN, candidate("NEW_LOCAL_EDIT"))
    with pytest.raises(ValueError, match="character_profile_changed"):
        store.applied(DOMAIN, reviewed["local_hash"], 2)
    assert store.status(DOMAIN)["local"]["systemPrompt"] == "NEW_LOCAL_EDIT"


@pytest.mark.parametrize("incoming", [head(PROFILE, 1), head(candidate("DIFFERENT_CONTENT_SAME_REVISION"), 2)])
def test_cloud_rollback_and_same_version_mutation_are_rejected(tmp_path, incoming):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    store.remote(DOMAIN, head(PROFILE, 2))
    with pytest.raises(ValueError, match="invalid_character_profile_response"):
        store.remote(DOMAIN, incoming)
    assert store.status(DOMAIN)["head_revision"] == 2


def test_foreign_account_and_other_character_have_independent_states(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    other_owner = ChatDomain("00000000-0000-4000-8000-000000000002", "a" * 32).key()
    other_character = ChatDomain("00000000-0000-4000-8000-000000000001", "b" * 32).key()
    store.observe(DOMAIN, PROFILE)
    approve(store)
    for domain in (other_owner, other_character):
        store.observe(domain, candidate("OTHER_DOMAIN"))
        assert store.next_operation(domain) is None
    assert store.status(DOMAIN)["local"] == PROFILE
    assert store.next_operation(DOMAIN) is not None


def test_wrong_receipt_does_not_consume_approved_content(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    approve(store)
    operation = store.next_operation(DOMAIN)
    with pytest.raises(ValueError, match="invalid_character_profile_receipt"):
        store.ack(DOMAIN, operation, {**receipt(operation), "revision": 2})
    assert store.next_operation(DOMAIN) == operation


def test_repeated_older_ack_does_not_rollback_a_newer_baseline(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    approve(store)
    first = store.next_operation(DOMAIN)
    store.ack(DOMAIN, first, receipt(first))
    store.observe(DOMAIN, candidate("NEWER_APPROVED_EDIT"))
    approve(store)
    second = store.next_operation(DOMAIN)
    store.ack(DOMAIN, second, receipt(second))
    store.ack(DOMAIN, first, receipt(first))
    state = store.status(DOMAIN)
    assert state["base_revision"] == 2 and state["base_hash"] == second["profile_hash"]
    assert state["head_revision"] == 2 and state["local"] == candidate("NEWER_APPROVED_EDIT")


def test_old_ack_cannot_clear_a_new_local_edit_matching_the_old_published_content(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    store.observe(DOMAIN, PROFILE)
    approve(store)
    first = store.next_operation(DOMAIN)
    store.ack(DOMAIN, first, receipt(first))
    store.observe(DOMAIN, candidate("NEWER_PUBLISHED_CONTENT"))
    approve(store)
    second = store.next_operation(DOMAIN)
    store.ack(DOMAIN, second, receipt(second))
    # Editing back to an old value is a new local change, not an old ACK's consent.
    store.observe(DOMAIN, PROFILE)
    store.ack(DOMAIN, first, receipt(first))
    state = store.status(DOMAIN)
    assert state["local"] == PROFILE and state["dirty"]
    assert state["head_revision"] == 2 and state["base_hash"] == second["profile_hash"]


def test_resolving_cloud_retains_the_local_candidate_for_recovery(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    store = ProfileStore(path)
    store.observe(DOMAIN, PROFILE)
    store.remote(DOMAIN, head(candidate("CHOSEN_CLOUD"), 1))
    state = store.status(DOMAIN)
    store.choose_remote(DOMAIN, state["local_hash"], 1)
    store.applied(DOMAIN, state["local_hash"], 1)
    with sqlite3.connect(path) as connection:
        values = [encode_character_profile(PROFILE), encode_character_profile(candidate("CHOSEN_CLOUD"))]
        assert {row[0] for row in connection.execute("SELECT profile FROM profile_versions WHERE domain=?", (DOMAIN,))} == set(values)


def test_unfinished_import_recovers_the_original_reviewed_snapshot(tmp_path):
    path = tmp_path / "profiles.sqlite3"
    store = ProfileStore(path)
    reviewed = head(PROFILE, 1)
    store.import_intent(DOMAIN, reviewed)
    restarted = ProfileStore(path)
    assert restarted.unfinished_import(DOMAIN) == reviewed
    assert restarted.import_intent(DOMAIN, head(candidate("LATER_CLOUD"), 2)) == reviewed


def test_new_import_after_completion_uses_the_newly_reviewed_version(tmp_path):
    store = ProfileStore(tmp_path / "profiles.sqlite3")
    first = head(PROFILE, 1)
    store.import_intent(DOMAIN, first)
    store.complete_import(DOMAIN)
    assert store.unfinished_import(DOMAIN) is None
    # A new explicit import after the original local role was removed is a new operation.
    reviewed_again = head(candidate("NEW_REVIEWED_CLOUD"), 2)
    assert store.import_intent(DOMAIN, reviewed_again) == reviewed_again
    assert store.unfinished_import(DOMAIN) == reviewed_again
