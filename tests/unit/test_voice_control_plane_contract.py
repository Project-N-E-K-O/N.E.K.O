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

MIC_PLANES_STAY_INDEPENDENT
    Routing to the lease holder is necessary but not sufficient: the two sends
    must also not short-circuit each other. Four instances of that second
    defect were found by hand on this PR, and the routing rule above is blind
    to every one of them -- ``send_session_started`` routed correctly and was
    still broken. Two shapes are rejected:

      Variant A (shared try) -- the lease-holder send sits in the same ``try``
      body as an earlier, unguarded display send, so any exception from the
      display send skips it. Starlette does not raise ``WebSocketDisconnect``
      from ``send``; an already-closed socket raises ``RuntimeError``, so a
      bare ``except WebSocketDisconnect`` is not the only arm that matters.

      Variant B (guard-nested) -- the lease-holder send sits inside an ``if``
      testing display-socket liveness, so a display socket that is simply GONE
      (rather than raising) drops the teardown entirely.

    KNOWN GAPS, stated so a later reviewer does not rediscover them as new:

      * The liveness PROBE itself (``self.websocket.client_state == ...``) is
        still bare inside the shared ``try`` at every site. A probe that raised
        would swallow the lease-holder send. Deliberately not flagged: it is
        effectively unreachable against real Starlette objects, and flagging it
        would make the gate unable to go green.
      * Display sends reached through a WRAPPER are invisible here. The live
        example is ``AsrRuntimeMixin._send_voice_control_status``, which awaits
        ``self.send_status(...)`` before its voice-plane send with no ``try``
        at all; it is safe only because ``send_status`` swallows its own
        exceptions internally. This gate mechanizes the lexically obvious
        subset -- which is precisely the subset that recurred four times -- and
        does not replace review for the wrapper-mediated path.
      * Position is compared by line number, and mutually exclusive if/else
        branches are not modelled, so a display send in a sibling branch would
        false-positive. No such site exists today.

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


# --------------------------------------------------------------- isolation
# Methods that put bytes on a socket. Wrapper helpers (send_status,
# _send_voice_control_status) are deliberately NOT here -- see KNOWN GAPS.
DISPLAY_SEND_METHODS = {"send_text", "send_json", "send_bytes", "send"}
# Names that make an expression a reference to the display socket.
DISPLAY_SOCKET_NAMES = {"websocket", "client_state"}


def _parent_map(node: ast.AST) -> dict[int, ast.AST]:
    parents: dict[int, ast.AST] = {}
    for parent in ast.walk(node):
        for child in ast.iter_child_nodes(parent):
            parents[id(child)] = parent
    return parents


def _ancestors(node: ast.AST, parents: dict[int, ast.AST]) -> list[ast.AST]:
    chain: list[ast.AST] = []
    current = parents.get(id(node))
    while current is not None:
        chain.append(current)
        current = parents.get(id(current))
    return chain


def _mentions_display_socket(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in DISPLAY_SOCKET_NAMES:
            return True
        if isinstance(sub, ast.Name) and sub.id in DISPLAY_SOCKET_NAMES:
            return True
    return False


def _display_sends_under(node: ast.AST) -> list[ast.Call]:
    sends: list[ast.Call] = []
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        func = sub.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr in DISPLAY_SEND_METHODS
            and _mentions_display_socket(func.value)
        ):
            sends.append(sub)
    return sends


def _voice_plane_markers(node: ast.AST) -> list[ast.AST]:
    """Every syntactic reference to a lease-holder delivery, incl. late-bound."""

    markers: list[ast.AST] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in ROUTING_NAMES:
            markers.append(sub)
        elif (
            isinstance(sub, ast.Constant)
            and isinstance(sub.value, str)
            and sub.value in ROUTING_NAMES
        ):
            markers.append(sub)
    return markers


def _reaches_via_body(node: ast.AST, block: ast.Try, parents: dict[int, ast.AST]) -> bool:
    """True if ``node`` sits in ``block``'s try BODY, not a handler/else/finally.

    A voice-plane send inside ``except`` is downstream of the failure, so it
    cannot be short-circuited by it.
    """

    current = node
    parent = parents.get(id(current))
    while parent is not None and parent is not block:
        current = parent
        parent = parents.get(id(current))
    if parent is not block:
        return False
    return any(current is stmt for stmt in block.body)


