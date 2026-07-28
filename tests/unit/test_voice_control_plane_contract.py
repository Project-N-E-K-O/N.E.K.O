# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Structural gate: the microphone control plane follows the LEASE.

``manager.websocket`` is reassigned to every newly accepted socket, so it is
the DISPLAY plane -- "the newest window", not "the window holding the
microphone". Three separate review rounds on #2345 were spent rediscovering
the same missing invariant: a notification that stops or changes the
microphone has to reach the voice-lease holder, and there is no broadcast to
fall back on (``sync_message_queue`` feeds monitor viewers on a different
port; no app window connects there).

Comments cannot enforce that, so this makes it a test failure:

MIC_TEARDOWN_ROUTES_TO_LEASE
    Every function in ``main_logic/core`` that builds a payload whose
    ``"type"`` is in :data:`MIC_TEARDOWN_PAYLOAD_TYPES` must also route it to
    the voice owner in the same function -- a ``_send_to_voice_owner`` call,
    or the ``getattr(self, "_send_to_voice_owner", ...)`` late-bound spelling
    the lifecycle mixin uses. Adding a NEW teardown notification therefore
    fails here until it is routed, instead of being caught by review.

MIC_TEARDOWN_REGISTRY_IS_HONEST
    Every registered type is actually constructed somewhere, so the registry
    cannot rot into a list of names that no longer exist and quietly stop
    gating anything.

The registry is deliberately explicit rather than inferred: adding a type to
it is a conscious edit, the same shape as ``check_core_contracts.py``'s
registered owner modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

CORE_DIR = Path(__file__).resolve().parents[2] / "main_logic" / "core"

# Payload types whose arrival is what makes a client stop or hand over the
# microphone. Keep the reason next to each one.
MIC_TEARDOWN_PAYLOAD_TYPES = {
    # Server terminated the session; the recorder must drop the hardware mic.
    "session_ended_by_server",
    # Silence timeout closed the mic while the display window may be a chat
    # window that never had one.
    "auto_close_mic",
    # A text session pins the route fail-closed for its whole life, so the ack
    # doubles as the recorder's mic-stop. (Audio-mode session_started is the
    # same payload type and is display-plane by nature; the enclosing function
    # routes both, so the type-level gate is still the right granularity.)
    "session_started",
}

VOICE_OWNER_SENDER = "_send_to_voice_owner"

# The fail-closed chokepoint delivers to the lease holder on its callers'
# behalf (that ordering is the reason it exists), so handing it the payload
# counts as routing. The chain is only honest if the chokepoint itself still
# reaches the sender, which is pinned separately below.
ROUTING_CHOKEPOINT = "_fail_closed_voice_route"
ROUTING_NAMES = {VOICE_OWNER_SENDER, ROUTING_CHOKEPOINT}


def _core_modules() -> list[Path]:
    return sorted(p for p in CORE_DIR.glob("*.py") if p.name != "__init__.py")


def _payload_types_in(node: ast.AST) -> set[str]:
    """Every ``{"type": "<literal>"}`` built anywhere inside ``node``."""

    found: set[str] = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Dict):
            continue
        for key, value in zip(sub.keys, sub.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == "type"
                and isinstance(value, ast.Constant)
                and isinstance(value.value, str)
            ):
                found.add(value.value)
    return found


def _routes_to_voice_owner(node: ast.AST, *, names: set[str] | None = None) -> bool:
    """True if ``node`` reaches the voice owner, directly or by delegation."""

    wanted = ROUTING_NAMES if names is None else names
    for sub in ast.walk(node):
        # self._send_to_voice_owner(...) / self._fail_closed_voice_route(...)
        if isinstance(sub, ast.Attribute) and sub.attr in wanted:
            return True
        # getattr(self, "_send_to_voice_owner", None) -- the late-bound form
        # the lifecycle mixin uses for managers without the notify mixin.
        if (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and sub.value in wanted
        ):
            return True
    return False


