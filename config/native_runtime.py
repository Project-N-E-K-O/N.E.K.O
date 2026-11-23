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
this module imports nothing but ``os`` and ``config/__init__.py`` applies it
ahead of every other import.

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


def apply_native_runtime_defaults(environ: MutableMapping[str, str] | None = None) -> None:
    """Fill in native-library defaults without overriding an explicit setting.

    A value set by the user (shell, ``.env``, container) wins. A blank value is
    treated as unset, because OpenBLAS ignores it and falls back to the CPU
    count. Child processes inherit the result through the environment.
    """
    env = os.environ if environ is None else environ
    if not env.get(OPENBLAS_THREADS_ENV, "").strip():
        env[OPENBLAS_THREADS_ENV] = DEFAULT_OPENBLAS_THREADS
