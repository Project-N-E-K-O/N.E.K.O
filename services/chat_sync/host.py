"""Host capabilities chat sync borrows from the application that runs it.

``services`` sits below the router layer, so chat sync never imports the routers
or the conversation core. The few host operations it needs are pushed down here
instead: each owner installs its own capability when it is imported, and chat
sync reads them through the accessors below.

Owners install late-binding callables that look up the owner's module attribute
on every call, so monkeypatching the owner still reaches chat sync and reloading
the owner simply installs again.

==========================  =====================================================
capability                  installed at import by
==========================  =====================================================
saved_session_snapshot      card_drop_router (``_desktop_session_snapshot``)
saved_session_writer_fence  card_drop_router (the credential writers' file locks)
resolve_saved_oauth_status  community_oauth (its single in-flight resolver)
get_session_manager         shared_state
get_init_one_catgirl        shared_state
conversation_busy           shared_state (``session_state.conversation_busy``)
rename_character            characters_router.crud (``_rename_catgirl_serialized``)
delete_character            characters_router.crud (``_delete_synced_character``)
==========================  =====================================================

An accessor whose capability is missing raises :class:`CapabilityNotInstalled`,
a ``RuntimeError`` like the shared-state registry's own "not initialized" error;
each consumer decides how it fails closed. Tests that need a capability to be
missing monkeypatch its slot to ``None``. Only the standard library is imported,
so any owner can install without an import cycle.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractContextManager
from typing import Any


class CapabilityNotInstalled(RuntimeError):
    """A host capability was used before its owner module was imported."""


_saved_session_snapshot: Callable[[], dict | None] | None = None
_saved_session_writer_fence: Callable[[], AbstractContextManager] | None = None
_resolve_saved_oauth_status: Callable[[], Awaitable[dict]] | None = None
_get_session_manager: Callable[[], Any] | None = None
_get_init_one_catgirl: Callable[[], Callable[..., Awaitable[Any]]] | None = None
_conversation_busy: Callable[[Any], bool] | None = None
_rename_character: Callable[..., Awaitable[Any]] | None = None
_delete_character: Callable[..., Awaitable[Any]] | None = None


def _callable(value, name: str):
    """Reject a result passed where the owner must pass the callable itself."""
    if not callable(value):
        raise TypeError(f"chat sync host capability {name!r} must be callable")
    return value


def _installed(value, name: str):
    """Return an installed capability or report which owner is missing."""
    if value is None:
        raise CapabilityNotInstalled(f"chat sync host capability {name!r} is not installed")
    return value


def install_saved_session_store(*, saved_session_snapshot, saved_session_writer_fence) -> None:
    """Installed by the credential store owner (card_drop_router)."""
    global _saved_session_snapshot, _saved_session_writer_fence
    snapshot = _callable(saved_session_snapshot, "saved_session_snapshot")
    fence = _callable(saved_session_writer_fence, "saved_session_writer_fence")
    _saved_session_snapshot, _saved_session_writer_fence = snapshot, fence


def install_oauth_status_resolver(*, resolve_saved_oauth_status) -> None:
    """Installed by the OAuth status owner (community_oauth)."""
    global _resolve_saved_oauth_status
    _resolve_saved_oauth_status = _callable(resolve_saved_oauth_status, "resolve_saved_oauth_status")


def install_conversation_registry(*, get_session_manager, get_init_one_catgirl, conversation_busy) -> None:
    """Installed by the live session registry owner (shared_state)."""
    global _get_session_manager, _get_init_one_catgirl, _conversation_busy
    managers = _callable(get_session_manager, "get_session_manager")
    init_one = _callable(get_init_one_catgirl, "get_init_one_catgirl")
    busy = _callable(conversation_busy, "conversation_busy")
    _get_session_manager, _get_init_one_catgirl, _conversation_busy = managers, init_one, busy


def install_rename_transaction(*, rename_character) -> None:
    """Installed by the character rename owner (characters_router.crud)."""
    global _rename_character
    _rename_character = _callable(rename_character, "rename_character")


def install_delete_transaction(*, delete_character) -> None:
    """Installed by the character owner; remote deletes reuse its full cleanup."""
    global _delete_character
    _delete_character = _callable(delete_character, "delete_character")


def saved_session_snapshot() -> dict | None:
    """Read the normalized saved desktop session; None when nobody is logged in."""
    return _installed(_saved_session_snapshot, "saved_session_snapshot")()


def saved_session_writer_fence() -> AbstractContextManager:
    """Return the cross-process lock that every credential writer also holds."""
    return _installed(_saved_session_writer_fence, "saved_session_writer_fence")()


def resolve_saved_oauth_status() -> Awaitable[dict]:
    """Join the owner's one refresh-capable resolution of the saved session."""
    return _installed(_resolve_saved_oauth_status, "resolve_saved_oauth_status")()


def get_session_manager() -> Any:
    """Return the live per-character session manager mapping."""
    return _installed(_get_session_manager, "get_session_manager")()


def get_init_one_catgirl() -> Callable[..., Awaitable[Any]]:
    """Return the fast single-character runtime refresh callable."""
    return _installed(_get_init_one_catgirl, "get_init_one_catgirl")()


def conversation_busy(manager: Any) -> bool:
    """Whether ``manager``'s conversation must keep its current prompt for now."""
    return _installed(_conversation_busy, "conversation_busy")(manager)


def rename_character(old_name: str, new_name: str, *, sync_commit=None) -> Awaitable[Any]:
    """Start the owner's serialized rename; the caller holds the config mutation lock."""
    return _installed(_rename_character, "rename_character")(old_name, new_name, sync_commit=sync_commit)


def delete_character(uid: str, before_delete) -> Awaitable[bool]:
    """Resolve the immutable identity and guard immediately before local cleanup."""
    return _installed(_delete_character, "delete_character")(uid, before_delete)
