"""Opt-in pytest plugin for lifecycle guard counterexamples.

Run only explicitly with ``-p tests.session_handoff_mutations`` and
``NEKO_HANDOFF_MUTATION=<name>``. Mutations affect in-memory functions and are
restored after each test; production files and user configuration never change.
"""

import inspect
import os
import textwrap

import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    from main_logic.core.lifecycle import LifecycleMixin
    from main_logic.core.session_lifecycle import SessionOwnershipMixin
    from main_logic.core.streaming import StreamingMixin
    from main_logic.omni_realtime_client._response_arbiter import RealtimeResponseArbiter

    mutation = os.environ["NEKO_HANDOFF_MUTATION"]
    changes = {
        "capacity": (SessionOwnershipMixin, "_connect_owned_session", [
            ("if len(live) < (1 if serial else 2):", "if True:"),
        ]),
        "memory": (SessionOwnershipMixin, "_wait_session_handoff", [
            ("await asyncio.shield(record.memory_completion)", "pass"),
            ("await asyncio.shield(completion)", "pass"),
        ]),
        "finally": (SessionOwnershipMixin, "_finish_start_operation", [
            ("if self._start_operation is operation:", "if True:"),
        ]),
        "callback": (SessionOwnershipMixin, "_bind_owned_output_callbacks", [
            ("if session is not self.session or (record is not None and record.retired):", "if False:"),
        ]),
        "cancel_cleanup": (LifecycleMixin, "end_session", [
            ("await asyncio.shield(task)", "await task"),
        ]),
        "slot_detach": (SessionOwnershipMixin, "_retire_session_resources", [
            ("self.session = None", "pass"),
        ]),
        "pcm": (StreamingMixin, "_process_stream_data_internal", [
            ('if input_type == "audio" and any(', 'if False and any('),
        ]),
        "ready_input": (StreamingMixin, "_process_stream_data_internal", [
            ("if not owns_ready_flush:", "if True:"),
        ]),
        "input_commit": (StreamingMixin, "_flush_pending_input_data", [
            ("next_unprocessed = index + 1\n                    try:", "next_unprocessed = index\n                    try:"),
        ]),
        "live_order": (StreamingMixin, "_stream_data_now", [
            ('or getattr(self, "_pending_input_flush_scheduled", None) is not None', 'or False'),
        ]),
        "nested_output": (SessionOwnershipMixin, "_bind_owned_output_callbacks", [
            ("registered_here = record is not None and task not in record.callbacks", "registered_here = record is not None"),
        ]),
        "nested_lifecycle": (SessionOwnershipMixin, "_run_owned_lifecycle_callback", [
            ("registered_here = record is not None and task not in record.callbacks", "registered_here = record is not None"),
        ]),
        "external_ticket_permit": (RealtimeResponseArbiter, "allow_ticket_while_paused", [
            ("queued.dispatch_while_paused = True", "pass"),
        ]),
        "external_prepare_barrier": (RealtimeResponseArbiter, "_can_dispatch", [
            ("self._turn_preparations == 0 and (", "True and ("),
        ]),
    }
    owner, name, replacements = changes[mutation]
    original = getattr(owner, name)
    source = textwrap.dedent(inspect.getsource(original))
    for before, after in replacements:
        if before not in source:
            raise RuntimeError(f"Mutation {mutation} does not match current source: {before}")
        source = source.replace(before, after)
    namespace = dict(original.__globals__)
    exec(compile(source, f"<handoff-mutation:{mutation}>", "exec"), namespace)
    setattr(owner, name, namespace[name])
    try:
        yield
    finally:
        setattr(owner, name, original)
