"""Runtime compatibility API for instruction context actions."""

from __future__ import annotations

from . import runtime_instructions


class RuntimeInstructionApiMixin:
    async def inject_instructions(self, *, force: bool = False) -> str:
        return await runtime_instructions.inject_instructions(self, force=force)

    async def sync_live_instructions(self, *, force: bool = False) -> str:
        return await runtime_instructions.sync_live_instructions(self, force=force)

    async def sync_developer_mode(
        self, *, announce: bool = False, force: bool = False
    ) -> str:
        return await runtime_instructions.sync_developer_mode(
            self, announce=announce, force=force
        )

    async def inject_developer_instructions(self, *, force: bool = False) -> str:
        return await runtime_instructions.inject_developer_instructions(self, force=force)

    async def restore_developer_instructions(self, *, force: bool = False) -> str:
        return await runtime_instructions.restore_developer_instructions(
            self, force=force
        )

    async def announce_developer_mode(self) -> str:
        return await runtime_instructions.announce_developer_mode(self)

    async def restore_instructions(self, *, force: bool = False) -> str:
        return await runtime_instructions.restore_instructions(self, force=force)
