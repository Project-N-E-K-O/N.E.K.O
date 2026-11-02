"""Test log-export file filtering."""
from pathlib import Path

import pytest

from plugin.server.logs import list_plugin_log_files_for_export


def test_export_filter_prevents_prefix_leak(tmp_path: Path):
    """Plugin ID prefixes must not cause cross-plugin exports."""
    # Create logs for two plugins: foo and foo_bar.
    (tmp_path / "N.E.K.O_Plugin_foo_20260906.log").touch()
    (tmp_path / "N.E.K.O_Plugin_foo_error.log").touch()
    (tmp_path / "N.E.K.O_Plugin_foo_bar_20260906.log").touch()
    (tmp_path / "N.E.K.O_Plugin_foo_bar_error.log").touch()

    # Exporting foo must not include foo_bar.
    foo_files = list_plugin_log_files_for_export(tmp_path, "foo")
    foo_names = {f.name for f in foo_files}

    assert "N.E.K.O_Plugin_foo_20260906.log" in foo_names
    assert "N.E.K.O_Plugin_foo_error.log" in foo_names
    assert "N.E.K.O_Plugin_foo_bar_20260906.log" not in foo_names
    assert "N.E.K.O_Plugin_foo_bar_error.log" not in foo_names


def test_export_filter_includes_rotated_logs(tmp_path: Path):
    """Export rotated logs and error logs."""
    (tmp_path / "N.E.K.O_Plugin_demo_20260906.log").touch()
    (tmp_path / "N.E.K.O_Plugin_demo_20260905.log.1").touch()  # RotatingFileHandler numeric suffix.
    (tmp_path / "N.E.K.O_Plugin_demo_20260904.log.2026-09-04").touch()  # TimedRotatingFileHandler date suffix.
    (tmp_path / "N.E.K.O_Plugin_demo_error.log").touch()
    (tmp_path / "N.E.K.O_Plugin_demo_error.log.2").touch()  # RotatingFileHandler numeric suffix.
    (tmp_path / "N.E.K.O_Plugin_demo_error.log.2026-09-03").touch()  # TimedRotatingFileHandler date suffix.

    files = list_plugin_log_files_for_export(tmp_path, "demo")
    names = {f.name for f in files}

    assert len(names) == 6
    assert "N.E.K.O_Plugin_demo_20260906.log" in names
    assert "N.E.K.O_Plugin_demo_20260905.log.1" in names
    assert "N.E.K.O_Plugin_demo_20260904.log.2026-09-04" in names
    assert "N.E.K.O_Plugin_demo_error.log" in names
    assert "N.E.K.O_Plugin_demo_error.log.2" in names
    assert "N.E.K.O_Plugin_demo_error.log.2026-09-03" in names


def test_export_filter_uses_sanitized_id(tmp_path: Path):
    """Match long IDs using the sanitized filename."""
    # Simulate a long ID with a truncated hash.
    long_id = "a" * 70  # Exceeds the 64-character limit.
    from plugin.core.host import _sanitize_plugin_id
    safe_id = _sanitize_plugin_id(long_id, max_len=64)

    # Create a log file using safe_id.
    (tmp_path / f"N.E.K.O_Plugin_{safe_id}_20260906.log").touch()

    # Querying with the original long_id must find it.
    files = list_plugin_log_files_for_export(tmp_path, long_id)
    assert len(files) == 1
    assert files[0].name == f"N.E.K.O_Plugin_{safe_id}_20260906.log"


def test_export_filter_rejects_invalid_suffix(tmp_path: Path):
    """Reject files that violate log-naming conventions."""
    (tmp_path / "N.E.K.O_Plugin_demo_20260906.log").touch()
    (tmp_path / "N.E.K.O_Plugin_demo_badfile.txt").touch()  # Invalid naming format.
    (tmp_path / "N.E.K.O_Plugin_demo_20260906").touch()  # Missing .log.
    # Passes the initial *.log* filter but fails the secondary filter.
    (tmp_path / "N.E.K.O_Plugin_demo_20260906X.log").touch()  # Invalid characters after the date.
    (tmp_path / "N.E.K.O_Plugin_demo_errorX.log").touch()  # Invalid characters after error.
    (tmp_path / "N.E.K.O_Plugin_demo_20260906.log.tmp").touch()  # Nonnumeric suffix after .log.
    (tmp_path / "N.E.K.O_Plugin_demo_error.logX").touch()  # Nonnumeric suffix after .log.

    files = list_plugin_log_files_for_export(tmp_path, "demo")
    names = {f.name for f in files}

    assert len(names) == 1
    assert "N.E.K.O_Plugin_demo_20260906.log" in names
    assert "N.E.K.O_Plugin_demo_badfile.txt" not in names
    assert "N.E.K.O_Plugin_demo_20260906" not in names
    assert "N.E.K.O_Plugin_demo_20260906X.log" not in names
    assert "N.E.K.O_Plugin_demo_errorX.log" not in names
    assert "N.E.K.O_Plugin_demo_20260906.log.tmp" not in names
    assert "N.E.K.O_Plugin_demo_error.logX" not in names
