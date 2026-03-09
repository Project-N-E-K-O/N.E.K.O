"""Shared transport building blocks for SDK v2.

This package exposes shared transport facades backed by public implementations.
"""

from .message_plane import MessageHandler, MessagePlaneRpcClient, MessagePlaneTransport, format_rpc_error

__all__ = ["MessagePlaneTransport", "MessageHandler", "MessagePlaneRpcClient", "format_rpc_error"]
