"""The HTTP cancellation path must return without waiting behind another import."""

import asyncio
import threading

from tests.unit.test_model_workshop_install import bundle_path, config, service  # noqa: F401


def test_async_cancel_of_queued_import_does_not_wait_for_running_import(service, tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = service.installer.run

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(service.installer, "run", blocked)
    path, _ = bundle_path(tmp_path)
    try:
        with path.open("rb") as source:
            first = service.receive(source)
        assert entered.wait(5)
        with path.open("rb") as source:
            queued = service.receive(source)

        async def cancel():
            return await asyncio.wait_for(service.acancel(queued), 1)

        result = asyncio.run(cancel())
        assert result["task_id"] == queued and result["phase"] == "queued"
        assert not release.is_set()
    finally:
        release.set()
    assert service.wait(first, 10)["phase"] == "completed"
    assert service.wait(queued, 10)["phase"] == "cancelled"
