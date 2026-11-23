# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""The BLAS thread default must be in the environment before numpy loads.

OpenBLAS reads ``OPENBLAS_NUM_THREADS`` once, when its library is loaded, and
reserves a 32 MiB buffer per thread. Applying the default late, or from a
module that itself drags numpy in, silently does nothing: the process still
starts, it just commits several hundred MiB more.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from config.native_runtime import (
    DEFAULT_OPENBLAS_THREADS,
    INJECTED_THREADS_MARKER_ENV,
    OPENBLAS_THREADS_ENV,
    THREAD_COUNT_ENVS,
    apply_native_runtime_defaults,
    env_for_user_programs,
)

ROOT = Path(__file__).resolve().parents[2]

# What an injected default leaves in the environment.
INJECTED = {OPENBLAS_THREADS_ENV: "1", INJECTED_THREADS_MARKER_ENV: "1"}


def test_unset_variable_gets_the_single_thread_default() -> None:
    env: dict[str, str] = {}
    apply_native_runtime_defaults(env)
    assert env == INJECTED
    assert DEFAULT_OPENBLAS_THREADS == "1"


def test_openblas_reads_these_variables_in_this_order() -> None:
    assert THREAD_COUNT_ENVS == (
        OPENBLAS_THREADS_ENV,
        "OPENBLAS_DEFAULT_NUM_THREADS",
        "GOTO_NUM_THREADS",
        "OMP_NUM_THREADS",
    )


@pytest.mark.parametrize("value", ["4", " 2 ", "+3", "8 threads"])
def test_explicit_thread_count_is_left_alone(value: str) -> None:
    env = {OPENBLAS_THREADS_ENV: value}
    apply_native_runtime_defaults(env)
    # Kept, and not marked: the user chose it.
    assert env == {OPENBLAS_THREADS_ENV: value}


# An ideographic space is whitespace to ``str.lstrip()`` but not to C ``atoi``.
_IDEOGRAPHIC_SPACE = chr(0x3000)


@pytest.mark.parametrize(
    "value", ["", "   ", "0", "-2", "one", "+", "threads 8", _IDEOGRAPHIC_SPACE + "3"]
)
def test_value_openblas_would_ignore_counts_as_unset(value: str) -> None:
    # Measured: OpenBLAS sizes the pool from the CPU count for each of these.
    env = {OPENBLAS_THREADS_ENV: value}
    apply_native_runtime_defaults(env)
    assert env == INJECTED


_FALLBACK_ENVS = ["OPENBLAS_DEFAULT_NUM_THREADS", "GOTO_NUM_THREADS", "OMP_NUM_THREADS"]


@pytest.mark.parametrize("name", _FALLBACK_ENVS)
def test_thread_count_from_a_fallback_variable_is_not_overridden(name: str) -> None:
    # OpenBLAS honours these when its own variable is unset; injecting "1"
    # there would outrank them.
    env = {name: "8"}
    apply_native_runtime_defaults(env)
    assert env == {name: "8"}

    env = {OPENBLAS_THREADS_ENV: "0", name: "8"}
    apply_native_runtime_defaults(env)
    assert env == {OPENBLAS_THREADS_ENV: "0", name: "8"}


@pytest.mark.parametrize("name", _FALLBACK_ENVS)
def test_ignored_fallback_variable_does_not_block_the_default(name: str) -> None:
    env = {name: "0"}
    apply_native_runtime_defaults(env)
    assert env == {name: "0", **INJECTED}


def test_a_process_that_inherits_the_injected_default_keeps_it_marked() -> None:
    # A backend child process imports ``config`` again with the parent's result.
    env = dict(INJECTED)
    apply_native_runtime_defaults(env)
    assert env == INJECTED


def test_user_programs_do_not_get_the_injected_default() -> None:
    env = {"PATH": "x", **INJECTED}
    assert env_for_user_programs(env) == {"PATH": "x"}
    assert env == {"PATH": "x", **INJECTED}, "the caller's mapping is left alone"


def test_user_programs_keep_a_thread_count_the_user_set() -> None:
    env = {"PATH": "x", OPENBLAS_THREADS_ENV: "4", "OMP_NUM_THREADS": "8"}
    assert env_for_user_programs(env) == env


