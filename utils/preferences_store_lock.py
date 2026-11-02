"""Shared file ownership for preference mutations and cloud-restore rollback.

Keep this below both persistence layers: importing preferences from cloudsave
would reverse their dependency, while separate locks would leave two writers.
"""

from contextlib import contextmanager
from pathlib import Path
import threading

import portalocker

PREFERENCES_THREAD_LOCK = threading.RLock()
PREFERENCES_LOCK_TIMEOUT_SECONDS = 10


@contextmanager
def preferences_store_lock(write_path, *, timeout=PREFERENCES_LOCK_TIMEOUT_SECONDS,
                           thread_lock=PREFERENCES_THREAD_LOCK):
    path = Path(write_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with thread_lock, portalocker.Lock(str(path.with_name(f"{path.name}.lock")), mode="a+", timeout=timeout):
        yield
