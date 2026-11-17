"""Which runtime a commission's task agent runs in."""

from __future__ import annotations

from typing import Any

import pytest

from nekoclaw.host.runtimes import RUNTIME_BUILTIN, RUNTIME_DSH, Runtimes
from nekoclaw.runtime.protocol import ModelConfig

MODEL = ModelConfig(model="m", base_url="http://127.0.0.1:9/v1", api_key="k")


class Installation:
    """What the choice reads of a dsh installation."""

    def __init__(self, *, installed: bool = True, preference: str = "auto") -> None:
        self.is_installed = installed
        self.preference = preference
        self.looked = 0

    def installed(self) -> bool:
        self.looked += 1
        return self.is_installed

    def wanted(self) -> bool:
        self.looked += 1
        return self.is_installed and self.preference == "auto"


class Dsh:
    """A dsh runtime that can be told not to come up."""

    def __init__(self) -> None:
        self.prepared: list[Any] = []
        self.failure: Exception | None = None
        # Set to hold ``prepare`` up until it is set.
        self.gate: Any = None
        self.shut_down = 0

    async def prepare(self, model: Any) -> None:
        self.prepared.append(model)
        if self.gate is not None:
            await self.gate.wait()
        if self.failure is not None:
            raise self.failure

    async def shutdown(self) -> None:
        self.shut_down += 1


def _runtimes(installation: Any = None) -> tuple[Runtimes, Dsh, list[str]]:
    made: list[str] = []
    dsh = Dsh()

    def builtin(on_event: Any, host: Any) -> Any:
        made.append(RUNTIME_BUILTIN)
        return object()

    def dsh_factory(on_event: Any, host: Any, installed: Any) -> Any:
        assert installed is installation
        made.append(RUNTIME_DSH)
        return dsh

    runtimes = Runtimes(
        on_event=lambda event: None,
        callbacks=lambda: "the host's callbacks",
        builtin_factory=builtin,
        dsh=installation,
        dsh_factory=dsh_factory,
    )
    return runtimes, dsh, made


async def test_without_dsh_there_is_only_the_built_in_runtime() -> None:
    runtimes, _dsh, made = _runtimes(None)
    assert not runtimes.started

    name, runtime, note = await runtimes.choose(MODEL)
    again = await runtimes.choose(MODEL, begun_in=RUNTIME_DSH)

    assert (name, note) == (RUNTIME_BUILTIN, None) and runtimes.get(RUNTIME_BUILTIN) is runtime
    assert again[0] == RUNTIME_BUILTIN and again[1] is runtime
    # Created once, and nothing of dsh.
    assert made == [RUNTIME_BUILTIN] and runtimes.get(RUNTIME_DSH) is None and runtimes.started


async def test_a_new_commission_runs_in_dsh_when_it_is_installed_and_not_switched_off() -> None:
    runtimes, dsh, made = _runtimes(Installation())

    name, runtime, note = await runtimes.choose(MODEL)

    assert (name, runtime, note) == (RUNTIME_DSH, dsh, None)
    # Made ready before the commission is given to it; the built-in runtime is not even created.
    assert dsh.prepared == [MODEL] and made == [RUNTIME_DSH]


@pytest.mark.parametrize("installation", [Installation(installed=False), Installation(preference="builtin")])
async def test_a_new_commission_runs_in_the_built_in_runtime_otherwise(installation: Installation) -> None:
    runtimes, dsh, made = _runtimes(installation)
    name, _runtime, note = await runtimes.choose(MODEL)
    assert (name, note) == (RUNTIME_BUILTIN, None) and dsh.prepared == [] and made == [RUNTIME_BUILTIN]


async def test_a_commission_goes_back_to_the_runtime_it_began_in() -> None:
    # Began in the built-in runtime, and dsh was installed since: it stays.
    runtimes, dsh, _made = _runtimes(Installation())
    assert (await runtimes.choose(MODEL, begun_in=RUNTIME_BUILTIN))[0] == RUNTIME_BUILTIN
    assert dsh.prepared == []

    # Began in dsh, and the user switched dsh off since: it stays, too.
    runtimes, dsh, _made = _runtimes(Installation(preference="builtin"))
    assert (await runtimes.choose(MODEL, begun_in=RUNTIME_DSH))[0] == RUNTIME_DSH
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_BUILTIN


async def test_a_commission_that_began_in_a_dsh_that_is_gone_starts_over_in_the_built_in_runtime() -> None:
    runtimes, dsh, _made = _runtimes(Installation(installed=False))
    name, _runtime, note = await runtimes.choose(MODEL, begun_in=RUNTIME_DSH)
    assert name == RUNTIME_BUILTIN and note == "dsh is no longer installed" and dsh.prepared == []


