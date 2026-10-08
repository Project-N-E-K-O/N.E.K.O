"""Windows termination evidence against real uv/Python trees and live pipes."""
import os
import subprocess

import pytest

from tests.unit import test_voice_overwrite_crashes as crash


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill contract")
def test_taskkill_nonzero_after_real_tree_exit_is_not_a_failed_crash(tmp_path, monkeypatch):
    with crash.provider() as (url, state, partial, ended):
        process = crash.start(tmp_path, url, "prepared")
        reader = None
        original_run = subprocess.run

        def exited_tree(command, **kwargs):
            actual = original_run(command, capture_output=True, timeout=5)
            process.wait(timeout=5)
            # Preserve the real kill and process/pipe checks. Inject only the
            # observed command failure, after the owned tree has exited.
            result = subprocess.CompletedProcess(command, 128, actual.stdout, b"controlled enumeration race")
            if kwargs.get("check"):
                result.check_returncode()
            return result

        try:
            reader = crash.wait_barrier(process, "prepared")
            with monkeypatch.context() as patch:
                patch.setattr(subprocess, "run", exited_tree)
                crash.terminate(process)
            assert process.poll() is not None
            reader.join(timeout=5)
            assert not reader.is_alive(), "A surviving descendant still owns stdout"
            assert state["accepted"] == 0
            assert crash.persisted(tmp_path)["overwrite_submission_phase"] == "prepared"
        finally:
            if process.poll() is None:
                crash.terminate(process)
            if reader:
                reader.join(timeout=5)
                assert not reader.is_alive()
            process.stdin.close()
            process.stdout.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows taskkill contract")
@pytest.mark.parametrize("command_code", [0, 128])
def test_taskkill_cannot_report_success_with_an_owned_tree_still_alive(tmp_path, monkeypatch, command_code):
    with crash.provider() as (url, state, partial, ended):
        process = crash.start(tmp_path, url, "prepared")
        reader = None

        def did_not_kill(command, **kwargs):
            result = subprocess.CompletedProcess(command, command_code, b"", b"controlled kill failure")
            if kwargs.get("check"):
                result.check_returncode()
            return result

        try:
            reader = crash.wait_barrier(process, "prepared")
            expected = subprocess.CalledProcessError if command_code else subprocess.TimeoutExpired
            with monkeypatch.context() as patch:
                patch.setattr(subprocess, "run", did_not_kill)
                with pytest.raises(expected):
                    crash.terminate(process)
            assert process.poll() is None
            assert reader.is_alive()
            assert state["accepted"] == 0
        finally:
            if process.poll() is None:
                crash.terminate(process)
            if reader:
                reader.join(timeout=5)
                assert not reader.is_alive()
            process.stdin.close()
            process.stdout.close()
