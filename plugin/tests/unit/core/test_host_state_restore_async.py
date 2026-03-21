from __future__ import annotations

from pathlib import Path


def test_host_uses_async_state_restore_calls() -> None:
    host_file = Path("/mnt/k_disk/programe/N.E.K.O/plugin/core/host.py")
    text = host_file.read_text(encoding="utf-8")

    assert "asyncio.run(state_persistence.has_saved_state())" in text
    assert "asyncio.run(state_persistence.load(instance))" in text
    assert "asyncio.run(state_persistence.clear())" in text
