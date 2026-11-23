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
import os
from pathlib import Path

import config
from config.native_runtime import (
    DEFAULT_OPENBLAS_THREADS,
    OPENBLAS_THREADS_ENV,
    apply_native_runtime_defaults,
)

ROOT = Path(__file__).resolve().parents[2]


def test_unset_variable_gets_the_single_thread_default() -> None:
    env: dict[str, str] = {}
    apply_native_runtime_defaults(env)
    assert env == {OPENBLAS_THREADS_ENV: "1"}
    assert DEFAULT_OPENBLAS_THREADS == "1"


def test_explicit_setting_is_left_alone() -> None:
    env = {OPENBLAS_THREADS_ENV: "4"}
    apply_native_runtime_defaults(env)
    assert env == {OPENBLAS_THREADS_ENV: "4"}


def test_blank_setting_counts_as_unset() -> None:
    # OpenBLAS ignores an empty value and sizes the pool from the CPU count.
    for blank in ("", "   "):
        env = {OPENBLAS_THREADS_ENV: blank}
        apply_native_runtime_defaults(env)
        assert env == {OPENBLAS_THREADS_ENV: "1"}


def test_importing_config_leaves_the_variable_set_in_this_process() -> None:
    assert config is not None
    assert os.environ.get(OPENBLAS_THREADS_ENV, "").strip()


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
