"""Replay lifecycle guard mutations without changing production files.

Run: uv run python scripts/check_session_handoff_mutations.py
Each isolated child must fail its behavioral test with an assertion. Import,
syntax, timeout and cancellation errors do not count as killed mutations.
"""

import argparse
import inspect
import subprocess
import sys
import textwrap
from pathlib import Path

# Editable installations and inherited PYTHONPATH can point at another checkout.
# Bind this script and every child process to the worktree containing the script.
WORKTREE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(WORKTREE_ROOT))


CASES = {
    "runtime_fence": "test_old_runtime_audio_waiting_for_frame_lock_is_not_sent",
    "capacity": "test_retired_live_workers_keep_both_slots_until_real_exit",
    "cleanup_shield": "test_cancelling_teardown_caller_does_not_cancel_owned_cleanup",
    "notification_owner": "test_runtime_handoff_drains_status_blocked_inside_websocket_send",
    "hot_swap_callbacks": "test_hot_swap_close_drains_inflight_owned_output_before_promote",
    "queue_consumer": "test_retirement_keeps_wakeup_until_real_queue_consumer_exits",
}


def replace_method(owner, name, namespace, before, after):
    source = textwrap.dedent(inspect.getsource(getattr(owner, name)))
    if source.count(before) != 1:
        raise RuntimeError(f"Mutation anchor changed: {name}")
    exec(compile(source.replace(before, after), f"<mutation:{name}>", "exec"), namespace)
    setattr(owner, name, namespace[name])


def run_mutant(name):
    import pytest
    from main_logic.core.tts_runtime import TtsRuntimeMixin
    from main_logic.core.tts_lifecycle import TtsLifecycleMixin
    from main_logic.core.session_lifecycle import SessionOwnershipMixin
    import main_logic.core.tts_runtime as tts_module
    import main_logic.core.tts_lifecycle as tts_lifecycle_module
    import main_logic.core.session_lifecycle as session_module
    for module in (tts_module, tts_lifecycle_module, session_module):
        if not Path(module.__file__).resolve().is_relative_to(WORKTREE_ROOT):
            raise RuntimeError(f"Wrong worktree import: {module.__file__}")
    print(f"Mutation worktree: {WORKTREE_ROOT}", flush=True)

    if name == "runtime_fence":
        TtsLifecycleMixin._tts_runtime_is_current = lambda self, runtime: True
    elif name == "capacity":
        TtsLifecycleMixin._tts_capacity_limit = lambda self, worker=None: 99
    elif name == "cleanup_shield":
        replace_method(TtsRuntimeMixin, "_teardown_tts_runtime", dict(vars(tts_module)),
                       "asyncio.shield(runtime.cleanup_task)", "runtime.cleanup_task")
    elif name == "notification_owner":
        replace_method(TtsRuntimeMixin, "tts_response_handler", dict(vars(tts_module)),
                       "await self.send_status(data[1])", "self._fire_task(self.send_status(data[1]))")
    elif name == "hot_swap_callbacks":
        replace_method(SessionOwnershipMixin, "_close_connection_record", dict(vars(session_module)),
                       "callbacks = tuple(record.callbacks - {initiating_task})", "callbacks = ()")
    elif name == "queue_consumer":
        replace_method(TtsRuntimeMixin, "tts_response_handler", dict(vars(tts_module)),
                       "while not pending_get.done():\n"
                       "                    try:\n"
                       "                        await asyncio.shield(pending_get)\n"
                       "                    except asyncio.CancelledError:\n"
                       "                        pass\n"
                       "                pending_get.result()", "pass")

    class AssertionWitness:
        assertion_failed = False
        unexpected_failure = False

        @pytest.hookimpl(hookwrapper=True)
        def pytest_runtest_makereport(self, item, call):
            outcome = yield
            report = outcome.get_result()
            if report.failed:
                assertion = (call.when == "call" and call.excinfo is not None
                             and isinstance(call.excinfo.value, (AssertionError, pytest.fail.Exception)))
                self.assertion_failed |= assertion
                self.unexpected_failure |= not assertion

    witness = AssertionWitness()
    filename = ("test_session_callback_contract_review.py" if name == "hot_swap_callbacks"
                else "test_tts_handoff_ownership.py")
    result = pytest.main([f"tests/unit/{filename}::{CASES[name]}", "-q", "--tb=short"], plugins=[witness])
    killed = result == pytest.ExitCode.TESTS_FAILED and witness.assertion_failed and not witness.unexpected_failure
    print(f"MUTATION {name}: {'KILLED_BY_ASSERTION' if killed else 'INVALID_OR_SURVIVED'}", flush=True)
    return 0 if killed else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mutant", choices=tuple(CASES))
    args = parser.parse_args()
    if args.mutant:
        return run_mutant(args.mutant)
    root = Path(__file__).resolve().parents[1]
    failures = []
    for name in CASES:
        process = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--mutant", name], cwd=root)
        if process.returncode:
            failures.append(name)
    print(f"Mutation replay: {len(CASES) - len(failures)}/{len(CASES)} killed by assertions")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
