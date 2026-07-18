# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.

"""In-memory enabled state for Widget Mode."""
from __future__ import annotations

import asyncio
from typing import Any


class WidgetModeCoordinator:
    """Coordinate the process-local Widget Mode switch."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._enabled = False

    def snapshot(self) -> dict[str, Any]:
        return {"enabled": self._enabled}

    async def set_enabled(self, enabled: bool) -> dict[str, Any]:
        async with self._lock:
            self._enabled = enabled is True
            return self.snapshot()


widget_mode_coordinator = WidgetModeCoordinator()
