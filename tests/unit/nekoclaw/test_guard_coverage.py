"""The repository's static guards really cover the ``nekoclaw`` package.

Several guards work from a list of directories. A new top-level package that
is missing from such a list is simply not checked, and the guard stays green.
These tests pin the registrations and prove each guard still bites inside the
package by feeding it a violating file placed under ``nekoclaw/``.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE = REPO_ROOT / "nekoclaw"
# The guards print characters outside ASCII. With its output on a pipe, a child
# process encodes with the machine's ANSI code page and crashes on them where
# that page lacks the character, so the encoding is fixed here.
_GUARD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_nekoclaw_is_registered_in_every_directory_list() -> None:
    assert "nekoclaw" in _load("check_async_blocking").DEFAULT_PATHS
    assert "nekoclaw" in _load("check_startup_import_lazy").DEFAULT_PATHS
    assert "nekoclaw" in _load("check_no_temperature").DEFAULT_PATHS
    layering = _load("check_module_layering")
    assert layering.PACKAGE_TO_LAYER["nekoclaw"] == layering.PACKAGE_TO_LAYER["brain"]
    forbidden = {package for prefix, package, _why in layering.FORBIDDEN_EDGES if prefix == "nekoclaw/runtime/"}
    # Everything registered at or below the runtime's own layer, except the two
    # packages it is allowed to use.
    own_layer = layering.PACKAGE_TO_LAYER["nekoclaw"]
    reachable = {
        package
        for package, layer in layering.PACKAGE_TO_LAYER.items()
        if layer <= own_layer and package != "nekoclaw"
    }
    assert forbidden == reachable - {"config", "utils"}
    assert "steamworks" in forbidden and "brain" in forbidden


@pytest.mark.parametrize(
    "script",
    [
        "check_async_blocking",
        "check_startup_import_lazy",
        "check_no_temperature",
        "check_module_layering",
        "check_llm_budget",
        "check_prompt_hygiene",
        "check_no_loguru",
        "check_no_tkinter",
    ],
)
def test_the_package_passes_each_guard(script: str) -> None:
    # Two guards are run over their whole default scope: the layering guard has
    # no other mode, and the blocking-call guard matches helper names across
    # files, so a helper here can only be judged together with the rest of
    # the repository. The others are pointed at the package to stay quick.
    whole_scope = {"check_module_layering", "check_async_blocking"}
    arguments = [] if script in whole_scope else ["nekoclaw"]
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / f"{script}.py"), *arguments],
        cwd=REPO_ROOT,
        env=_GUARD_ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr


VIOLATIONS = {
    # guard -> (source of a file that violates it, text expected in the report)
    "check_startup_import_lazy": ("import openai\n", "STARTUP_LAZY_IMPORT"),
    "check_no_temperature": (
        "def build(create):\n    return create('m', None, None, temperature=0.2)\n",
        "NO_TEMPERATURE",
    ),
    "check_llm_budget": (
        "from utils.llm_client import create_chat_llm\n\n\ndef build():\n"
        "    return create_chat_llm('m', None, None)\n",
        "LLM_OUTPUT_BUDGET",
    ),
}


@pytest.mark.parametrize("script", ["check_startup_import_lazy", "check_no_temperature", "check_llm_budget"])
def test_each_guard_reports_a_violation_placed_in_the_package(script: str, tmp_path: Path) -> None:
    source, code = VIOLATIONS[script]
    # A mirror of the package layout, scanned by path: nothing is written into the repo.
    mirror = tmp_path / "nekoclaw" / "runtime"
    mirror.mkdir(parents=True)
    bad = mirror / "violation.py"
    bad.write_text(source, encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / f"{script}.py"), str(tmp_path / "nekoclaw")],
        cwd=REPO_ROOT,
        env=_GUARD_ENV,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,
    )
    assert completed.returncode == 1, completed.stdout + completed.stderr
    assert code in completed.stdout


def test_async_blocking_guard_flags_a_blocking_queue_get_in_package_style_code(tmp_path: Path) -> None:
    mirror = tmp_path / "nekoclaw" / "runtime"
    mirror.mkdir(parents=True)
    (mirror / "violation.py").write_text(
        "import queue\n\nwork_queue = queue.Queue()\n\n\nasync def pump():\n    return work_queue.get()\n",
        encoding="utf-8",
    )
    module = _load("check_async_blocking")
    assert module.main([str(tmp_path / "nekoclaw")]) == 1


def test_layering_guard_rejects_a_runtime_import_of_another_subsystem() -> None:
    layering = _load("check_module_layering")
    path = PACKAGE / "runtime" / "session.py"
    for package in ("memory", "plugin", "main_logic", "brain"):
        edge = (path, 1, 0, "nekoclaw", package, f"import {package}")
        found = layering.find_forbidden_edges([edge])
        assert len(found) == 1 and "uses only config and utils" in found[0][1]
    # The dependencies it does have are allowed.
    for package in ("config", "utils"):
        edge = (path, 1, 0, "nekoclaw", package, f"import {package}")
        assert layering.find_forbidden_edges([edge]) == []
        assert layering.find_violations([edge]) == ([], [])


def test_layering_guard_keeps_the_host_from_the_analyzer_and_from_memory() -> None:
    layering = _load("check_module_layering")
    forbidden = {package for prefix, package, _why in layering.FORBIDDEN_EDGES if prefix == "nekoclaw/host/"}
    own_layer = layering.PACKAGE_TO_LAYER["nekoclaw"]
    reachable = {
        package
        for package, layer in layering.PACKAGE_TO_LAYER.items()
        if layer <= own_layer and package != "nekoclaw"
    }
    # Everything its layer could reach, except config, utils and plugin (whose
    # settings it reads; plugins themselves are called over HTTP).
    assert forbidden == reachable - {"config", "utils", "plugin"}
    path = PACKAGE / "host" / "task_brain.py"
    for package, reason in (
        ("brain", "replaces the analyzer"),
        ("memory", "never read or write each other"),
        ("main_logic", "through its outbox only"),
    ):
        edge = (path, 1, 0, "nekoclaw", package, f"import {package}")
        found = layering.find_forbidden_edges([edge])
        assert len(found) == 1 and reason in found[0][1]
    for package in ("config", "utils", "plugin"):
        edge = (path, 1, 0, "nekoclaw", package, f"import {package}")
        assert layering.find_forbidden_edges([edge]) == []
        assert layering.find_violations([edge]) == ([], [])


def test_importing_the_package_starts_nothing_and_pulls_no_heavy_sdk() -> None:
    program = (
        "import sys, threading\n"
        "import nekoclaw, nekoclaw.runtime\n"
        "import nekoclaw.runtime.process, nekoclaw.runtime.core, nekoclaw.runtime.session\n"
        "import nekoclaw.runtime.model, nekoclaw.runtime.tools.web, nekoclaw.runtime.tools.image\n"
        "import nekoclaw.runtime.tools.shell, nekoclaw.runtime.tools.search\n"
        "heavy = sorted(name for name in ('openai', 'anthropic', 'httpx', 'bs4', 'PIL', 'tiktoken', 'markdownify',"
        " 'utils.config_manager', 'utils.logger_config', 'utils.llm_client') if name in sys.modules)\n"
        "print('heavy=' + ','.join(heavy))\n"
        "print('threads=' + str(threading.active_count()))\n"
        "print('children=' + str(len(__import__('multiprocessing').active_children())))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", program],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "heavy=\n" in completed.stdout.replace("\r\n", "\n")
    assert "threads=1" in completed.stdout
    assert "children=0" in completed.stdout
