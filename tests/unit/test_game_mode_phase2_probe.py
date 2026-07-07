import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "probe_game_mode_beta_phase2.py"
SPEC = importlib.util.spec_from_file_location("probe_game_mode_beta_phase2", SCRIPT_PATH)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = probe
SPEC.loader.exec_module(probe)


def test_phase2_probe_accepts_clean_default_off_and_debug_health():
    responses = {
        "/api/game-mode-beta/state": {
            "ok": True,
            "status": 200,
            "data": {
                "success": True,
                "state": {
                    "enabled": False,
                    "pressure_state": "normal",
                    "trigger_reason": None,
                    "auto_switch_active": False,
                    "manual_override": False,
                    "suppressed_until": None,
                },
            },
        },
        "/api/debug/health": {
            "ok": True,
            "status": 200,
            "data": {
                "game_mode_beta": {
                    "enabled": False,
                    "pressure_state": "normal",
                    "last_samples": [],
                    "trigger_reason": None,
                    "suppressed_until": None,
                },
            },
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
    assert "enabled=False" in report["summary"]["game_mode_state"]
    assert "pressure_state=normal" in report["summary"]["game_mode_state"]
    assert report["summary"]["debug_health_game_mode_present"] is True


def test_phase2_probe_auto_discovers_backend_and_reuses_selected_base_url(monkeypatch):
    monkeypatch.delenv("NEKO_GAME_MODE_PROBE_URL", raising=False)
    monkeypatch.delenv("NEKO_BACKEND_URL", raising=False)
    monkeypatch.delenv("NEKO_ORIGINAL_URL", raising=False)

    calls = []

    def fetcher(base_url, path, _timeout):
        calls.append((base_url, path))
        if base_url == "http://127.0.0.1:48912" and path == "/api/game-mode-beta/state":
            return {
                "ok": True,
                "status": 200,
                "data": {"success": True, "state": {"enabled": False, "pressure_state": "normal", "trigger_reason": None}},
            }
        if base_url == "http://127.0.0.1:48912" and path == "/api/debug/health":
            return {
                "ok": True,
                "status": 200,
                "data": {"game_mode_beta": {"enabled": False, "pressure_state": "normal", "last_samples": []}},
            }
        return {"ok": False, "status": None, "error": "connection refused"}

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="auto",
            expect_default_off=True,
            check_debug_health=True,
        ),
        fetcher=fetcher,
    )

    assert report["ok"] is True
    assert report["summary"]["selected_base_url"] == "http://127.0.0.1:48912"
    assert ("http://127.0.0.1:48911", "/api/game-mode-beta/state") in calls
    assert ("http://127.0.0.1:48912", "/api/debug/health") in calls


def test_phase2_probe_parse_args_defaults_to_auto_base_url():
    options = probe.parse_args([])

    assert options.base_url == "auto"


def test_phase2_probe_reports_restart_default_off_failures():
    def fetcher(_base_url, path, _timeout):
        assert path == "/api/game-mode-beta/state"
        return {
            "ok": True,
            "status": 200,
            "data": {
                "success": True,
                "state": {
                    "enabled": True,
                    "pressure_state": "protected",
                    "trigger_reason": {"metric": "gpu", "percent": 91, "duration_seconds": 30},
                },
            },
        }

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            expect_default_off=True,
        ),
        fetcher=fetcher,
    )

    assert report["ok"] is False
    assert "expected enabled=false after restart" in report["failures"]
    assert "expected pressure_state=normal after restart" in report["failures"]
    assert "expected trigger_reason=null after restart" in report["failures"]


def test_phase2_probe_reports_missing_debug_health_game_mode():
    responses = {
        "/api/game-mode-beta/state": {
            "ok": True,
            "status": 200,
            "data": {"success": True, "state": {"enabled": False, "pressure_state": "normal", "trigger_reason": None}},
        },
        "/api/debug/health": {
            "ok": True,
            "status": 200,
            "data": {"uptime_seconds": 12},
        },
    }

    def fetcher(_base_url, path, _timeout):
        return responses[path]

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            check_debug_health=True,
        ),
        fetcher=fetcher,
    )

    assert report["ok"] is False
    assert report["failures"] == ["debug health response missing game_mode_beta"]
    assert report["summary"]["debug_health_game_mode_present"] is False


def test_phase2_probe_inspects_debug_health_log_with_rotation_summary(tmp_path):
    log_path = tmp_path / "debug_health.jsonl"
    rotated_path = tmp_path / "debug_health.jsonl.1"
    log_path.write_text(
        '{"ts": 1, "uptime_seconds": 3}\n'
        '{"ts": 2, "game_mode_beta": {"enabled": false, "pressure_state": "normal"}}\n',
        encoding="utf-8",
    )
    rotated_payload = "old\n"
    rotated_path.write_text(rotated_payload, encoding="utf-8")

    responses = {
        "/api/game-mode-beta/state": {
            "ok": True,
            "status": 200,
            "data": {"success": True, "state": {"enabled": False, "pressure_state": "normal", "trigger_reason": None}},
        },
    }

    def fetcher(_base_url, path, _timeout):
        return responses[path]

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            debug_health_log_path=str(log_path),
        ),
        fetcher=fetcher,
    )

    log_info = report["summary"]["debug_health_log"]
    assert report["ok"] is True
    assert log_info["exists"] is True
    assert log_info["line_count"] == 2
    assert log_info["latest_has_game_mode_beta"] is True
    assert log_info["rotated_exists"] is True
    assert log_info["rotated_size_bytes"] == len(rotated_path.read_bytes())


def test_phase2_probe_reports_missing_debug_health_log(tmp_path):
    missing_log_path = tmp_path / "missing_debug_health.jsonl"

    def fetcher(_base_url, path, _timeout):
        assert path == "/api/game-mode-beta/state"
        return {
            "ok": True,
            "status": 200,
            "data": {"success": True, "state": {"enabled": False, "pressure_state": "normal", "trigger_reason": None}},
        }

    report = probe.run_probe(
        probe.ProbeOptions(
            base_url="http://127.0.0.1:48911",
            debug_health_log_path=str(missing_log_path),
        ),
        fetcher=fetcher,
    )

    assert report["ok"] is False
    assert report["failures"] == [f"debug health log missing: {missing_log_path}"]
