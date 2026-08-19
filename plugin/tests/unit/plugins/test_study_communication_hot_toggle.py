from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion import entry_status_entries
from plugin.plugins.study_companion.entry_status_entries import _StatusEntriesMixin
from plugin.plugins.study_companion.models import CommunicationConfig, StudyConfig
from plugin.sdk.plugin import Err, Ok


class _Logger:
    def warning(self, *_args, **_kwargs) -> None:
        pass


class _TaskState:
    def __init__(self, *, done: bool = False) -> None:
        self._done = done

    def done(self) -> bool:
        return self._done


class _FakeEventBus:
    instances: list["_FakeEventBus"] = []

    def __init__(self, *, plugin_ctx: object) -> None:
        self.plugin_ctx = plugin_ctx
        self.emit_count = 3
        self.block_count = 2
        self.close_calls = 0
        self.__class__.instances.append(self)

    async def close(self) -> None:
        self.close_calls += 1


class _FailingConfigComponent:
    def __init__(self) -> None:
        self.calls = 0

    def update_config(self, _config: StudyConfig) -> None:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("apply failed")
        raise RuntimeError("restore failed")


class _RuntimeOwner(_StatusEntriesMixin):
    def __init__(
        self,
        *,
        enabled: bool,
        narration_enabled: bool = True,
        event_bus: object | None = None,
    ) -> None:
        self.ctx = SimpleNamespace(name="test")
        self.logger = _Logger()
        self._communication_settings_lock = asyncio.Lock()
        self._cfg = StudyConfig(
            communication=CommunicationConfig(
                enabled=enabled,
                solution_narration_enabled=narration_enabled,
            )
        )
        self._event_bus = event_bus
        self._ocr_pipeline = None
        self._agent = None
        self._pomodoro_timer = None
        self._supervision = None
        self._checkin_manager = None
        self._neko_command_transport = None
        self._neko_command_handler = None
        self._neko_command_watcher = None
        self._command_worker_task = None
        self.subscribe_calls = 0
        self.unsubscribe_calls = 0
        self.start_worker_calls = 0
        self.cancel_worker_calls = 0
        self.persist_calls = 0
        self.fail_subscribe = False
        self.fail_unsubscribe = False
        self.fail_persist_once = False
        self.start_review_due_calls = 0
        self.cancel_review_due_calls = 0

    def _start_review_due_task(self) -> None:
        self.start_review_due_calls += 1

    async def _cancel_review_due_task(self) -> None:
        self.cancel_review_due_calls += 1

    async def _subscribe_neko_commands(self) -> None:
        self.subscribe_calls += 1
        if self.fail_subscribe:
            raise RuntimeError("subscribe failed")
        self._neko_command_transport = object()
        self._neko_command_handler = object()

    async def _unsubscribe_neko_commands(self) -> None:
        self.unsubscribe_calls += 1
        self._neko_command_transport = None
        self._neko_command_handler = None
        self._neko_command_watcher = None
        if self.fail_unsubscribe:
            self.fail_unsubscribe = False
            raise RuntimeError("unsubscribe failed")

    def _start_command_worker(self) -> None:
        self.start_worker_calls += 1
        self._command_worker_task = _TaskState()

    async def _cancel_command_worker(self) -> None:
        self.cancel_worker_calls += 1
        self._command_worker_task = None

    async def _refresh_dependency_status(self) -> dict:
        return {}

    async def _persist_state(self) -> None:
        self.persist_calls += 1
        if self.fail_persist_once:
            self.fail_persist_once = False
            raise RuntimeError("persist failed")


@pytest.fixture(autouse=True)
def _fake_event_bus(monkeypatch: pytest.MonkeyPatch):
    _FakeEventBus.instances.clear()
    monkeypatch.setattr(entry_status_entries, "StudyEventBus", _FakeEventBus)


@pytest.mark.asyncio
async def test_settings_read_and_status_report_configured_and_runtime_state() -> None:
    owner = _RuntimeOwner(enabled=False, narration_enabled=True)

    settings = await owner.study_get_settings_config()
    status = await owner.study_neko_communication_status()

    expected = {
        "configured_enabled": False,
        "solution_narration_enabled": True,
        "available": False,
        "command_subscription_active": False,
        "command_worker_active": False,
        "events_emitted": 0,
        "events_blocked": 0,
    }
    assert isinstance(settings, Ok)
    assert settings.value["communication_status"] == expected
    assert isinstance(status, Ok)
    assert status.value == expected


