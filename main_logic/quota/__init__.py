"""Local quota-drop hook package for N.E.K.O.Servers integration.

The package exposes two hooks for ``main_logic.agent_event_bus``:
- ``on_text_message(lanlan_name, text) -> None`` for text-message events. It
  must return ``None`` so existing first-hit-wins consumers keep their behavior.
- ``on_utterance(bucket, event) -> None`` for user utterance sinks. The current
  M2-j implementation is a placeholder until emotion rules are enabled.

Activation requires both ``NEKO_QUOTA_DROPPER_ENABLED=1`` and
``NEKO_SOCIAL_BASE_URL``. The default is disabled to avoid unexpected outbound
cloud traffic.
"""

from main_logic.quota.dropper import on_text_message, on_utterance

__all__ = ["on_text_message", "on_utterance"]
