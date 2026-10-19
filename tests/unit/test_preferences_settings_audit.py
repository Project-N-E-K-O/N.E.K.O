"""Durable settings/journal handoff; every test uses an isolated preferences file."""

import asyncio
from copy import deepcopy
from contextlib import contextmanager
import json
import threading

import pytest

from utils import file_utils, preferences, preferences_audit


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    path = tmp_path / "user_preferences.json"

    class Config:
        def get_config_path(self, _name):
            return path

        get_runtime_config_path = get_config_path

        def ensure_config_directory(self):
            path.parent.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(preferences, "_config_manager", Config())
    monkeypatch.setattr(preferences, "assert_cloudsave_writable", lambda *a, **k: None)
    monkeypatch.setattr(preferences_audit, "_attribution_factory", None)
    return path


def seed(path, **settings):
    path.write_text(json.dumps([{"model_path": preferences.GLOBAL_CONVERSATION_KEY, **settings}]), encoding="utf-8")


def test_actual_change_commits_with_config_and_filters_unrelated_data(isolated):
    seed(isolated, proactiveMusicEnabled=False, userLanguage="private-text")
    assert preferences.save_global_conversation_settings({
        "proactiveMusicEnabled": True, "proactiveChatInterval": 15,
        "userLanguage": "another-private-text", "independentAsrEnabled": True,
        "proactiveUnrecognized": "never persist",
    })
    entry, = preferences.pending_settings_changes()
    assert entry["payload"]["origin"] == "user"
    assert entry["payload"]["previous"] == {"proactiveMusicEnabled": False}
    assert entry["payload"]["settings"] == {"proactiveMusicEnabled": True, "proactiveChatInterval": 15}
    assert entry["payload"]["revision"] == 1
    assert entry["payload"]["occurred_at"] > 0
    assert entry["owner"]["user_id"] is None
    raw = json.loads(isolated.read_text(encoding="utf-8"))
    assert raw[0][preferences_audit.JOURNAL_KEY]["entries"] == [entry]
    assert raw[0]["proactiveMusicEnabled"] is True
    assert "private-text" not in json.dumps(entry)
    assert "independentAsrEnabled" not in json.dumps(entry)
    assert preferences_audit.JOURNAL_KEY not in preferences.load_user_preferences()[0]
    assert preferences_audit.JOURNAL_KEY not in preferences.load_global_entry_flags()


def test_initialization_is_baseline_and_reading_existing_config_has_no_side_effect(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    original = isolated.read_bytes()
    assert preferences.pending_settings_changes() == []
    assert isolated.read_bytes() == original
    isolated.unlink()
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    first, = preferences.pending_settings_changes()
    assert first["payload"]["origin"] == "baseline"
    assert first["payload"]["previous"] == {}
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True}, audit_origin="system")
    assert preferences.pending_settings_changes()[1]["payload"]["origin"] == "system"


