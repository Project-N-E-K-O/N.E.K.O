from __future__ import annotations

import os
import shutil
import subprocess
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
@pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
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