@pytest.mark.asyncio
async def test_settings_remain_readable_when_model_runtime_diagnostics_fail() -> None:
    owner = _RuntimeOwner(enabled=False)

    async def fail_model_runtime() -> dict:
        raise RuntimeError("model config unavailable")

    owner._agent = SimpleNamespace(describe_model_runtimes=fail_model_runtime)

    settings = await owner.study_get_settings_config()

    assert isinstance(settings, Ok)
    assert settings.value["config"] == entry_status_entries._settings_config_payload(
        owner._cfg
    )
    assert settings.value["model_runtime"] == {}


@pytest.mark.asyncio
async def test_enabling_communication_starts_runtime_once_and_persists() -> None:
    owner = _RuntimeOwner(enabled=False)

    first = await owner.study_update_settings_config(
        config={"communication": {"enabled": True}}
    )
    first_bus = owner._event_bus
    second = await owner.study_update_settings_config(
        config={"communication": {"enabled": True}}
    )

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert owner._cfg.communication.enabled is True
    assert first_bus is not None
    assert owner._event_bus is first_bus
    assert owner.subscribe_calls == 1
    assert owner.start_worker_calls == 1
    assert owner.start_review_due_calls == 1
    assert owner.persist_calls == 2
    assert first.value["communication_status"] == {
        "configured_enabled": True,
        "solution_narration_enabled": True,
        "available": True,
        "command_subscription_active": True,
        "command_worker_active": True,
        "events_emitted": 3,
        "events_blocked": 2,
    }


@pytest.mark.asyncio
async def test_concurrent_enable_updates_share_one_runtime() -> None:
    owner = _RuntimeOwner(enabled=False)

    results = await asyncio.gather(
        owner.study_update_settings_config(
            config={"communication": {"enabled": True}}
        ),
        owner.study_update_settings_config(
            config={"communication": {"enabled": True}}
        ),
    )

    assert all(isinstance(result, Ok) for result in results)
    assert len(_FakeEventBus.instances) == 1
    assert owner.subscribe_calls == 1
    assert owner.start_worker_calls == 1


@pytest.mark.asyncio
async def test_unavailable_command_subscription_does_not_block_outbound_runtime() -> None:
    owner = _RuntimeOwner(enabled=False)

    async def subscribe_without_transport() -> None:
        owner.subscribe_calls += 1

    owner._subscribe_neko_commands = subscribe_without_transport

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": True}}
    )

    assert isinstance(result, Ok)
    assert result.value["communication_status"]["available"] is True
    assert (
        result.value["communication_status"]["command_subscription_active"]
        is False
    )
    assert result.value["communication_status"]["command_worker_active"] is True


@pytest.mark.asyncio
async def test_disabling_communication_detaches_runtime_before_cleanup() -> None:
    owner = _RuntimeOwner(enabled=True, event_bus=_FakeEventBus(plugin_ctx=object()))
    old_bus = owner._event_bus
    close_saw_detached = False

    async def close() -> None:
        nonlocal close_saw_detached
        close_saw_detached = owner._event_bus is None
        old_bus.close_calls += 1

    old_bus.close = close
    owner._neko_command_transport = object()
    owner._neko_command_handler = object()
    owner._command_worker_task = _TaskState()

    first = await owner.study_update_settings_config(
        config={"communication": {"enabled": False}}
    )
    second = await owner.study_update_settings_config(
        config={"communication": {"enabled": False}}
    )

    assert isinstance(first, Ok)
    assert isinstance(second, Ok)
    assert close_saw_detached is True
    assert old_bus.close_calls == 1
    assert owner._event_bus is None
    assert owner.unsubscribe_calls == 1
    assert owner.cancel_worker_calls == 1
    assert owner.cancel_review_due_calls == 1
    assert first.value["communication_status"]["available"] is False


