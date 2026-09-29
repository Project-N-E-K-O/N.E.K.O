from __future__ import annotations

from pathlib import Path
import re

import pytest


ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"

NUITKA_STEPS = (
    ("build-desktop.yml", "Build with Nuitka (Unix)"),
    ("build-desktop.yml", "Build with Nuitka (Windows)"),
    ("build-desktop-linux.yml", "Build with Nuitka"),
)

# Unconditional NUITKA_OPTS appends sit at the run-script base indent; lines
# inside if/else branches are indented further and do not count.
_APPEND = re.compile(
    r'^ {10}(?:NUITKA_OPTS="\$NUITKA_OPTS (?P<bash>[^"]+)"'
    r"|set NUITKA_OPTS=%NUITKA_OPTS% (?P<cmd>.+?))\s*$"
)


def _step_script(workflow: str, step_name: str) -> str:
    text = (WORKFLOWS / workflow).read_text(encoding="utf-8")
    header = f"      - name: {step_name}\n"
    assert text.count(header) == 1, f"{workflow}: step {step_name!r} not found exactly once"
    body = text.split(header, 1)[1]
    return body.split("\n      - name: ", 1)[0]


def _nuitka_flags(script: str) -> list[str]:
    flags: list[str] = []
    for line in script.splitlines():
        match = _APPEND.match(line)
        if match:
            flags.extend((match.group("bash") or match.group("cmd")).split())
    return flags


@pytest.mark.parametrize(("workflow", "step_name"), NUITKA_STEPS)
def test_pyav_is_frozen_from_extension_modules(workflow: str, step_name: str) -> None:
    # PyAV wheels ship Cython pure-python-mode .py files next to some compiled
    # extension modules. Compiling those sources instead of using the .so/.pyd
    # breaks the frozen import (`No module named 'cython'`), and only the
    # nightly Nuitka build would notice.
    script = _step_script(workflow, step_name)
    assert re.search(r"-m nuitka [$%]NUITKA_OPTS", script), "step must invoke Nuitka with NUITKA_OPTS"
    flags = _nuitka_flags(script)

    assert "--include-module=av" in flags
    # --include-package walks the package directory and compiles the shadow .py files.
    assert not [flag for flag in flags if re.fullmatch(r"--include-package=av(\..*)?", flag)]
    # Nuitka marks "extension module vs source" as an undecided default; pin it.
    assert "--no-prefer-source-code" in flags
    assert "--prefer-source-code" not in flags
