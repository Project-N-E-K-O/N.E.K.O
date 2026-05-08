"""Regression test for ``_list_plugin_log_files_for_tail`` (PR #912 codex).

Why this test exists
--------------------
RobustLoggerConfig opens ``N.E.K.O_<Service>_error.log`` as a separate
file handler that only receives ERROR/CRITICAL events. Its mtime can be
*newer* than the daily ``N.E.K.O_<Service>_YYYYMMDD.log`` file because
the most recent error happened at, say, 21:00 while today's regular
INFO traffic stopped at 20:30.

If "latest log file" selection just sorts ``log_dir.glob("N.E.K.O_..._*.log")``
by mtime, it picks the error log — and the user's tail / WebSocket feed
silently drops to "errors only", with no INFO/DEBUG visible at all.

The shared helper ``_list_plugin_log_files_for_tail`` exists exactly so
``get_plugin_logs`` / ``LogFileWatcher._watch_loop`` /
``LogFileWatcher.send_initial_logs`` can't drift apart on this rule.
"""
from __future__ import annotations

import os
import time

import pytest

from plugin.server.logs import (
    SERVER_LOG_ID,
    _is_error_log_file,
    _list_plugin_log_files_for_tail,
)


@pytest.mark.plugin_unit
def test_is_error_log_file_matches_plain_and_rotated() -> None:
    # Plain error file
    assert _is_error_log_file("N.E.K.O_PluginServer_error.log")
    assert _is_error_log_file("N.E.K.O_Plugin_demo_error.log")
    # Rotation suffixes (RotatingFileHandler / TimedRotatingFileHandler)
    assert _is_error_log_file("N.E.K.O_PluginServer_error.log.1")
    assert _is_error_log_file("N.E.K.O_PluginServer_error.log.2026-04-21")
    # Regular daily files must NOT be flagged
    assert not _is_error_log_file("N.E.K.O_PluginServer_20260421.log")
    assert not _is_error_log_file("N.E.K.O_Plugin_demo_20260421.log")
    assert not _is_error_log_file("N.E.K.O_PluginServer_20260421.log.1")


@pytest.mark.plugin_unit
def test_list_plugin_log_files_for_tail_excludes_error_log_even_when_newer(tmp_path) -> None:
    # Daily log first (older mtime).
    daily = tmp_path / "N.E.K.O_Plugin_demo_20260420.log"
    daily.write_text("regular\n", encoding="utf-8")
    older = time.time() - 60
    os.utime(daily, (older, older))

    # Error log second (newer mtime — this is the regression case).
    err = tmp_path / "N.E.K.O_Plugin_demo_error.log"
    err.write_text("ERROR\n", encoding="utf-8")
    # leave default mtime (now) — newer than daily

    files = _list_plugin_log_files_for_tail(tmp_path, "demo")

    assert daily in files
    assert err not in files, (
        "tail/WebSocket selector picked _error.log because its mtime was "
        "newer; users would have only seen ERROR rows, no INFO/DEBUG."
    )
    # Newest-first order is the contract callers depend on.
    assert files == [daily]


@pytest.mark.plugin_unit
def test_list_plugin_log_files_for_tail_includes_rotated_regular_logs(tmp_path) -> None:
    """RotatingFileHandler 触发轮转后只剩 ``.log.1`` / ``.log.2026-04-21`` 这种
    后缀的文件。如果 helper 的 glob 只匹配 ``*.log``，目录里只剩轮转后的常规
    日志时会返回空，让 tail / WebSocket 误报 "无日志"。
    """
    daily = tmp_path / "N.E.K.O_Plugin_demo_20260421.log"
    daily.write_text("today\n", encoding="utf-8")
    rotated_size = tmp_path / "N.E.K.O_Plugin_demo_20260420.log.1"
    rotated_size.write_text("yesterday-size\n", encoding="utf-8")
    rotated_time = tmp_path / "N.E.K.O_Plugin_demo_20260419.log.2026-04-19"
    rotated_time.write_text("two-days-ago\n", encoding="utf-8")

    # Error rotations must STILL be excluded even with the wider glob.
    err_rotated = tmp_path / "N.E.K.O_Plugin_demo_error.log.3"
    err_rotated.write_text("err-rotated\n", encoding="utf-8")

    files = _list_plugin_log_files_for_tail(tmp_path, "demo")

    assert daily in files
    assert rotated_size in files, "size-based rotation suffix .log.1 not picked up"
    assert rotated_time in files, "time-based rotation suffix .log.YYYY-MM-DD not picked up"
    assert err_rotated not in files, (
        "error-log rotation must still be excluded even after the glob widening."
    )


@pytest.mark.plugin_unit
def test_list_plugin_log_files_for_tail_server_id_uses_pluginserver_pattern(tmp_path) -> None:
    server_daily = tmp_path / "N.E.K.O_PluginServer_20260421.log"
    server_daily.write_text("server\n", encoding="utf-8")
    server_err = tmp_path / "N.E.K.O_PluginServer_error.log"
    server_err.write_text("server-err\n", encoding="utf-8")
    plugin_unrelated = tmp_path / "N.E.K.O_Plugin_other_20260421.log"
    plugin_unrelated.write_text("nope\n", encoding="utf-8")

    files = _list_plugin_log_files_for_tail(tmp_path, SERVER_LOG_ID)

    assert files == [server_daily]