def _protected_by_inner_try(
    send: ast.AST,
    outer: ast.Try,
    parents: dict[int, ast.AST],
) -> bool:
    """True if ``send`` has its own ``try`` strictly inside ``outer``."""

    for ancestor in _ancestors(send, parents):
        if ancestor is outer:
            return False
        if isinstance(ancestor, ast.Try):
            return True
    return False


def _isolation_violations(fn: ast.AST) -> list[str]:
    parents = _parent_map(fn)
    problems: list[str] = []
    for marker in _voice_plane_markers(fn):
        chain = _ancestors(marker, parents)
        for ancestor in chain:
            if isinstance(ancestor, ast.If) and _mentions_display_socket(ancestor.test):
                problems.append(
                    f"line {marker.lineno}: variant B -- lease-holder send is nested "
                    f"inside the display-liveness guard at line {ancestor.lineno}, so a "
                    f"display socket that is simply GONE drops the teardown entirely"
                )
                break
        for ancestor in chain:
            if not isinstance(ancestor, ast.Try):
                continue
            if not _reaches_via_body(marker, ancestor, parents):
                continue
            unguarded = [
                send
                for stmt in ancestor.body
                for send in _display_sends_under(stmt)
                if send.lineno < marker.lineno
                and not _protected_by_inner_try(send, ancestor, parents)
            ]
            if unguarded:
                problems.append(
                    f"line {marker.lineno}: variant A -- shares the try at line "
                    f"{ancestor.lineno} with an unguarded display send at line "
                    f"{unguarded[0].lineno}, so that send raising skips the "
                    f"lease holder's copy"
                )
                break
    return problems


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
def test_the_two_planes_never_short_circuit_each_other():
    offenders = []
    for path in _core_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for problem in _isolation_violations(node):
                offenders.append(f"{path.name}::{node.name} {problem}")
    assert not offenders, (
        "The display plane and the microphone control plane are INDEPENDENT "
        "best-effort sends. Give the display send its own try/except, and keep "
        "the lease-holder send out of any guard on display-socket liveness -- "
        "otherwise a dead chat window silently leaves a live hardware "
        "microphone uploading into a route that discards every frame. "
        "Offenders:\n  " + "\n  ".join(offenders)
    )


@pytest.mark.unit
def test_the_isolation_gate_catches_both_defect_shapes():
    # This gate exists because four instances shipped past review, so it has to
    # be demonstrably red-capable on both shapes and quiet on the fixed form.
    variant_a = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'session_ended_by_server'}\n"
        "    try:\n"
        "        if self.websocket.client_state == CONNECTED:\n"
        "            await self.websocket.send_text(json.dumps(payload))\n"
        "        await self._send_to_voice_owner(payload)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    variant_b = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'auto_close_mic'}\n"
        "    if self.websocket and self.websocket.client_state == CONNECTED:\n"
        "        try:\n"
        "            await self.websocket.send_json(payload)\n"
        "        except Exception:\n"
        "            pass\n"
        "        await self._send_to_voice_owner(payload)\n"
    )
    fixed = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'session_ended_by_server'}\n"
        "    try:\n"
        "        if self.websocket.client_state == CONNECTED:\n"
        "            try:\n"
        "                await self.websocket.send_text(json.dumps(payload))\n"
        "            except Exception:\n"
        "                pass\n"
        "        await self._send_to_voice_owner(payload)\n"
        "    except Exception:\n"
        "        pass\n"
    )
    # A voice-plane send in an except handler is downstream of the failure, so
    # it must not be flagged.
    in_handler = ast.parse(
        "async def send_it(self):\n"
        "    payload = {'type': 'auto_close_mic'}\n"
        "    try:\n"
        "        await self.websocket.send_text(json.dumps(payload))\n"
        "    except Exception:\n"
        "        await self._send_to_voice_owner(payload)\n"
    )

    def problems(mod):
        fn = mod.body[0]
        return _isolation_violations(fn)

    assert any("variant A" in p for p in problems(variant_a))
    assert any("variant B" in p for p in problems(variant_b))
    assert problems(fixed) == []
    assert problems(in_handler) == []


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
