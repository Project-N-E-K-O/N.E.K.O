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
"""Wall-clock measurements for complexity guards that survive a loaded machine.

A single ``perf_counter`` sample measures the code *plus* whatever the
scheduler did meanwhile.  Under ``pytest -n`` or on a shared CI runner that
second term alone reaches hundreds of milliseconds, so a guard like "80 spaces
must not take 25x longer than 40" goes red on one preempted sample while the
code is unchanged.

Noise only ever adds time, so the fastest of several runs is the best estimate
of what the code itself costs; that is the ``timeit`` convention too.  A real
blow-up (exponential backtracking, a lost bound) is slow on *every* run and
still fails.
"""

import time


def fastest_run(fn, *, repeat=5, stop_below=None):
    """Return the fastest wall-clock time of up to ``repeat`` calls to ``fn``.

    With ``stop_below`` set, stop as soon as one run beats it: a passing guard
    then costs one call, and only a genuinely slow path pays for every retry.
    """
    best = float("inf")
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - started)
        if stop_below is not None and best < stop_below:
            break
    return best
