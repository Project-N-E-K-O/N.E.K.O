from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.sdk.shared.core.call_chain_view import (
    call_chain_scope,
    current_call_chain,
    self_only_view,
    view_from_wire,
)
from plugin.sdk.shared.core.context import SdkContext

pytestmark = pytest.mark.plugin_unit

WIRE = {
    "trace_id": "t-1",
    "call_id": "c-2",
    "depth": 1,
    "origin": {"kind": "run", "ref": "run-1"},
    "hops": [["caller", "plugin_entry", "call"], ["me", "plugin_entry", "read"]],
    "rights": ["memory.read"],
}


def test_outside_an_invocation_the_view_is_self_only_and_never_none():
    view = current_call_chain("me")
    assert view is not None
    assert view == self_only_view("me")
    assert [hop.plugin_id for hop in view.hops] == ["me"]
    assert view.callers == () and view.depth == 0
    assert view.origin.kind == "plugin_self" and view.origin.speaker is None


def test_unknown_rights_never_allow():
    view = self_only_view("me")
    assert view.rights_known is False and view.rights == frozenset()
    assert view.allows("memory.read") is False
    assert view.allows("anything") is False


def test_known_rights_allow_only_what_is_in_them():
    view = view_from_wire("me", WIRE)
    assert view.rights_known is True
    assert view.allows("memory.read") is True
    assert view.allows("messages.push") is False


def test_known_empty_rights_differ_from_unknown():
    view = view_from_wire("me", {**WIRE, "rights": []})
    assert view.rights_known is True and view.rights == frozenset()
    assert view_from_wire("me", {**WIRE, "rights": None}).rights_known is False


def test_wire_view_fields():
    view = view_from_wire("me", WIRE)
    assert view.callers == ("caller",)
    assert view.trace_id == "t-1" and view.call_id == "c-2" and view.depth == 1
    assert (view.origin.kind, view.origin.ref, view.origin.speaker) == ("run", "run-1", None)


@pytest.mark.parametrize(
    "payload",
    [None, "x", {}, {"hops": "x"}, {"hops": [["someone_else", "e", "x"]]}, {"hops": [[1, 2]]}],
)
def test_malformed_or_foreign_payloads_fall_back_to_self_only(payload):
    assert view_from_wire("me", payload) == self_only_view("me")


async def test_scope_is_closed_for_background_work_after_the_invocation():
    started = asyncio.Event()
    release = asyncio.Event()
    seen = []

    async def background():
        seen.append(current_call_chain("me").call_id)
        started.set()
        await release.wait()
        seen.append(current_call_chain("me").call_id)

    with call_chain_scope("me", WIRE):
        assert current_call_chain("me").call_id == "c-2"
        task = asyncio.create_task(background())
        await started.wait()
    release.set()
    await task
    assert seen == ["c-2", None]
    assert current_call_chain("me") == self_only_view("me")


def test_absent_payload_sets_nothing():
    with call_chain_scope("me", None):
        assert current_call_chain("me") == self_only_view("me")


def test_sdk_facade_delegates_and_falls_back():
    view = view_from_wire("me", WIRE)
    assert SdkContext(SimpleNamespace(plugin_id="me", call_chain=view)).call_chain is view
    assert SdkContext(SimpleNamespace(plugin_id="me")).call_chain == self_only_view("me")


def test_plugin_process_sets_the_view_around_every_entry_command():
    # The child command loop is a closure; pin that the one function every
    # TRIGGER / TRIGGER_CUSTOM goes through enters the view scope.
    source = Path(__file__).resolve().parents[3] / "core" / "host.py"
    tree = ast.parse(source.read_bytes())
    (func,) = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_command"
    ]
    withs = [node for node in ast.walk(func) if isinstance(node, ast.With)]
    assert any(
        isinstance(item.context_expr, ast.Call)
        and getattr(item.context_expr.func, "id", None) == "call_chain_scope"
        and any(
            isinstance(call, ast.Call) and getattr(call.func, "id", None) == "execute_entry_command"
            for stmt in node.body for call in ast.walk(stmt)
        )
        for node in withs for item in node.items
    )
