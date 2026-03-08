"""Lightweight shared facade for message-plane transport."""

from plugin.sdk_v2.public.transport.message_plane import MessageHandler, MessagePlaneTransport

__all__ = ["MessagePlaneTransport", "MessageHandler"]
