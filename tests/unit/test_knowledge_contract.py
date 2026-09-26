from __future__ import annotations

import threading
import time

import pytest

from utils.storage.knowledge_contract import knowledge_root_barrier


def test_root_barrier_timeout_bounds_a_same_process_holder(tmp_path):
    """The timeout must cover the in-process lock, not only the file lock.

    Callers map a timeout to a retryable ``knowledge_mutation_busy``; an
    unbounded wait behind another thread would hang writers and shutdown.
    """
    root = tmp_path / "knowledge"
    held = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with knowledge_root_barrier(root):
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        assert held.wait(5)
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with knowledge_root_barrier(root, timeout=0.1):
                pass
        assert time.monotonic() - started < 2.0
    finally:
        release.set()
        holder.join(5)

    with knowledge_root_barrier(root, timeout=0.1):
        pass

