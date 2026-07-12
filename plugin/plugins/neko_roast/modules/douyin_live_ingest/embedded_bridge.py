"""Bundled Douyin bridge supervisor."""

from __future__ import annotations

from ..live_bridge.process_supervisor import BridgeProcessState, BridgeProcessSupervisor
from .bridge_backend import DouyinBridgeBackend, default_douyin_bridge_backend


class DouyinEmbeddedBridgeSupervisor:
    """Owns the selected bundled Douyin bridge executable lifecycle."""

    def __init__(
        self,
        *,
        supervisor: BridgeProcessSupervisor | None = None,
        backend: DouyinBridgeBackend | None = None,
    ) -> None:
        backend = backend or default_douyin_bridge_backend()
        self.backend_id = backend.backend_id
        self._supervisor = supervisor or BridgeProcessSupervisor(
            executable_path=backend.executable_path,
            args_factory=backend.args_factory,
            stale_process_cleaner=backend.stale_process_cleaner,
        )

    async def start(self) -> BridgeProcessState:
        return await self._supervisor.start()

    async def stop(self) -> BridgeProcessState:
        return await self._supervisor.stop()