def _functions_building_teardowns() -> list[tuple[str, str, ast.AST, set[str]]]:
    """(module, function, node, teardown types it builds) for the whole package."""

    hits: list[tuple[str, str, ast.AST, set[str]]] = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            types = _payload_types_in(node) & MIC_TEARDOWN_PAYLOAD_TYPES
            if types:
                hits.append((path.name, node.name, node, types))
    return hits


@pytest.mark.unit
def test_every_mic_teardown_notification_routes_to_the_lease_holder():
    offenders = [
        f"{module}::{function} builds {sorted(types)} but never reaches "
        f"{VOICE_OWNER_SENDER}"
        for module, function, node, types in _functions_building_teardowns()
        if not _routes_to_voice_owner(node)
    ]
    assert not offenders, (
        "A microphone-teardown notification must follow the voice LEASE, not "
        "manager.websocket (the display plane, reassigned to every new "
        "socket). Route it with "
        f"{VOICE_OWNER_SENDER} -- there is no broadcast fallback; "
        "sync_message_queue feeds monitor viewers on MONITOR_SERVER_PORT and "
        "no app window connects there. Offenders:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_the_chokepoint_itself_reaches_the_voice_owner():
    # Callers are allowed to discharge the contract by delegating to
    # _fail_closed_voice_route, so the chain dangles the moment the chokepoint
    # stops actually sending. Pin that last link.
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == ROUTING_CHOKEPOINT
            ):
                assert _routes_to_voice_owner(node, names={VOICE_OWNER_SENDER}), (
                    f"{path.name}::{ROUTING_CHOKEPOINT} is what its callers rely "
                    f"on to reach the lease holder, but it no longer calls "
                    f"{VOICE_OWNER_SENDER}."
                )
                return
    pytest.fail(
        f"{ROUTING_CHOKEPOINT} not found in main_logic/core -- it is the "
        "delegation target this gate accepts on callers' behalf, so its "
        "disappearance silently widens the contract."
    )


@pytest.mark.unit
def test_the_teardown_registry_still_matches_real_payloads():
    built: set[str] = set()
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        built |= _payload_types_in(tree)
    stale = MIC_TEARDOWN_PAYLOAD_TYPES - built
    assert not stale, (
        "These registered mic-teardown payload types are no longer built "
        f"anywhere in main_logic/core, so they gate nothing: {sorted(stale)}. "
        "Remove them, or fix the rename that orphaned them."
    )


@pytest.mark.unit
def test_the_gate_would_catch_an_unrouted_teardown():
    # The gate is only worth having if it actually fails on the shape it
    # exists to reject, so exercise both directions on synthetic sources
    # rather than trusting the production tree to stay red-capable.
    unrouted = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'session_ended_by_server'}\n"
        "    await self.websocket.send_text(json.dumps(payload))\n"
    )
    routed = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'session_ended_by_server'}\n"
        "    await self.websocket.send_text(json.dumps(payload))\n"
        "    await self._send_to_voice_owner(payload)\n"
    )
    late_bound = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'auto_close_mic'}\n"
        "    sender = getattr(self, '_send_to_voice_owner', None)\n"
        "    if callable(sender):\n"
        "        await sender(payload)\n"
    )

    delegated = ast.parse(
        "async def send_it(self):\n"
        "    await self._fail_closed_voice_route(\n"
        "        'text_session_active',\n"
        "        operation_generation=1,\n"
        "        voice_owner_notice={'type': 'session_started'},\n"
        "    )\n"
    )

    assert _payload_types_in(unrouted) & MIC_TEARDOWN_PAYLOAD_TYPES
    assert _routes_to_voice_owner(unrouted) is False
    assert _routes_to_voice_owner(routed) is True
    assert _routes_to_voice_owner(late_bound) is True
    assert _routes_to_voice_owner(delegated) is True
    # Delegation is not a way to satisfy the direct-send requirement the
    # chokepoint itself is held to.
    assert _routes_to_voice_owner(delegated, names={VOICE_OWNER_SENDER}) is False