def test_noop_cas_conflict_and_nonproactive_change_do_not_create_history(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    assert preferences.save_global_conversation_settings({"userLanguage": "zh-CN"})
    conflict = preferences.save_global_conversation_settings_versioned({"proactiveMusicEnabled": True}, expected_revision=0)
    assert conflict.conflict
    assert preferences.pending_settings_changes() == []


def test_failed_replace_preserves_config_and_journal(isolated, monkeypatch):
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    original = isolated.read_bytes()

    def fail(*args):
        raise PermissionError("injected replace failure")

    monkeypatch.setattr(file_utils, "_replace_with_busy_retry", fail)
    assert not preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    assert isolated.read_bytes() == original
    assert preferences.load_global_conversation_settings()["proactiveMusicEnabled"] is True
    assert len(preferences.pending_settings_changes()) == 1
    with pytest.raises(PermissionError):
        preferences.acknowledge_settings_changes([preferences.pending_settings_changes()[0]["id"]])
    assert isolated.read_bytes() == original


def test_ack_only_removes_exact_ids_and_preserves_revision_across_restart(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    first, = preferences.pending_settings_changes()
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    second = preferences.pending_settings_changes()[1]
    assert preferences.acknowledge_settings_changes([first["id"], "not-present"]) == 1
    assert preferences.pending_settings_changes() == [second]
    assert preferences.acknowledge_settings_changes([first["id"]]) == 0
    assert preferences.acknowledge_settings_changes([second["id"]]) == 1
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    third, = preferences.pending_settings_changes()
    assert third["payload"]["revision"] == 3
    assert third["id"] != first["id"]


def test_full_journal_rejects_change_and_preserves_existing_records(isolated, monkeypatch):
    monkeypatch.setattr(preferences_audit, "MAX_PENDING_CHANGES", 2)
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    original = isolated.read_bytes()
    assert not preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    assert isolated.read_bytes() == original
    # Model/UI writes can still proceed without deleting or adding audit records.
    assert preferences.save_ui_language_override("ja")
    assert len(preferences.pending_settings_changes()) == 2
    assert preferences.load_global_conversation_settings()["proactiveMusicEnabled"] is False


def test_raw_full_save_cannot_forge_or_drop_pending_journal(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    original = preferences.pending_settings_changes()
    public = preferences.load_user_preferences()
    public[0][preferences_audit.JOURNAL_KEY] = {"version": 1, "revision": 999, "entries": []}
    assert preferences.save_user_preferences(public)
    assert preferences.pending_settings_changes() == original
    assert preferences.update_model_preferences("a.vrm", {"x": 0, "y": 0}, {"x": 1, "y": 1})
    assert preferences.move_model_to_top("a.vrm")
    assert preferences.pending_settings_changes() == original


def test_original_owner_fenced_through_commit_and_anonymous_never_rebound(isolated, monkeypatch):
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    anonymous, = preferences.pending_settings_changes()
    account_lock = threading.RLock()
    identity = {"key": "account-a", "user_id": "a", "community": "https://example.test"}
    entered, release, switched = threading.Event(), threading.Event(), threading.Event()

    @contextmanager
    def attribution():
        with account_lock:
            yield dict(identity)

    preferences.install_settings_attribution_context(attribution)
    atomic = preferences.atomic_write_json

    def blocked_write(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return atomic(*args, **kwargs)

    monkeypatch.setattr(preferences, "atomic_write_json", blocked_write)
    outcomes = []
    writer = threading.Thread(target=lambda: outcomes.append(preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})))

    def change_owner():
        with account_lock:
            identity.update(key="account-b", user_id="b")
            switched.set()

    switcher = threading.Thread(target=change_owner)
    try:
        writer.start()
        assert entered.wait(5)
        switcher.start()
        assert not switched.wait(0.05)
    finally:
        release.set()
        writer.join(5)
        if switcher.ident is not None:
            switcher.join(5)
    assert not writer.is_alive() and not switcher.is_alive()
    assert outcomes == [True]
    committed = preferences.pending_settings_changes()
    assert committed[0] == anonymous
    assert committed[1]["owner"]["user_id"] == "a"
    # Ack/read must work even if resolving current identity would fail.
    def broken_attribution():
        raise AssertionError("journal handoff must not inspect current account")
    preferences.install_settings_attribution_context(broken_attribution)
    assert preferences.acknowledge_settings_changes([committed[1]["id"]]) == 1
    assert preferences.pending_settings_changes() == [anonymous]


@pytest.mark.parametrize("broken", ["not json", '[{"model_path":"__global_conversation__","_settings_mutation_journal":{"version":2}}]'])
def test_unreadable_or_unknown_journal_is_not_overwritten(isolated, broken):
    isolated.write_text(broken, encoding="utf-8")
    original = isolated.read_bytes()
    assert not preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    assert isolated.read_bytes() == original
    with pytest.raises((ValueError, json.JSONDecodeError)):
        preferences.pending_settings_changes()


def test_async_counterparts_and_flags_cover_same_journal(isolated):
    seed(isolated, proactiveMusicEnabled=False)

    async def run():
        assert await preferences.asave_global_entry_flags({"proactiveMusicEnabled": True, "uiLanguage": "ja"})
        first, = await preferences.apending_settings_changes()
        assert await preferences.aacknowledge_settings_changes([first["id"]]) == 1
        assert await preferences.asave_global_conversation_settings({"proactiveMusicEnabled": False})
        result = await preferences.asave_global_conversation_settings_versioned({"proactiveMusicEnabled": True})
        assert result.success
        assert len(await preferences.apending_settings_changes()) == 2

    asyncio.run(run())


def test_input_validation_and_invalid_origin_do_not_commit(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    original = isolated.read_bytes()
    assert not preferences.save_global_conversation_settings({"proactiveMusicEnabled": True}, audit_origin="bad")
    assert isolated.read_bytes() == original
    for limit in [0, 4097, True, "100"]:
        with pytest.raises(ValueError):
            preferences.pending_settings_changes(limit)
    assert preferences.acknowledge_settings_changes([]) == 0
    with pytest.raises(ValueError):
        preferences.acknowledge_settings_changes([1])


def test_cloud_archive_export_excludes_journal_restore_preserves_local_and_ignores_import(isolated):
    from utils.cloudsave_runtime import bindings

    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    local = preferences.pending_settings_changes()
    manager = preferences._config_manager
    exported = bindings._extract_conversation_settings(manager)
    assert preferences_audit.JOURNAL_KEY not in exported
    assert exported["proactiveMusicEnabled"] is True
    archive = {
        "proactiveMusicEnabled": False,
        preferences_audit.JOURNAL_KEY: {"version": 1, "revision": 999, "entries": [{"id": "forged", "owner": {}, "payload": {}}]},
    }
    restored = bindings._build_runtime_preferences_payload(manager, archive)
    assert preferences_audit.read_journal(restored)["entries"] == local
    assert preferences_audit.proactive_snapshot(restored) == {"proactiveMusicEnabled": False}
    # A stale archive cannot resurrect or import already acknowledged history.
    assert preferences.acknowledge_settings_changes([local[0]["id"]]) == 1
    restored = bindings._build_runtime_preferences_payload(manager, archive)
    assert preferences_audit.read_journal(restored)["entries"] == []


def test_cloud_restore_aborts_if_local_journal_cannot_be_read(isolated):
    from utils.cloudsave_runtime import bindings

    isolated.write_text("broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        bindings._build_runtime_preferences_payload(preferences._config_manager, {})
    assert isolated.read_text(encoding="utf-8") == "broken"


def test_local_root_migration_preserves_original_owner_events_and_rejects_conflicts(isolated, monkeypatch):
    from utils.cloudsave_runtime.legacy_migration import _merge_preferences_payloads

    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    older = json.loads(isolated.read_text(encoding="utf-8"))
    first = preferences.pending_settings_changes()[0]
    assert preferences.acknowledge_settings_changes([first["id"]]) == 1

    @contextmanager
    def logged_in():
        yield {"key": "new-owner", "user_id": "another-account", "community": "https://example.test"}

    preferences.install_settings_attribution_context(logged_in)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    newer = json.loads(isolated.read_text(encoding="utf-8"))
    second = preferences.pending_settings_changes()[0]
    merged = _merge_preferences_payloads(older, newer)
    assert preferences_audit.read_journal(merged)["entries"] == [first, second]
    assert preferences_audit.proactive_snapshot(merged) == {"proactiveMusicEnabled": False}
    assert preferences_audit.read_journal(_merge_preferences_payloads(older, older))["entries"] == [first]
    forged = deepcopy(older)
    forged[0][preferences_audit.JOURNAL_KEY]["entries"][0]["payload"]["origin"] = "system"
    with pytest.raises(ValueError, match="Conflicting"):
        _merge_preferences_payloads(older, forged)
    monkeypatch.setattr(preferences_audit, "MAX_PENDING_CHANGES", 1)
    with pytest.raises(ValueError, match="capacity"):
        _merge_preferences_payloads(older, newer)


def test_full_preferences_reset_logs_change_without_dropping_pending(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    first = preferences.pending_settings_changes()[0]
    assert preferences.save_user_preferences([])
    changes = preferences.pending_settings_changes()
    assert changes[0] == first
    assert changes[1]["payload"]["previous"] == {"proactiveMusicEnabled": True}
    assert changes[1]["payload"]["settings"] == {}


def test_invalid_attribution_fails_before_config_commit(isolated):
    seed(isolated, proactiveMusicEnabled=False)
    original = isolated.read_bytes()

    @contextmanager
    def invalid_owner():
        yield None

    preferences.install_settings_attribution_context(invalid_owner)
    assert not preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    assert isolated.read_bytes() == original


def test_real_json_to_sqlite_handoff_survives_ack_failure_without_duplicate(isolated, monkeypatch):
    import sqlite3
    from main_logic.recommendation_feedback import RecommendationStore
    from services.proactive_feedback.host import owner_from_snapshot
    from services.proactive_feedback.protocol import COMMUNITY_ORIGIN
    from services.proactive_feedback.runtime import drain_settings

    owner = owner_from_snapshot({"access_token": "test-only", "base_url": COMMUNITY_ORIGIN, "local_user_id": "user-a"})

    @contextmanager
    def attribution():
        yield owner

    preferences.install_settings_attribution_context(attribution)
    seed(isolated, proactiveMusicEnabled=False)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    original = preferences.pending_settings_changes()
    store = RecommendationStore(isolated.parent / "feedback.sqlite3")
    atomic = preferences.atomic_write_json

    def fail_ack(*args, **kwargs):
        raise OSError("injected JSON acknowledgement disk failure")

    monkeypatch.setattr(preferences, "atomic_write_json", fail_ack)
    with pytest.raises(OSError):
        drain_settings(store)
    assert preferences.pending_settings_changes() == original
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM feedback_settings_evidence").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM feedback_sync_outbox").fetchone()[0] == 1
    # Simulate restart: rebuild Store and retry the exact committed journal ID.
    monkeypatch.setattr(preferences, "atomic_write_json", atomic)
    drain_settings(RecommendationStore(store.path))
    assert preferences.pending_settings_changes() == []
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM feedback_settings_evidence").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM feedback_sync_outbox").fetchone()[0] == 1
        saved_owner, item_id, payload = db.execute("SELECT owner,item_id,payload FROM feedback_sync_outbox").fetchone()
    assert saved_owner == owner["key"]
    assert item_id == original[0]["id"]
    assert json.loads(payload) == original[0]["payload"]


@pytest.mark.parametrize("fail_after_preferences_apply", [False, True], ids=["successful-restore", "rollback-after-apply"])
def test_restore_and_prechecked_writer_share_journal_lock_through_rollback(
    isolated, monkeypatch, fail_after_preferences_apply,
):
    from tests.unit.test_cloudsave_runtime import _make_config_manager, _write_runtime_state
    from utils import cloudsave_runtime
    from utils.cloudsave_runtime import operations

    manager = _make_config_manager(isolated.parent / "cloud-restore")
    _write_runtime_state(manager)
    monkeypatch.setattr(preferences, "_config_manager", manager)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": False})
    cloudsave_runtime.export_local_cloudsave_snapshot(manager)
    assert preferences.save_global_conversation_settings({"proactiveMusicEnabled": True})
    original = preferences.pending_settings_changes()
    checked, writer_continue, staged, restore_continue, writer_committed = (threading.Event() for _ in range(5))
    outcomes = {}
    real_assert = cloudsave_runtime.assert_cloudsave_writable

    def prechecked(*args, **kwargs):
        real_assert(*args, **kwargs)
        if threading.current_thread().name == "prechecked-settings-writer":
            checked.set()
            assert writer_continue.wait(10)

    monkeypatch.setattr(preferences, "assert_cloudsave_writable", prechecked)
    real_build = operations._build_runtime_preferences_payload

    def build_and_pause(*args, **kwargs):
        result = real_build(*args, **kwargs)
        staged.set()
        assert restore_continue.wait(10)
        return result

    monkeypatch.setattr(operations, "_build_runtime_preferences_payload", build_and_pause)
    real_apply = cloudsave_runtime._apply_runtime_file
    did_fail = False

    def apply_then_fail_once(source, target):
        nonlocal did_fail
        result = real_apply(source, target)
        if fail_after_preferences_apply and target.name == "user_preferences.json" and not did_fail:
            did_fail = True
            raise OSError("injected failure after preference file replacement")
        return result

    monkeypatch.setattr(cloudsave_runtime, "_apply_runtime_file", apply_then_fail_once)

    def write():
        try:
            outcomes["write"] = preferences.save_global_conversation_settings({"proactiveNewsChatEnabled": True})
        except BaseException as exc:
            outcomes["write_error"] = exc
        finally:
            writer_committed.set()

    def restore():
        try:
            outcomes["restore"] = cloudsave_runtime.import_local_cloudsave_snapshot(manager)
        except BaseException as exc:
            outcomes["restore_error"] = exc

    writer = threading.Thread(target=write, name="prechecked-settings-writer")
    restorer = threading.Thread(target=restore, name="cloud-settings-restorer")
    try:
        writer.start()
        assert checked.wait(10)
        restorer.start()
        assert staged.wait(10)
        writer_continue.set()
        # This writer passed maintenance before restore began. The shared file
        # lock, rather than another mode check, must keep it behind apply/rollback.
        assert not writer_committed.wait(0.1)
    finally:
        writer_continue.set()
        restore_continue.set()
        writer.join(15)
        if restorer.ident is not None:
            restorer.join(15)
    assert not writer.is_alive() and not restorer.is_alive()
    assert outcomes.get("write") is True, outcomes
    if fail_after_preferences_apply:
        assert did_fail
        assert isinstance(outcomes.get("restore_error"), OSError), outcomes
    else:
        assert "restore_error" not in outcomes, outcomes
    settings = preferences.load_global_conversation_settings()
    assert settings["proactiveMusicEnabled"] is fail_after_preferences_apply
    assert settings["proactiveNewsChatEnabled"] is True
    committed = preferences.pending_settings_changes()
    assert committed[:len(original)] == original
    assert len(committed) == len(original) + 1
    assert committed[-1]["payload"]["settings"]["proactiveMusicEnabled"] is fail_after_preferences_apply
