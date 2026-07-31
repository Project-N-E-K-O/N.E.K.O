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

"""The writer lock for ``root_state.json``.

Its own module, with no project imports, for two reasons. The lock guards a
*file*, not a ``ConfigManager`` instance — several managers can coexist in one
process (the shared singleton main_server publishes, plus the
``get_runtime_config_manager(APP_NAME, migrate=False)`` fallback the storage
router takes during limited startup) and they all point at the same file, so a
per-instance lock would not serialize them. And hanging it off the manager
would put it in the duck-typed surface every test double has to grow.

⚠️ Writers only. The read path (``load_root_state``) must never take this lock.
The storage-location writes run in ``asyncio.to_thread`` workers, and
``utils.file_utils`` re-enables its 155ms ``os.replace`` backoff off the event
loop; if reads took the lock too, the storage page's ``GET /status`` poll
(``STORAGE_STATUS_POLL_INTERVAL_MS``) would stall behind a worker and the
blocking would come straight back to the loop through the lock. Reads do not
need it: writes land via ``os.replace``, so a reader sees either the old file
or the new one, never a half-written one.
"""
from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

_ROOT_STATE_WRITE_LOCK = threading.RLock()


@contextmanager
def root_state_transaction() -> Iterator[None]:
    """Hold the root_state writer lock across one read-modify-write sequence.

    Callers that load root_state, edit it and save it back must wrap the whole
    sequence. Locking only inside ``save_root_state`` makes the final write
    atomic but still lets two writers read the same pre-image and have the
    later one clobber the earlier one's fields.

    Reentrant on purpose: ``ConfigManager.save_root_state`` takes the same lock,
    so it nests inside this block.
    """
    with _ROOT_STATE_WRITE_LOCK:
        yield