async def test_a_task_agent_started_by_another_runs_where_that_one_runs() -> None:
    runtimes, dsh, _made = _runtimes(Installation(preference="builtin"))
    assert (await runtimes.choose(MODEL, parent_runtime=RUNTIME_DSH))[0] == RUNTIME_DSH

    runtimes, dsh, _made = _runtimes(Installation())
    assert (await runtimes.choose(MODEL, parent_runtime=RUNTIME_BUILTIN))[0] == RUNTIME_BUILTIN
    assert dsh.prepared == []

    runtimes, _dsh, _made = _runtimes(Installation(installed=False))
    assert (await runtimes.choose(MODEL, parent_runtime=RUNTIME_DSH))[0] == RUNTIME_BUILTIN


async def test_a_dsh_that_cannot_be_made_ready_leaves_the_commission_to_the_built_in_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config import nekoclaw_settings
    from nekoclaw.dsh import DshUnavailable

    runtimes, dsh, _made = _runtimes(Installation())
    dsh.failure = DshUnavailable("dsh did not load the permission guard")

    name, runtime, note = await runtimes.choose(MODEL)

    assert name == RUNTIME_BUILTIN and runtime is runtimes.get(RUNTIME_BUILTIN)
    assert note == "dsh did not load the permission guard" == runtimes.dsh_error
    # The next new commission does not wait for dsh to fail again...
    dsh.failure = None
    name, _runtime, note = await runtimes.choose(MODEL)
    assert name == RUNTIME_BUILTIN and "a moment ago" in str(note) and len(dsh.prepared) == 1
    # ...but one that began in dsh is tried there all the same: only dsh has what was said in it.
    assert (await runtimes.choose(MODEL, begun_in=RUNTIME_DSH))[0] == RUNTIME_DSH
    assert runtimes.dsh_error is None
    # And once dsh worked, new commissions go to it again.
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_DSH

    dsh.failure = DshUnavailable("dsh could not be started")
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_BUILTIN
    monkeypatch.setattr(nekoclaw_settings, "DSH_RETRY_SECONDS", 0.0)
    dsh.failure = None
    # After a while it is tried again.
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_DSH


async def test_a_commission_that_began_in_dsh_and_cannot_go_back_is_told_why() -> None:
    from nekoclaw.dsh import DshUnavailable

    runtimes, dsh, _made = _runtimes(Installation())
    dsh.failure = DshUnavailable("dsh could not be started: no such file")
    name, _runtime, note = await runtimes.choose(MODEL, begun_in=RUNTIME_DSH)
    assert name == RUNTIME_BUILTIN and note == "dsh could not be started: no such file"


async def test_a_model_dsh_is_not_served_on_is_no_failure_of_dsh() -> None:
    runtimes, dsh, _made = _runtimes(Installation())
    anthropic = ModelConfig(model="claude", base_url="https://example.invalid", api_key="k", provider_type="anthropic")

    name, _runtime, note = await runtimes.choose(anthropic)

    assert name == RUNTIME_BUILTIN and "Anthropic" in str(note)
    # dsh was not started for it, and the next commission with another model is not kept from it.
    assert dsh.prepared == [] and runtimes.dsh_error is None
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_DSH


async def test_a_dsh_that_was_ready_and_opened_no_session_is_left_alone_for_a_while() -> None:
    runtimes, dsh, _made = _runtimes(Installation())
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_DSH

    runtimes.dsh_refused("dsh refused the session: no such directory")

    name, _runtime, note = await runtimes.choose(MODEL)
    assert name == RUNTIME_BUILTIN and "dsh refused the session: no such directory" in str(note)
    assert runtimes.dsh_error == "dsh refused the session: no such directory" and len(dsh.prepared) == 1


async def test_dsh_is_ended_unless_a_session_is_in_it_and_made_anew_when_next_needed() -> None:
    runtimes, dsh, made = _runtimes(Installation())
    await runtimes.choose(MODEL)
    await runtimes.choose(MODEL, begun_in=RUNTIME_BUILTIN)

    assert await runtimes.retire_dsh(lambda: True) is False
    assert dsh.shut_down == 0 and runtimes.get(RUNTIME_DSH) is dsh

    assert await runtimes.retire_dsh(lambda: False) is True
    assert dsh.shut_down == 1 and [name for name, _runtime in runtimes.created()] == [RUNTIME_BUILTIN]
    # With nothing to end, it is ended.
    assert await runtimes.retire_dsh(lambda: False) is True and dsh.shut_down == 1
    await runtimes.choose(MODEL)
    assert made == [RUNTIME_DSH, RUNTIME_BUILTIN, RUNTIME_DSH]


