# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Process-wide defaults for native math libraries.

These have to be in ``os.environ`` before the library's shared object loads, so
this module imports only the standard library and ``config/__init__.py``
applies it ahead of every other import.

OpenBLAS (bundled with numpy, and separately with scipy) sizes its thread pool
from the CPU count when its library loads, and reserves a 32 MiB work buffer
per thread up front. On a 20-thread machine ``import numpy`` alone commits
about 615 MiB and starts 19 threads in every process that imports it. The pages
are never touched, so they stay out of the working set and out of Task
Manager's default column, but they still count against the system commit limit
(RAM plus page file). The backend only does small matrix work with numpy --
audio buffers and 256-dimension embedding similarity -- and embedding inference
runs on onnxruntime's own pool, so one BLAS thread is enough.
"""

from __future__ import annotations

import os
from collections.abc import MutableMapping

OPENBLAS_THREADS_ENV = "OPENBLAS_NUM_THREADS"
DEFAULT_OPENBLAS_THREADS = "1"

# OpenBLAS takes its thread count from the first of these that holds a positive
# number, in this order (measured against the OpenBLAS 0.3.23 bundled with
# numpy 1.26: the default variable outranks the GotoBLAS and OpenMP ones).
THREAD_COUNT_ENVS = (
    OPENBLAS_THREADS_ENV,
    "OPENBLAS_DEFAULT_NUM_THREADS",
    "GOTO_NUM_THREADS",
    "OMP_NUM_THREADS",
)

_ASCII_DIGITS = "0123456789"


def _sets_a_thread_count(value: str | None) -> bool:
    """Whether OpenBLAS would use ``value`` rather than fall back to the CPU count.

    OpenBLAS reads the value with C ``atoi``: leading whitespace, an optional
    sign, then digits; anything after the digits is ignored. Zero and negative
    results count as unset.
    """
    if not value:
        return False
    text = value.lstrip()
    if text[:1] == "-":
        return False
    if text[:1] == "+":
        text = text[1:]
    for char in text:
        if char not in _ASCII_DIGITS:
            return False
        if char != "0":
            return True
    return False


def apply_native_runtime_defaults(environ: MutableMapping[str, str] | None = None) -> None:
    """Fill in native-library defaults without overriding an explicit setting.

    A thread count the user set (shell, ``.env``, container) through any of
    ``THREAD_COUNT_ENVS`` wins. A value OpenBLAS would ignore -- blank, zero,
    negative, not a number -- is treated as unset, because OpenBLAS then sizes
    the pool from the CPU count. Everything this process starts inherits the
    result through the environment, external programs included.
    """
    env = os.environ if environ is None else environ
    if any(_sets_a_thread_count(env.get(name)) for name in THREAD_COUNT_ENVS):
        return
    env[OPENBLAS_THREADS_ENV] = DEFAULT_OPENBLAS_THREADS
