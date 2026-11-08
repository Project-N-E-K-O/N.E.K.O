"""Pipe polling retires exited commands without dropping their final output."""

from types import SimpleNamespace

from plugin.server.infrastructure import process_output as module


def test_exit_during_empty_pipe_wait_still_drains_final_bytes(monkeypatch):
    process = SimpleNamespace(returncode=None, stdout=SimpleNamespace(fileno=lambda: 123))
    process.poll = lambda: process.returncode
    reads = []

    def read_ready():
        reads.append(True)
        if len(reads) == 1:
            # The pipe was empty when peeked. The child writes and exits during
            # the short wait before the next peek, as happens on Windows.
            process.returncode = 0
            return None
        assert len(reads) == 2
        return b"final-output\n"

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_windows_pipe_reader", lambda fd: read_ready)

    assert "".join(module.iter_process_output(process, checkpoint=lambda: None)) == "final-output\n"
    assert len(reads) == 2
