"""claude_companion keeps its inbox in the plugin data directory.

The inbox used to live next to the plugin code, which is part of the packaged
source fingerprint and may be read-only once installed. Startup moves a legacy
file over once, and a failed migration must not stop the plugin.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import plugin.plugins.claude_companion as companion
from plugin.plugins.claude_companion import ClaudeCompanionPlugin, InboxQueue, _migrate_legacy_inbox

pytestmark = pytest.mark.plugin_unit

_SECRET = "private message body"


class _Logger:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, tuple]] = []

    def _record(self, level: str, msg: str, *args: object, **_kwargs: object) -> None:
        self.lines.append((level, msg, args))

    def info(self, msg: str, *args: object, **kwargs: object) -> None:
        self._record("info", msg, *args, **kwargs)

    def warning(self, msg: str, *args: object, **kwargs: object) -> None:
        self._record("warning", msg, *args, **kwargs)

    def text(self) -> str:
        return "\n".join(f"{msg} {args}" for _level, msg, args in self.lines)


class _Context:
    plugin_id = "claude_companion"


def _legacy_payload() -> list[dict[str, str]]:
    return [
        {
            "id": "abc12345",
            "timestamp": "2026-11-23T10:00:00+08:00",
            "content": _SECRET,
            "source": "neko",
            "type": "message",
            "status": "pending",
        }
    ]


def _write_legacy(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_legacy_payload(), ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ClaudeCompanionPlugin:
    monkeypatch.setenv("NEKO_STORAGE_SELECTED_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(companion, "_LEGACY_INBOX_PATH", tmp_path / "install" / "inbox.json")
    instance = ClaudeCompanionPlugin.__new__(ClaudeCompanionPlugin)
    instance.ctx = _Context()
    instance.logger = _Logger()
    instance._inbox = None
    return instance


def test_inbox_reads_and_writes_in_data_directory(plugin: ClaudeCompanionPlugin) -> None:
    plugin._init_inbox()

    assert plugin._inbox is not None
    plugin._inbox.add("hello")
    target = plugin.data_path("inbox.json")
    assert target.is_file()
    assert target.parent == plugin.data_path()
    assert not companion._LEGACY_INBOX_PATH.exists()
    assert not companion._LEGACY_INBOX_PATH.parent.exists()

    # A later start reads the same file back.
    plugin._inbox = None
    plugin._init_inbox()
    assert [m["content"] for m in plugin._inbox.pending()] == ["hello"]


def test_startup_moves_legacy_inbox(plugin: ClaudeCompanionPlugin) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)

    plugin._init_inbox()

    assert not legacy.exists()
    target = plugin.data_path("inbox.json")
    assert json.loads(target.read_text(encoding="utf-8")) == _legacy_payload()
    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]
    assert _SECRET not in plugin.logger.text()


def test_existing_data_inbox_wins_and_leftover_legacy_is_removed(plugin: ClaudeCompanionPlugin) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    target.write_text("[]", encoding="utf-8")

    plugin._init_inbox()

    assert not legacy.exists()
    assert plugin._inbox.pending() == []

    # Clearing the data directory later must not bring the stale copy back.
    target.unlink()
    plugin._init_inbox()
    assert plugin._inbox.pending() == []


def test_leftover_legacy_that_cannot_be_removed_is_ignored(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    target.write_text("[]", encoding="utf-8")
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == legacy:
            raise PermissionError("read-only install directory")
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)

    plugin._init_inbox()

    assert legacy.is_file()
    assert plugin._inbox.pending() == []
    assert [level for level, _msg, _args in plugin.logger.lines] == []


def test_copy_fallback_when_move_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    legacy = tmp_path / "install" / "inbox.json"
    target = tmp_path / "data" / "inbox.json"
    _write_legacy(legacy)
    real_replace = os.replace

    def replace(src: object, dst: object) -> None:
        if Path(src) == legacy:
            raise OSError("cross-device link")
        real_replace(src, dst)

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self == legacy:
            raise PermissionError("read-only install directory")
        os.remove(self)

    monkeypatch.setattr(companion.os, "replace", replace)
    monkeypatch.setattr(Path, "unlink", unlink)
    logger = _Logger()

    assert _migrate_legacy_inbox(legacy, target, logger) == companion._MIGRATION_DONE

    assert json.loads(target.read_text(encoding="utf-8")) == _legacy_payload()
    assert legacy.is_file()  # could not be removed, left in place
    assert not (tmp_path / "data" / "inbox.json.migrating").exists()
    assert [level for level, _msg, _args in logger.lines] == ["info"]
    assert _SECRET not in logger.text()


def _fail(*_args: object, **_kwargs: object) -> None:
    raise PermissionError("denied")


_REAL_REPLACE = os.replace


def _replace_failing_from_legacy(src: object, dst: object) -> None:
    """os.replace that cannot move the legacy inbox but still saves the queue."""
    if Path(src) == companion._LEGACY_INBOX_PATH:
        raise PermissionError("denied")
    _REAL_REPLACE(src, dst)


def _migration_warnings(plugin: ClaudeCompanionPlugin) -> list[tuple[str, tuple]]:
    return [(msg, args) for level, msg, args in plugin.logger.lines if level == "warning"]


def test_failed_migration_keeps_legacy_messages(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _replace_failing_from_legacy)
        patched.setattr(companion.shutil, "copyfileobj", _fail)
        plugin._init_inbox()

    # The legacy file could not be moved or copied but is readable: the queue
    # opens with its messages, so writing the new file completes the migration.
    assert plugin._inbox is not None
    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]
    assert json.loads(plugin.data_path("inbox.json").read_text(encoding="utf-8")) == _legacy_payload()
    assert legacy.is_file()
    warnings = _migration_warnings(plugin)
    assert len(warnings) == 1
    assert "migration failed" in warnings[0][0]
    assert warnings[0][1] == ("PermissionError",)
    assert _SECRET not in plugin.logger.text()
    assert str(legacy) not in plugin.logger.text()

    # A message arriving before the next start must not hide the old ones.
    assert plugin._on_inbox_send({"content": "new"})["status"] == "ok"
    plugin._inbox = None
    plugin._init_inbox()

    assert [m["content"] for m in plugin._inbox.pending()] == [_SECRET, "new"]


def test_failed_migration_survives_ack_before_restart(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _replace_failing_from_legacy)
        patched.setattr(companion.shutil, "copyfileobj", _fail)
        plugin._init_inbox()
    assert plugin._on_inbox_ack({"message_ids": ["other"]}) == {"status": "ok", "acknowledged": 0}

    plugin._inbox = None
    plugin._init_inbox()

    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_unreadable_legacy_inbox_disables_inbox_until_migrated(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _replace_failing_from_legacy)
        patched.setattr(companion.shutil, "copyfileobj", _fail)
        patched.setattr(companion, "_read_legacy_inbox", _fail)
        plugin._init_inbox()

        # The plugin keeps running; the inbox endpoints report it unavailable
        # and nothing is written at the new location.
        assert plugin._inbox is None
        assert plugin._on_inbox_send({"content": "new"}) == {"error": "inbox not initialized"}
        assert plugin._on_inbox_ack({})["acknowledged"] == 0
        assert not plugin.data_path("inbox.json").exists()

    assert legacy.is_file()
    assert [args for _msg, args in _migration_warnings(plugin)] == [("PermissionError",), ("PermissionError",)]
    assert _SECRET not in plugin.logger.text()
    assert str(legacy) not in plugin.logger.text()

    # Once the file can be moved, the next start migrates it.
    plugin._init_inbox()

    assert not legacy.exists()
    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_corrupt_legacy_inbox_counts_as_empty(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{not json", encoding="utf-8")

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _replace_failing_from_legacy)
        patched.setattr(companion.shutil, "copyfileobj", _fail)
        plugin._init_inbox()

    assert plugin._inbox is not None
    assert plugin._inbox.pending() == []


def test_inbox_queue_creates_missing_data_directory(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "data" / "inbox.json"
    queue = InboxQueue(str(path))
    queue.add("hi")
    assert json.loads(path.read_text(encoding="utf-8"))[0]["content"] == "hi"


def test_inbox_send_log_omits_message_content(plugin: ClaudeCompanionPlugin) -> None:
    plugin._init_inbox()
    plugin.logger.lines.clear()

    assert plugin._on_inbox_send({"content": _SECRET, "source": "neko"})["status"] == "ok"

    assert plugin.logger.lines == [("info", "Inbox: new message from {} ({} chars)", ("neko", len(_SECRET)))]
    logged = plugin.logger.text()
    assert _SECRET not in logged
    assert "private" not in logged


def _interrupted_dump(obj: object, fp: object, **_kwargs: object) -> None:
    fp.write("[{")  # part of the payload reaches the disk, then the write fails
    raise OSError("No space left on device")


def test_interrupted_first_save_leaves_migration_retryable(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    target = plugin.data_path("inbox.json")

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _replace_failing_from_legacy)
        patched.setattr(companion.shutil, "copyfileobj", _fail)
        patched.setattr(companion.json, "dump", _interrupted_dump)
        plugin._init_inbox()

    # The seeded queue's first save failed half-way: no target, no temp file.
    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]
    assert not target.exists()
    assert not target.with_name("inbox.json.tmp").exists()
    assert legacy.is_file()

    plugin._inbox = None
    plugin._init_inbox()

    assert not legacy.exists()
    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_interrupted_save_keeps_existing_inbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "data" / "inbox.json"
    queue = InboxQueue(str(path))
    queue.add("kept")
    before = path.read_text(encoding="utf-8")

    with monkeypatch.context() as patched:
        patched.setattr(companion.json, "dump", _interrupted_dump)
        queue.add("lost")

    assert path.read_text(encoding="utf-8") == before
    assert not path.with_name("inbox.json.tmp").exists()
    assert [m["content"] for m in InboxQueue(str(path)).pending()] == ["kept"]


class _Debuggable(_Logger):
    def debug(self, msg: str, *args: object, **kwargs: object) -> None:
        self._record("debug", msg, *args, **kwargs)


def test_turn_start_log_omits_prompt(plugin: ClaudeCompanionPlugin) -> None:
    plugin.logger = _Debuggable()

    plugin._on_turn_start({"prompt": _SECRET})

    assert plugin.logger.lines == [("debug", "Turn start: prompt of {} chars", (len(_SECRET),))]
    assert _SECRET not in plugin.logger.text()


class _Store:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    def _write_value(self, key: str, value: object) -> None:
        self.data[key] = value


async def test_set_user_name_log_omits_name(plugin: ClaudeCompanionPlugin) -> None:
    plugin.store = _Store()
    name = "Private Person"

    await plugin.set_user_name(name)

    assert plugin.store.data == {"user_name": name}
    assert plugin.logger.lines == [("info", "User name updated ({} chars)", (len(name),))]
    assert name not in plugin.logger.text()


def test_legacy_inbox_with_bad_encoding_counts_as_empty(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"\xff\xfe not utf-8")

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _replace_failing_from_legacy)
        patched.setattr(companion.shutil, "copyfileobj", _fail)
        plugin._init_inbox()

    assert plugin._inbox is not None
    assert plugin._inbox.pending() == []


def test_data_inbox_with_bad_encoding_loads_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "inbox.json"
    path.write_bytes(b"\xff\xfe not utf-8")
    assert InboxQueue(str(path)).pending() == []


def test_uninspectable_paths_keep_existing_data_inbox(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    newer = json.dumps([dict(_legacy_payload()[0], id="newer001", content="newer")])
    target.write_text(newer, encoding="utf-8")
    real_exists = Path.exists

    def exists(self: Path) -> bool:
        if self == target:
            raise PermissionError("denied")
        return real_exists(self)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "exists", exists)
        plugin._init_inbox()

    # The target's state is unknown: the inbox stays closed and neither file
    # is touched, so the newer data inbox is not replaced by the legacy copy.
    assert plugin._inbox is None
    assert target.read_text(encoding="utf-8") == newer
    assert legacy.is_file()
    assert [level for level, _msg, _args in plugin.logger.lines] == ["warning", "warning"]
    assert _SECRET not in plugin.logger.text()

    plugin._init_inbox()

    assert [m["id"] for m in plugin._inbox.pending()] == ["newer001"]
    assert not legacy.exists()


def test_pending_migration_without_legacy_file_opens_the_inbox(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The legacy file vanished between the migration attempt and the read:
    # the target is known not to exist, so the inbox opens as usual.
    monkeypatch.setattr(companion, "_migrate_legacy_inbox", lambda *_args: companion._MIGRATION_PENDING)

    plugin._init_inbox()

    assert plugin._inbox is not None
    assert not [line for line in plugin.logger.lines if line[0] == "warning"]


def test_init_failure_logs_error_type_only(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    def cleanup(self: InboxQueue, max_age_hours: int = 48) -> None:
        raise ValueError(f"Invalid isoformat string: {_SECRET!r}")

    monkeypatch.setattr(InboxQueue, "cleanup", cleanup)

    plugin._init_inbox()

    assert plugin._inbox is None
    assert plugin.logger.lines == [("warning", "Failed to initialize inbox: {}", ("ValueError",))]
    assert _SECRET not in plugin.logger.text()


def test_unreadable_inbox_after_move_is_left_untouched(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    original = legacy.read_bytes()
    target = plugin.data_path("inbox.json")
    real_open = open

    def guarded_open(file: object, *args: object, **kwargs: object):
        # Like a mode-000 file: renaming works (directory permissions), reading does not.
        if Path(file) == target and (not args or "r" in str(args[0])):
            raise PermissionError("denied")
        return real_open(file, *args, **kwargs)

    with monkeypatch.context() as patched:
        patched.setattr(companion, "open", guarded_open, raising=False)
        plugin._init_inbox()

    # The move succeeded but the file cannot be read: the inbox stays closed
    # instead of saving an empty queue over the only copy of the messages.
    assert plugin._inbox is None
    assert not legacy.exists()
    assert target.read_bytes() == original
    assert plugin.logger.lines[-1] == ("warning", "Failed to initialize inbox: {}", ("PermissionError",))

    plugin._init_inbox()

    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_legacy_check_error_keeps_existing_data_inbox(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    newer = json.dumps([dict(_legacy_payload()[0], id="newer001", content="newer")])
    target.write_text(newer, encoding="utf-8")
    real_is_file = Path.is_file

    def is_file(self: Path) -> bool:
        if self == legacy:
            raise PermissionError("denied")
        return real_is_file(self)

    with monkeypatch.context() as patched:
        patched.setattr(Path, "is_file", is_file)
        plugin._init_inbox()

    # The legacy check failed before the target was looked at: its state is
    # unknown, so nothing is written.
    assert plugin._inbox is None
    assert target.read_text(encoding="utf-8") == newer
    assert legacy.is_file()


@pytest.mark.parametrize(
    "payload",
    [
        "{}",
        '["x"]',
        '[{"id": "a"}]',
        '[{"id": "a", "status": "read", "timestamp": ""}]',
        '[{"id": "a", "status": "read", "timestamp": 1700000000}]',
        '[{"id": 1, "status": "pending", "timestamp": "2026-11-23T10:00:00+08:00"}]',
    ],
)
def test_data_inbox_that_is_not_a_message_list_counts_as_empty(
    plugin: ClaudeCompanionPlugin, payload: str
) -> None:
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    target.write_text(payload, encoding="utf-8")

    plugin._init_inbox()

    assert plugin._inbox is not None
    assert plugin._inbox.pending() == []


def test_unusable_entries_are_dropped_and_good_ones_kept(plugin: ClaudeCompanionPlugin) -> None:
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    bad = dict(_legacy_payload()[0], id="bad00001", status="read", timestamp="not a time")
    target.write_text(json.dumps([bad] + _legacy_payload()), encoding="utf-8")

    plugin._init_inbox()

    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_request_labels_are_cut_short_in_the_log(plugin: ClaudeCompanionPlugin) -> None:
    plugin._init_inbox()
    plugin.logger.lines.clear()
    source = "s" * 32 + _SECRET

    assert plugin._on_inbox_send({"content": "hi", "source": source})["status"] == "ok"

    assert plugin.logger.lines == [("info", "Inbox: new message from {} ({} chars)", ("s" * 32, 2))]


def test_read_entry_whose_timestamp_cannot_be_converted_is_dropped(plugin: ClaudeCompanionPlugin) -> None:
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    # Parses, but converting it to a POSIX timestamp fails.
    bad = dict(_legacy_payload()[0], id="bad00001", status="read", timestamp="0001-01-01T00:00:00")
    target.write_text(json.dumps([bad] + _legacy_payload()), encoding="utf-8")

    plugin._init_inbox()

    assert plugin._inbox is not None
    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_pending_entry_with_damaged_timestamp_is_kept(plugin: ClaudeCompanionPlugin) -> None:
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    damaged = dict(_legacy_payload()[0], timestamp="not a time")
    target.write_text(json.dumps([damaged]), encoding="utf-8")

    plugin._init_inbox()

    assert [m["id"] for m in plugin._inbox.pending()] == ["abc12345"]


def test_copy_keeps_legacy_until_directory_is_synced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = tmp_path / "install" / "inbox.json"
    target = tmp_path / "data" / "inbox.json"
    _write_legacy(legacy)

    def replace(src: object, dst: object) -> None:
        if Path(src) == legacy:
            raise OSError("cross-device link")
        _REAL_REPLACE(src, dst)

    monkeypatch.setattr(companion.os, "replace", replace)
    monkeypatch.setattr(companion, "_sync_directory", lambda _directory: False)

    assert _migrate_legacy_inbox(legacy, target, _Logger()) == companion._MIGRATION_DONE

    assert json.loads(target.read_text(encoding="utf-8")) == _legacy_payload()
    assert legacy.is_file()


@pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
def test_save_keeps_existing_file_permissions(tmp_path: Path) -> None:
    path = tmp_path / "inbox.json"
    queue = InboxQueue(str(path))
    queue.add("first")
    path.chmod(0o600)

    queue.add("second")

    assert path.stat().st_mode & 0o777 == 0o600


def test_request_labels_cannot_forge_log_lines() -> None:
    assert companion._log_label("neko\nFAKE line\r\x1b[31m") == "neko?FAKE line??[31m"


def test_copy_fallback_works_where_fsync_needs_a_writable_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fcntl = pytest.importorskip("fcntl")
    legacy = tmp_path / "install" / "inbox.json"
    target = tmp_path / "data" / "inbox.json"
    _write_legacy(legacy)
    real_fsync = os.fsync

    def windows_like_fsync(fd: int) -> None:
        # Windows' fsync (FlushFileBuffers) rejects handles opened read-only.
        if fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE == os.O_RDONLY and not os.path.isdir(f"/proc/self/fd/{fd}"):
            raise OSError(9, "Bad file descriptor")
        real_fsync(fd)

    def replace(src: object, dst: object) -> None:
        if Path(src) == legacy:
            raise OSError("cross-device link")
        _REAL_REPLACE(src, dst)

    monkeypatch.setattr(companion.os, "fsync", windows_like_fsync)
    monkeypatch.setattr(companion.os, "replace", replace)
    logger = _Logger()

    assert _migrate_legacy_inbox(legacy, target, logger) == companion._MIGRATION_DONE

    assert json.loads(target.read_text(encoding="utf-8")) == _legacy_payload()
    assert [level for level, _msg, _args in logger.lines] == ["info"]
