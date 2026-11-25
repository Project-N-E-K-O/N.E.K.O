from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
SURVEY_SETUP = ROOT / "local_server" / "survey_server" / "deploy" / "setup.sh"
TELEMETRY_SETUP = ROOT / "local_server" / "telemetry_server" / "deploy" / "setup.sh"


def _extract_service_start_block(script_path: Path) -> str:
    text = script_path.read_text(encoding="utf-8")
    start_idx = text.index("# 启动/重启")
    end_idx = text.index('echo "========================================="', start_idx)
    return text[start_idx:end_idx]


@pytest.mark.unit
@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="setup.sh targets Linux/systemd; Windows 'bash' is usually the WSL launcher",
)
@pytest.mark.parametrize(
    ("script_path", "service_name"),
    [
        (SURVEY_SETUP, "neko-survey"),
        (TELEMETRY_SETUP, "neko-telemetry"),
    ],
)
def test_deploy_setup_restarts_service_and_polls_health(
    tmp_path: Path, script_path: Path, service_name: str
):
    """F0139/F0142: setup.sh must restart an already-active unit so updated server.py and .env take effect."""
    text = script_path.read_text(encoding="utf-8")
    assert "enable --now" not in text

    syntax = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    systemctl_log = tmp_path / "systemctl.log"
    curl_count = tmp_path / "curl.count"

    sudo_bin = bin_dir / "sudo"
    sudo_bin.write_text('#!/usr/bin/env bash\nexec "$@"\n', encoding="utf-8")
    sudo_bin.chmod(0o755)

    systemctl_bin = bin_dir / "systemctl"
    systemctl_bin.write_text(
        "#!/usr/bin/env bash\n"
        'echo "$*" >> "$SYSTEMCTL_LOG"\n'
        'if [ "$1" = "is-active" ]; then exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    systemctl_bin.chmod(0o755)

    curl_bin = bin_dir / "curl"
    curl_bin.write_text(
        "#!/usr/bin/env bash\n"
        'n=0; [ -f "$CURL_COUNT" ] && n=$(cat "$CURL_COUNT")\n'
        "n=$((n + 1))\n"
        'echo "$n" > "$CURL_COUNT"\n'
        "if [ \"$n\" -lt 2 ]; then exit 7; fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    curl_bin.chmod(0o755)

    sleep_bin = bin_dir / "sleep"
    sleep_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep_bin.chmod(0o755)

    block = _extract_service_start_block(script_path)
    harness = (
        "set -e\n"
        f"SERVICE_NAME={service_name}\n"
        "PORT=18999\n"
        f"{block}\n"
    )
    env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "SYSTEMCTL_LOG": str(systemctl_log),
        "CURL_COUNT": str(curl_count),
    }
    res = subprocess.run(["bash", "-c", harness], env=env, capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "Server is running on port 18999" in res.stdout

    calls = systemctl_log.read_text(encoding="utf-8").splitlines()
    assert calls[:3] == [
        "daemon-reload",
        f"enable {service_name}",
        f"restart {service_name}",
    ]
    assert any(line == f"is-active --quiet {service_name}" for line in calls[3:])
    assert int(curl_count.read_text(encoding="utf-8").strip()) == 2


@pytest.mark.unit
@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="setup.sh targets Linux/systemd; Windows 'bash' is usually the WSL launcher",
)
@pytest.mark.parametrize(
    ("script_path", "service_name"),
    [
        (SURVEY_SETUP, "neko-survey"),
        (TELEMETRY_SETUP, "neko-telemetry"),
    ],
)
def test_deploy_setup_retains_old_venvs_on_health_failure_and_cleans_on_success(
    tmp_path: Path, script_path: Path, service_name: str
):
    """F0565: setup.sh must retain legacy/old virtualenvs if /health fails and remove them only after HEALTH_OK=1."""
    syntax = subprocess.run(["bash", "-n", str(script_path)], capture_output=True, text=True)
    assert syntax.returncode == 0, syntax.stderr

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    sudo_bin = bin_dir / "sudo"
    sudo_bin.write_text('#!/usr/bin/env bash\nexec "$@"\n', encoding="utf-8")
    sudo_bin.chmod(0o755)

    systemctl_bin = bin_dir / "systemctl"
    systemctl_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    systemctl_bin.chmod(0o755)

    sleep_bin = bin_dir / "sleep"
    sleep_bin.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    sleep_bin.chmod(0o755)

    curl_count = tmp_path / "curl.count"
    curl_bin = bin_dir / "curl"
    curl_bin.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${FAIL_HEALTH:-0}" = "1" ]; then exit 7; fi\n'
        'n=0; [ -f "$CURL_COUNT" ] && n=$(cat "$CURL_COUNT")\n'
        "n=$((n + 1))\n"
        'echo "$n" > "$CURL_COUNT"\n'
        'if [ "$n" -lt 2 ]; then exit 7; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    curl_bin.chmod(0o755)

    def _prepare_install_dir(name: str) -> tuple[Path, Path, Path, Path, Path]:
        install_dir = tmp_path / name
        venvs_dir = install_dir / "venvs"
        old_venv = venvs_dir / "venv-old"
        new_venv = venvs_dir / "venv-new"
        legacy_venv = install_dir / "venv.legacy.1234"
        live_link = install_dir / "venv"
        for d in (old_venv, new_venv, legacy_venv):
            d.mkdir(parents=True)
        live_link.symlink_to(new_venv)
        return install_dir, old_venv, new_venv, legacy_venv, live_link

    block = _extract_service_start_block(script_path)

    # Case A: health probes always fail -> previous virtualenvs must stay intact for rollback.
    fail_install, fail_old, fail_new, fail_legacy, fail_link = _prepare_install_dir("fail_case")
    fail_harness = (
        "set -e\n"
        f"SERVICE_NAME={service_name}\n"
        "PORT=18999\n"
        f'INSTALL_DIR="{fail_install}"\n'
        f'ACTIVE_VENV="{fail_new}"\n'
        f"{block}\n"
    )
    fail_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "FAIL_HEALTH": "1",
        "CURL_COUNT": str(curl_count),
    }
    fail_res = subprocess.run(["bash", "-c", fail_harness], env=fail_env, capture_output=True, text=True)
    assert fail_res.returncode == 0, fail_res.stderr
    assert "previous environments retained" in fail_res.stdout
    assert fail_legacy.is_dir()
    assert fail_old.is_dir()
    assert fail_new.is_dir()
    assert fail_link.is_symlink()

    # Case B: health check passes -> old/legacy virtualenvs are cleaned while ACTIVE_VENV & symlink remain.
    ok_install, ok_old, ok_new, ok_legacy, ok_link = _prepare_install_dir("ok_case")
    ok_harness = (
        "set -e\n"
        f"SERVICE_NAME={service_name}\n"
        "PORT=18999\n"
        f'INSTALL_DIR="{ok_install}"\n'
        f'ACTIVE_VENV="{ok_new}"\n'
        f"{block}\n"
    )
    ok_env = {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
        "FAIL_HEALTH": "0",
        "CURL_COUNT": str(curl_count),
    }
    ok_res = subprocess.run(["bash", "-c", ok_harness], env=ok_env, capture_output=True, text=True)
    assert ok_res.returncode == 0, ok_res.stderr
    assert "Server is running on port 18999" in ok_res.stdout
    assert not ok_legacy.exists()
    assert not ok_old.exists()
    assert ok_new.is_dir()
    assert ok_link.is_symlink() and ok_link.resolve() == ok_new.resolve()
