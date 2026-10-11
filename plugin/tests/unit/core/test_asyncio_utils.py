from __future__ import annotations

import asyncio

import pytest

from plugin.utils.asyncio_utils import await_cancellation_safe

pytestmark = [pytest.mark.plugin_unit, pytest.mark.asyncio]


@pytest.mark.parametrize("fail", [False, True])
async def test_repeated_cancellation_drains_the_operation(fail):
    entered = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def work():
        entered.set()
        try:
            await release.wait()
            if fail:
                raise RuntimeError("worker failed")
        finally:
            finished.set()

    operation = asyncio.create_task(work())
    waiter = asyncio.create_task(await_cancellation_safe(operation))
    try:
        await entered.wait()
        for _ in range(2):
            waiter.cancel()
            await asyncio.sleep(0)
            assert not waiter.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(waiter, 1)
        assert finished.is_set()
        assert operation.done() and not operation.cancelled()
    finally:
        release.set()
        await asyncio.gather(waiter, operation, return_exceptions=True)


async def test_an_operation_that_cancels_itself_propagates_cancellation():
    async def work():
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(await_cancellation_safe(asyncio.create_task(work())), 1)
