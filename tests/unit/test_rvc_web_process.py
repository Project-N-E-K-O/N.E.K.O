from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from plugin.plugins.rvc_cover.rvc_web_process import RvcWebConfig, RvcWebProcessManager
from plugin.plugins.rvc_cover.service import settings_from_mapping


class FakeProc:
    def __init__(self) -> None:
        self.pid = 4242
        self._alive = True
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self.terminated = True
        self._alive = False

    def kill(self) -> None:
        self.killed = True
        self._alive = False

    def wait(self, timeout=None):
        self._alive = False
        return 0


def test_settings_include_web_flags() -> None:
    settings = settings_from_mapping({"auto_start_web": False, "web_port": 7901})
    assert settings.auto_start_web is False
    assert settings.web_port == 7901


def test_web_manager_attaches_external(tmp_path: Path) -> None:
    calls = {"health": 0, "spawn": 0}

    def health(_url: str, _timeout: float) -> bool:
        calls["health"] += 1
        return True

    def popen(*_a, **_k):
        calls["spawn"] += 1
        raise AssertionError("should not spawn when external healthy")

    mgr = RvcWebProcessManager(
        RvcWebConfig(
            rvc_root=tmp_path,
            python_path=tmp_path / "python.exe",
            port=7897,
            auto_start=True,
            log_dir=tmp_path / "logs",
        ),
        health_check=health,
        popen_factory=popen,
        sleep=lambda _t: None,
    )
    snap = mgr.start_if_needed()
    assert snap["mode"] == "external"
    assert snap["started_by_plugin"] is False
    assert calls["spawn"] == 0
    stop = mgr.stop()
    assert stop["mode"] == "external"


def test_web_manager_starts_and_stops_owned(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "infer-web.py").write_text("# fake", encoding="utf-8")
    py = tmp_path / "python.exe"
    py.write_bytes(b"x")
    healthy = {"ok": False}
    proc = FakeProc()

    def health(_url: str, _timeout: float) -> bool:
        return bool(healthy["ok"])

    def popen(cmd, **kwargs):
        assert "infer-web.py" in " ".join(str(x) for x in cmd)
        assert kwargs.get("cwd") == str(tmp_path)
        healthy["ok"] = True
        return proc

    kills: list[int] = []

    def fake_kill_tree(pid):
        kills.append(pid)
        proc._alive = False

    mgr = RvcWebProcessManager(
        RvcWebConfig(
            rvc_root=tmp_path,
            python_path=py,
            port=7897,
            auto_start=True,
            startup_timeout_seconds=1,
            log_dir=tmp_path / "logs",
        ),
        health_check=health,
        popen_factory=popen,
        sleep=lambda _t: None,
    )
    monkeypatch.setattr(mgr, "_kill_process_tree", fake_kill_tree)

    start = mgr.start_if_needed()
    assert start["mode"] == "managed"
    assert start["started_by_plugin"] is True
    assert start["pid"] == 4242
    assert start["health"] is True

    stop = mgr.stop()
    assert stop["mode"] == "stopped"
    assert stop["started_by_plugin"] is False
    assert kills == [4242]


def test_web_manager_disabled_skips_spawn(tmp_path: Path) -> None:
    def health(_url: str, _timeout: float) -> bool:
        return False

    def popen(*_a, **_k):
        raise AssertionError("disabled must not spawn")

    mgr = RvcWebProcessManager(
        RvcWebConfig(
            rvc_root=tmp_path,
            python_path=tmp_path / "python.exe",
            auto_start=False,
            log_dir=tmp_path / "logs",
        ),
        health_check=health,
        popen_factory=popen,
        sleep=lambda _t: None,
    )
    snap = mgr.start_if_needed()
    assert snap["mode"] == "disabled"