@pytest.mark.asyncio
async def test_enable_failure_rolls_back_config_and_partial_runtime() -> None:
    owner = _RuntimeOwner(enabled=False)
    owner.fail_subscribe = True

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": True}}
    )

    assert isinstance(result, Err)
    assert owner._cfg.communication.enabled is False
    assert owner._event_bus is None
    assert len(_FakeEventBus.instances) == 1
    assert _FakeEventBus.instances[0].close_calls == 1
    assert owner.unsubscribe_calls == 1
    assert owner.cancel_worker_calls == 1
    assert owner.persist_calls == 0


@pytest.mark.asyncio
async def test_persist_failure_rolls_back_enabled_runtime_and_persists_old_config() -> None:
    owner = _RuntimeOwner(enabled=False)
    owner.fail_persist_once = True

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": True}}
    )

    assert isinstance(result, Err)
    assert owner._cfg.communication.enabled is False
    assert owner._event_bus is None
    assert len(_FakeEventBus.instances) == 1
    assert _FakeEventBus.instances[0].close_calls == 1
    assert owner.persist_calls == 2


@pytest.mark.asyncio
async def test_persist_failure_restores_previously_enabled_runtime() -> None:
    old_bus = _FakeEventBus(plugin_ctx=object())
    owner = _RuntimeOwner(enabled=True, event_bus=old_bus)
    owner.fail_persist_once = True

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": False}}
    )

    assert isinstance(result, Err)
    assert owner._cfg.communication.enabled is True
    assert owner._event_bus is not None
    assert owner._event_bus is not old_bus
    assert old_bus.close_calls == 1
    assert owner.subscribe_calls == 1
    assert owner.start_worker_calls == 1
    assert owner.persist_calls == 2


@pytest.mark.asyncio
async def test_disable_cleanup_failure_still_cancels_worker_closes_bus_and_rolls_back() -> None:
    old_bus = _FakeEventBus(plugin_ctx=object())
    owner = _RuntimeOwner(enabled=True, event_bus=old_bus)
    owner._neko_command_transport = object()
    owner._neko_command_handler = object()
    owner._command_worker_task = _TaskState()
    owner.fail_unsubscribe = True

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": False}}
    )

    assert isinstance(result, Err)
    assert old_bus.close_calls == 1
    assert owner.cancel_worker_calls == 1
    assert owner._cfg.communication.enabled is True
    assert owner._event_bus is not None
    assert owner._event_bus is not old_bus


@pytest.mark.asyncio
async def test_disable_closes_bus_before_other_cleanup_and_continues_after_close_failure() -> None:
    old_bus = _FakeEventBus(plugin_ctx=object())
    owner = _RuntimeOwner(enabled=True, event_bus=old_bus)
    owner._neko_command_transport = object()
    owner._neko_command_handler = object()
    owner._command_worker_task = _TaskState()
    cleanup_order: list[str] = []

    async def close() -> None:
        cleanup_order.append("close")
        old_bus.close_calls += 1
        raise RuntimeError("close failed")

    async def unsubscribe() -> None:
        cleanup_order.append("unsubscribe")
        owner.unsubscribe_calls += 1
        owner._neko_command_transport = None
        owner._neko_command_handler = None

    async def cancel_worker() -> None:
        cleanup_order.append("cancel_worker")
        owner.cancel_worker_calls += 1
        owner._command_worker_task = None

    old_bus.close = close
    owner._unsubscribe_neko_commands = unsubscribe
    owner._cancel_command_worker = cancel_worker

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": False}}
    )

    assert isinstance(result, Err)
    assert cleanup_order[:3] == ["close", "unsubscribe", "cancel_worker"]
    assert owner.unsubscribe_calls == 1
    assert owner.cancel_worker_calls == 1


@pytest.mark.asyncio
async def test_component_apply_and_restore_failures_still_restore_runtime_and_persistence() -> None:
    owner = _RuntimeOwner(enabled=False)
    component = _FailingConfigComponent()
    owner._ocr_pipeline = component

    result = await owner.study_update_settings_config(
        config={"communication": {"enabled": True}}
    )

    assert isinstance(result, Err)
    assert str(result.error) == "apply failed"
    assert component.calls == 2
    assert owner._cfg.communication.enabled is False
    assert owner._event_bus is None
    assert owner.persist_calls == 1
    assert len(_FakeEventBus.instances) == 1
    assert _FakeEventBus.instances[0].close_calls == 1
