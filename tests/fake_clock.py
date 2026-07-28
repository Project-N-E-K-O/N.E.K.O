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
"""Fake clocks scoped to one module.

``monkeypatch.setattr(some_module.time, "monotonic", ...)`` reads like it
patches ``some_module``, but ``some_module.time`` *is* the stdlib ``time``
module, so it swaps the function for the whole process.  With a constant that
is merely untidy; with a stateful fake — an iterator of timestamps — it is a
race: any other live thread that calls ``time.monotonic()`` consumes one of the
values, and this repo runs plenty of background threads across a suite.  The
test then sees shifted timestamps or a ``StopIteration``, and it fails
somewhere unrelated to what it was checking.  Meanwhile every other thread in
the process reads that fake clock too.

Rebinding the module-local name instead keeps the fake where the test meant to
put it.  Everything the module does not override still resolves to the real
``time``.
"""

import time as _real_time


class _ScopedTime:
    """Stands in for the ``time`` module inside one importing module."""

    def __init__(self, **overrides):
        self._overrides = overrides

    def __getattr__(self, name):
        try:
            return self._overrides[name]
        except KeyError:
            return getattr(_real_time, name)


def patch_module_clock(monkeypatch, module, **overrides):
    """Point ``module.time`` at a fake that overrides only ``overrides``.

    Example::

        stamps = iter([1.0, 2.0])
        patch_module_clock(monkeypatch, sender_module, monotonic=lambda: next(stamps))
    """
    monkeypatch.setattr(module, "time", _ScopedTime(**overrides))