async def test_ending_dsh_waits_for_a_commission_that_is_being_given_it() -> None:
    import asyncio

    runtimes, dsh, _made = _runtimes(Installation())
    dsh.gate = asyncio.Event()
    choosing = asyncio.ensure_future(runtimes.choose(MODEL))
    await asyncio.sleep(0.05)

    # Whether a session is in dsh is asked once that commission has its runtime.
    retiring = asyncio.ensure_future(runtimes.retire_dsh(lambda: choosing.done()))
    await asyncio.sleep(0.05)
    assert not retiring.done() and dsh.shut_down == 0
    dsh.gate.set()

    assert (await choosing)[0] == RUNTIME_DSH
    assert await retiring is False and dsh.shut_down == 0 and runtimes.get(RUNTIME_DSH) is dsh


async def test_no_commission_starts_dsh_while_its_program_is_being_removed() -> None:
    import asyncio

    installation = Installation()
    runtimes, dsh, _made = _runtimes(installation)
    assert (await runtimes.choose(MODEL))[0] == RUNTIME_DSH
    removing = asyncio.Event()
    removed = asyncio.Event()

    async def remove() -> None:
        removing.set()
        await removed.wait()
        installation.is_installed = False

    retiring = asyncio.ensure_future(runtimes.retire_dsh(lambda: False, remove))
    await asyncio.wait_for(removing.wait(), 1)
    # A commission that wants dsh meanwhile waits: its program is going.
    choosing = asyncio.ensure_future(runtimes.choose(MODEL))
    await asyncio.sleep(0.05)
    assert not choosing.done() and len(dsh.prepared) == 1

    removed.set()
    assert await retiring is True and dsh.shut_down == 1
    name, _runtime, note = await choosing

    # dsh is gone, which is no failure of dsh: nothing is noted against it.
    assert (name, note) == (RUNTIME_BUILTIN, None) and len(dsh.prepared) == 1 and runtimes.dsh_error is None
    # One that began in dsh is told why it starts over.
    assert (await runtimes.choose(MODEL, begun_in=RUNTIME_DSH))[2] == "dsh is no longer installed"


async def test_a_commission_for_the_built_in_runtime_does_not_wait_for_dsh() -> None:
    import asyncio

    runtimes, dsh, _made = _runtimes(Installation())
    dsh.gate = asyncio.Event()
    slow = asyncio.ensure_future(runtimes.choose(MODEL))
    await asyncio.sleep(0.05)

    # dsh takes its time to come up, or to turn out not to: these three go to the built-in runtime meanwhile.
    stays = await asyncio.wait_for(runtimes.choose(MODEL, begun_in=RUNTIME_BUILTIN), 1)
    child = await asyncio.wait_for(runtimes.choose(MODEL, parent_runtime=RUNTIME_BUILTIN), 1)
    anthropic = ModelConfig(model="claude", base_url="https://example.invalid", api_key="k", provider_type="anthropic")
    unserved = await asyncio.wait_for(runtimes.choose(anthropic), 1)
    assert [chosen[0] for chosen in (stays, child, unserved)] == [RUNTIME_BUILTIN] * 3 and not slow.done()

    # One that is to run in dsh waits its turn, and does not try a dsh that just failed.
    waiting = asyncio.ensure_future(runtimes.choose(MODEL))
    await asyncio.sleep(0.05)
    assert not waiting.done()
    from nekoclaw.dsh import DshUnavailable

    dsh.failure = DshUnavailable("dsh could not be started")
    dsh.gate.set()
    assert (await slow)[0] == RUNTIME_BUILTIN
    name, _runtime, note = await waiting
    assert name == RUNTIME_BUILTIN and "a moment ago" in str(note) and len(dsh.prepared) == 1


async def test_whatever_keeps_dsh_from_working_the_commission_is_still_done() -> None:
    runtimes, dsh, _made = _runtimes(Installation())
    dsh.failure = RuntimeError("a bug")
    name, runtime, note = await runtimes.choose(MODEL)
    assert name == RUNTIME_BUILTIN and runtime is runtimes.get(RUNTIME_BUILTIN)
    assert note == "dsh failed: RuntimeError: a bug" == runtimes.dsh_error


async def test_a_choice_that_is_cancelled_is_cancelled() -> None:
    import asyncio

    runtimes, dsh, _made = _runtimes(Installation())
    dsh.failure = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await runtimes.choose(MODEL)
    assert runtimes.dsh_error is None


async def test_the_runtimes_are_handed_over_for_shutting_down_and_made_anew_afterwards() -> None:
    runtimes, dsh, made = _runtimes(Installation())
    await runtimes.choose(MODEL)
    await runtimes.choose(MODEL, begun_in=RUNTIME_BUILTIN)

    taken = runtimes.take_all()

    assert [name for name, _runtime in taken] == [RUNTIME_DSH, RUNTIME_BUILTIN] and not runtimes.started
    await runtimes.choose(MODEL)
    assert made == [RUNTIME_DSH, RUNTIME_BUILTIN, RUNTIME_DSH] and [name for name, _ in runtimes.created()] == [RUNTIME_DSH]
