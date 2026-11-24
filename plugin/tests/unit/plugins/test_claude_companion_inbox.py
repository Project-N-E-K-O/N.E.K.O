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


def test_existing_data_inbox_wins_over_legacy(plugin: ClaudeCompanionPlugin) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)
    target = plugin.data_path("inbox.json")
    target.parent.mkdir(parents=True)
    target.write_text("[]", encoding="utf-8")

    plugin._init_inbox()

    assert legacy.is_file()
    assert plugin._inbox.pending() == []


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

    assert _migrate_legacy_inbox(legacy, target, logger) is True

    assert json.loads(target.read_text(encoding="utf-8")) == _legacy_payload()
    assert legacy.is_file()  # could not be removed, left in place
    assert not (tmp_path / "data" / "inbox.json.migrating").exists()
    assert [level for level, _msg, _args in logger.lines] == ["info"]
    assert _SECRET not in logger.text()


def _fail(*_args: object, **_kwargs: object) -> None:
    raise PermissionError("denied")


def _migration_warnings(plugin: ClaudeCompanionPlugin) -> list[tuple[str, tuple]]:
    return [(msg, args) for level, msg, args in plugin.logger.lines if level == "warning"]


def test_failed_migration_keeps_legacy_messages(
    plugin: ClaudeCompanionPlugin, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = companion._LEGACY_INBOX_PATH
    _write_legacy(legacy)

    with monkeypatch.context() as patched:
        patched.setattr(companion.os, "replace", _fail)
        patched.setattr(companion.shutil, "copyfile", _fail)
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
        patched.setattr(companion.os, "replace", _fail)
        patched.setattr(companion.shutil, "copyfile", _fail)
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
        patched.setattr(companion.os, "replace", _fail)
        patched.setattr(companion.shutil, "copyfile", _fail)
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
        patched.setattr(companion.os, "replace", _fail)
        patched.setattr(companion.shutil, "copyfile", _fail)
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