def test_user_programs_keep_a_thread_count_changed_after_injection() -> None:
    # The marker says "1" was injected; the value is no longer "1", so someone set it on purpose.
    env = {OPENBLAS_THREADS_ENV: "6", INJECTED_THREADS_MARKER_ENV: "1"}
    assert env_for_user_programs(env) == {OPENBLAS_THREADS_ENV: "6"}


def test_user_programs_default_to_this_process_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(OPENBLAS_THREADS_ENV, "1")
    monkeypatch.setenv(INJECTED_THREADS_MARKER_ENV, "1")
    seen = env_for_user_programs()
    assert OPENBLAS_THREADS_ENV not in seen and INJECTED_THREADS_MARKER_ENV not in seen
    assert os.environ[OPENBLAS_THREADS_ENV] == "1", "this process keeps its own setting"


# Only ``config`` is imported in the child. That package sits at the bottom of
# the layering and builds no ConfigManager, so the child touches no storage
# root; the probe reports any other product package so that stays true.
_CHILD_PROBE = """
import json, os, sys
sys.path.insert(0, sys.argv[1])
import config
roots = {name.partition(".")[0] for name in sys.modules}
print(json.dumps({
    "value": os.environ.get(sys.argv[2]),
    "marker": os.environ.get(sys.argv[3]),
    "numpy_loaded": "numpy" in roots,
    "other_product": sorted(roots & {"app", "main_logic", "main_routers", "memory", "plugin", "services", "utils"}),
}))
"""


@pytest.mark.parametrize(
    ("inherited", "expected"),
    [
        ({}, ("1", "1")),
        ({OPENBLAS_THREADS_ENV: ""}, ("1", "1")),
        ({OPENBLAS_THREADS_ENV: "   "}, ("1", "1")),
        ({OPENBLAS_THREADS_ENV: "0"}, ("1", "1")),
        ({OPENBLAS_THREADS_ENV: "4"}, ("4", None)),
        ({"OMP_NUM_THREADS": "8"}, (None, None)),
        ({"OPENBLAS_DEFAULT_NUM_THREADS": "8"}, (None, None)),
    ],
    ids=["unset", "empty", "blank", "zero", "explicit", "omp-only", "default-only"],
)
def test_importing_config_in_a_fresh_interpreter(
    inherited: dict[str, str], expected: tuple[str | None, str | None]
) -> None:
    # The test process imported ``config`` during collection, so its own
    # environment cannot show which branch ran. A new interpreter can.
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    for name in (*THREAD_COUNT_ENVS, INJECTED_THREADS_MARKER_ENV):
        env.pop(name, None)
    env.update(inherited)

    result = subprocess.run(
        [sys.executable, "-c", _CHILD_PROBE, str(ROOT), OPENBLAS_THREADS_ENV, INJECTED_THREADS_MARKER_ENV],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout.strip().splitlines()[-1])

    assert (seen["value"], seen["marker"]) == expected
    # The default only helps if it lands before the library loads.
    assert seen["numpy_loaded"] is False
    assert seen["other_product"] == []


def test_config_applies_the_default_before_any_other_import() -> None:
    tree = ast.parse((ROOT / "config" / "__init__.py").read_text(encoding="utf-8"))
    body = [
        node
        for node in tree.body
        if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))
    ]
    first, second = body[0], body[1]

    assert isinstance(first, ast.ImportFrom)
    assert (first.level, first.module) == (1, "native_runtime")
    alias = first.names[0]
    assert alias.name == "apply_native_runtime_defaults"

    assert isinstance(second, ast.Expr) and isinstance(second.value, ast.Call)
    assert isinstance(second.value.func, ast.Name)
    assert second.value.func.id == (alias.asname or alias.name)
    assert not second.value.args and not second.value.keywords


def test_native_runtime_module_imports_only_the_standard_library() -> None:
    # Anything heavier could load numpy before the default is applied.
    tree = ast.parse((ROOT / "config" / "native_runtime.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            imported.add(node.module)
    assert imported == {"__future__", "os", "collections.abc"}
