"""In-memory stand-in for ``ctx.permissions`` in plugin unit tests.

    perms = FakePermissions({"plugin_sdk.main_agent.memory:read": "denied"})
    perms.get("plugin_sdk.main_agent.memory:read").status  # "denied"

Lets a plugin test its degraded paths instead of only the all-granted one.
Nothing here talks to a host.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import replace

from ..models.exceptions import PermissionDeniedError
from .catalog import PERMISSIONS
from .state import PermissionState, PermissionSubscription, is_async_callable, known_permission

StateLike = str | PermissionState | Mapping[str, object]
ChangeCallback = Callable[[str, PermissionState], object]


class FakePermissions:
    """Settable ``get`` / ``request`` / ``on_change`` over a dict of states.

    ``states`` maps a permission id to a status string, a ``PermissionState``
    or a mapping with ``status`` / ``background`` / ``enforced``. Ids not
    listed get ``default``. For a string, or a mapping without ``enforced``,
    ``enforced`` comes from the vocabulary (False for disclosure-only ids); a
    ``PermissionState`` is used as given. ``checks_disabled=True`` makes it
    False for every id, as the host's master switch does. ``answers`` is what ``request()`` resolves an id
    to, as if the user had answered; without one it returns the current state.
    Unknown ids raise ``ValueError`` so a typo fails the test.

    ``request()`` exists only here for now: the ``ctx.permissions`` of a real
    plugin process has ``get`` and ``on_change`` but no ``request`` yet, so a
    plugin that calls it passes against the fake and fails with
    ``AttributeError`` in a real process.
    """

    def __init__(
        self,
        states: Mapping[str, StateLike] | None = None,
        *,
        default: StateLike = "granted",
        answers: Mapping[str, StateLike] | None = None,
        checks_disabled: bool = False,
    ) -> None:
        self._checks_disabled = checks_disabled
        self._default = default
        self._states: dict[str, PermissionState] = {}
        self._answers: dict[str, StateLike] = {}
        self._callbacks: list[ChangeCallback] = []
        self.requests: list[tuple[str, bool]] = []
        self._coerce(next(iter(PERMISSIONS)), default)  # validate now
        for permission, state in (states or {}).items():
            self._states[_known(permission)] = self._coerce(permission, state)
        for permission, state in (answers or {}).items():
            self._coerce(permission, state)  # validate now
            self._answers[_known(permission)] = state

    def get(self, permission: str) -> PermissionState:
        permission = _known(permission)
        state = self._states.get(permission)
        return state if state is not None else self._coerce(permission, self._default)

    async def request(self, permission: str, *, wait: bool = True, timeout: float | None = None) -> PermissionState:
        permission = _known(permission)
        self.requests.append((permission, wait))
        if permission in self._answers:
            return self.set(permission, self._answers[permission])
        return self.get(permission)

    def on_change(self, callback: ChangeCallback) -> PermissionSubscription:
        if is_async_callable(callback):
            raise TypeError("FakePermissions.on_change callbacks must be synchronous")
        self._callbacks.append(callback)
        return PermissionSubscription(self._callbacks, callback)

    def set(self, permission: str, state: StateLike) -> PermissionState:
        """Change a state as the user would in the panel; notifies ``on_change``."""
        permission = _known(permission)
        new = self._coerce(permission, state)
        old = self.get(permission)
        self._states[permission] = new
        if new != old:
            for callback in list(self._callbacks):
                result = callback(permission, new)
                if inspect.iscoroutine(result):
                    result.close()
                    raise TypeError("FakePermissions.on_change callbacks must be synchronous")
        return new

    def denial(self, permission: str) -> PermissionDeniedError | None:
        """The error a host would refuse ``permission`` with in its current state, if any."""
        state = self.get(permission)
        if state.status in ("granted", "once") or not state.enforced:
            return None
        return PermissionDeniedError(permission=_known(permission), reason=state.status)

    def _coerce(self, permission: str, state: StateLike) -> PermissionState:
        enforced = PERMISSIONS[_known(permission)].enforced and not self._checks_disabled
        if isinstance(state, str):
            return PermissionState(state, enforced=enforced)  # type: ignore[arg-type]
        if isinstance(state, PermissionState):
            return replace(state, enforced=False) if self._checks_disabled else state
        if isinstance(state, Mapping):
            unknown = set(state) - {"status", "background", "enforced"}
            if unknown:
                raise ValueError(f"unknown permission state fields: {sorted(unknown)}")
            built = PermissionState(
                state["status"],  # type: ignore[arg-type]
                background=bool(state.get("background", False)),
                enforced=bool(state.get("enforced", enforced)),
            )
            return replace(built, enforced=False) if self._checks_disabled else built
        raise TypeError(f"unsupported permission state: {state!r}")


_known = known_permission


__all__ = ["FakePermissions"]
