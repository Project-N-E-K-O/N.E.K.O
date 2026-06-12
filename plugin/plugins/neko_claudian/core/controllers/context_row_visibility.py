# Ported from claudian/src/features/chat/controllers/contextRowVisibility.ts
# Original author: Claudian contributors
# License: MIT

"""
ContextRowVisibility — Manages context row visibility in the UI.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class ContextRowVisibilityController:
    """Manages context row visibility in the UI.

    Ported from claudian/src/features/chat/controllers/contextRowVisibility.ts
    """

    def __init__(
        self,
        get_context_row_el: Callable[[], Optional[Any]],
    ):
        self._get_context_row_el = get_context_row_el
        self._is_visible = True
        self._has_content = False

    @property
    def is_visible(self) -> bool:
        return self._is_visible

    def set_visible(self, visible: bool) -> None:
        """Set visibility of the context row."""
        self._is_visible = visible
        self._update_visibility()

    def set_has_content(self, has_content: bool) -> None:
        """Set whether the context row has content."""
        self._has_content = has_content
        self._update_visibility()

    def _update_visibility(self) -> None:
        """Update the DOM visibility based on state."""
        el = self._get_context_row_el()
        if not el:
            return

        should_show = self._is_visible and self._has_content
        # In full version, this would toggle CSS classes
        if should_show:
            el.removeClass("hidden")
        else:
            el.addClass("hidden")
