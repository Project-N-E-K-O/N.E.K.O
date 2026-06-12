# Ported from claudian/src/features/chat/controllers/NavigationController.ts
# Original author: Claudian contributors
# License: MIT

"""
NavigationController — Manages keyboard navigation and scrolling.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class NavigationController:
    """Manages keyboard navigation and scrolling.

    Ported from claudian/src/features/chat/controllers/NavigationController.ts
    """

    def __init__(
        self,
        get_messages_el: Callable[[], Any],
        get_input_el: Callable[[], Any],
    ):
        self._get_messages_el = get_messages_el
        self._get_input_el = get_input_el
        self._scroll_up_key = "w"
        self._scroll_down_key = "s"
        self._focus_input_key = "i"
        self._is_focused_on_messages = False

    @property
    def is_focused_on_messages(self) -> bool:
        return self._is_focused_on_messages

    def handle_keydown(self, event: Any) -> bool:
        """Handle a keydown event.

        Returns True if the event was handled.
        """
        key = getattr(event, 'key', None)
        if not key:
            return False

        # Focus input
        if key == self._focus_input_key and self._is_focused_on_messages:
            self._focus_input()
            return True

        # Scroll up
        if key == self._scroll_up_key and self._is_focused_on_messages:
            self._scroll_up()
            return True

        # Scroll down
        if key == self._scroll_down_key and self._is_focused_on_messages:
            self._scroll_down()
            return True

        return False

    def set_focus_on_messages(self, focused: bool) -> None:
        """Set whether the focus is on messages."""
        self._is_focused_on_messages = focused

    def _focus_input(self) -> None:
        """Focus the input element."""
        input_el = self._get_input_el()
        if input_el:
            input_el.focus()

    def _scroll_up(self) -> None:
        """Scroll messages up."""
        messages_el = self._get_messages_el()
        if messages_el:
            messages_el.scrollTop -= 100

    def _scroll_down(self) -> None:
        """Scroll messages down."""
        messages_el = self._get_messages_el()
        if messages_el:
            messages_el.scrollTop += 100

    def update_keys(self, scroll_up: str, scroll_down: str, focus_input: str) -> None:
        """Update the keyboard shortcuts."""
        self._scroll_up_key = scroll_up
        self._scroll_down_key = scroll_down
        self._focus_input_key = focus_input
