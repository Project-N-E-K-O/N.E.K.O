import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "probe_widget_mode.py"
SPEC = importlib.util.spec_from_file_location("probe_widget_mode", SCRIPT_PATH)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def clean_state() -> dict:
    return {
        "enabled": False,
        "compaction_phase": "idle",
        "compaction_cycle_id": None,
        "user_restore_active": False,
        "suppressed_until": None,
    }


def test_probe_accepts_clean_default_off_and_debug_health():
    responses = {
        "/api/widget-mode/state": {
            "ok": True,
            "status": 200,
            "data": {"success": True, "state": clean_state()},
        },
        "/api/debug/health": {
            "ok": True,
            "status": 200,
            "data": {"widget_mode": clean_state()},
        },
    }

    def fetcher(_base_url, path, _timeout):
        return responses[path]

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            expect_default_off=True,
            check_debug_health=True,
        ),
        fetcher=fetcher,
    )

    assert report["ok"] is True
    assert report["failures"] == []
    assert "enabled=False" in report["summary"]["widget_mode_state"]
    assert "compaction_phase=idle" in report["summary"]["widget_mode_state"]
    assert report["summary"]["debug_health_widget_mode_present"] is True


def test_probe_auto_discovers_backend_and_reuses_selected_base_url(monkeypatch):
    monkeypatch.delenv("NEKO_WIDGET_MODE_PROBE_URL", raising=False)
    monkeypatch.delenv("NEKO_BACKEND_URL", raising=False)
    monkeypatch.delenv("NEKO_ORIGINAL_URL", raising=False)
    calls = []

    def fetcher(base_url, path, _timeout):
        calls.append((base_url, path))
        if base_url == "http://127.0.0.1:48912" and path == "/api/widget-mode/state":
            return {"ok": True, "status": 200, "data": {"success": True, "state": clean_state()}}
        if base_url == "http://127.0.0.1:48912" and path == "/api/debug/health":
            return {"ok": True, "status": 200, "data": {"widget_mode": clean_state()}}
        return {"ok": False, "status": None, "error": "connection refused"}

    report = probe.run_probe(
        probe.ProbeOptions(base_url="auto", expect_default_off=True, check_debug_health=True),
        fetcher=fetcher,
    )

    assert report["ok"] is True
    assert report["summary"]["selected_base_url"] == "http://127.0.0.1:48912"
    assert ("http://127.0.0.1:48911", "/api/widget-mode/state") in calls
    assert ("http://127.0.0.1:48912", "/api/debug/health") in calls


def test_probe_parse_args_defaults_to_auto_base_url():
    assert probe.parse_args([]).base_url == "auto"


def test_summarize_state_reports_cycle_and_suppression():
    state = clean_state() | {"compaction_cycle_id": "cycle-1", "suppressed_until": 1234.5}

    summary = probe.summarize_state(state)

    assert "compaction_cycle_id=cycle-1" in summary
    assert "suppressed_until=1234.5" in summary


def test_probe_reports_restart_default_off_failures():
    def fetcher(_base_url, path, _timeout):
        assert path == "/api/widget-mode/state"
        state = clean_state() | {
            "enabled": True,
            "compaction_phase": "compacted",
        }
        return {"ok": True, "status": 200, "data": {"success": True, "state": state}}

    report = probe.run_probe(
        probe.ProbeOptions(base_url="http://127.0.0.1:48911", expect_default_off=True),
        fetcher=fetcher,
    )

    assert report["ok"] is False
    assert "expected enabled=false after restart" in report["failures"]
    assert "expected compaction_phase=idle after restart" in report["failures"]


def test_probe_reports_missing_debug_health_widget_mode():
    responses = {
        "/api/widget-mode/state": {
            "ok": True,
            "status": 200,
            "data": {"success": True, "state": clean_state()},
        },
        "/api/debug/health": {"ok": True, "status": 200, "data": {"uptime_seconds": 12}},
    }

    report = probe.run_probe(
        probe.ProbeOptions(base_url="http://127.0.0.1:48911", check_debug_health=True),
        fetcher=lambda _base_url, path, _timeout: responses[path],
    )

    assert report["ok"] is False
    assert report["failures"] == ["debug health response missing widget_mode"]
    assert report["summary"]["debug_health_widget_mode_present"] is False


def test_probe_inspects_debug_health_log_with_rotation_summary(tmp_path):
    log_path = tmp_path / "debug_health.jsonl"
    rotated_path = tmp_path / "debug_health.jsonl.1"
    log_path.write_text(
        '{"ts": 1, "uptime_seconds": 3}\n'
        '{"ts": 2, "widget_mode": {"enabled": false, "compaction_phase": "idle"}}\n',
        encoding="utf-8",
    )
    rotated_path.write_text("old\n", encoding="utf-8")

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            debug_health_log_path=str(log_path),
        ),
        fetcher=lambda _base_url, _path, _timeout: {
            "ok": True,
            "status": 200,
            "data": {"success": True, "state": clean_state()},
        },
    )

    log_info = report["summary"]["debug_health_log"]
    assert report["ok"] is True
    assert log_info["exists"] is True
    assert log_info["line_count"] == 2
    assert log_info["latest_has_widget_mode"] is True
    assert log_info["rotated_exists"] is True
    assert log_info["rotated_size_bytes"] == len(rotated_path.read_bytes())


def test_probe_reports_missing_debug_health_log(tmp_path):
    missing_log_path = tmp_path / "missing_debug_health.jsonl"

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            debug_health_log_path=str(missing_log_path),
        ),
        fetcher=lambda _base_url, path, _timeout: {
            "ok": path == "/api/widget-mode/state",
            "status": 200,
            "data": {"success": True, "state": clean_state()},
        },
    )

    assert report["ok"] is False
    assert report["failures"] == [f"debug health log missing: {missing_log_path}"]
