"""Pipe polling retires exited commands without dropping their final output."""

from types import SimpleNamespace

from plugin.server.infrastructure import process_output as module


def test_exit_during_empty_pipe_wait_still_drains_final_bytes(monkeypatch):
    process = SimpleNamespace(returncode=None, stdout=SimpleNamespace(fileno=lambda: 123))
    process.poll = lambda: process.returncode
    reads = []

    def read_ready(_limit):
        reads.append(True)
        if len(reads) == 1:
            # The pipe was empty when peeked. The child writes and exits during
            # the short wait before the next peek, as happens on Windows.
            process.returncode = 0
            return None
        assert len(reads) == 2
        return b"final-output\n"

    monkeypatch.setattr(module, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(module, "_windows_pipe_reader", lambda fd: (read_ready, lambda: len(b"final-output\n")))

    assert "".join(module.iter_process_output(process, checkpoint=lambda: None)) == "final-output\n"
    assert len(reads) == 2


def test_exit_drains_buffered_snapshot_without_following_later_helper_output(monkeypatch):
    process = SimpleNamespace(stdout=SimpleNamespace(fileno=lambda: 123), poll=lambda: 0)
    payload = b"p" * (2 * module._CHUNK_BYTES)
    chunks = [payload[:module._CHUNK_BYTES], payload[module._CHUNK_BYTES:], b"parent-last\n"]
    snapshot_size = sum(map(len, chunks))
    reads = []

    class Selector:
        def register(self, *_args):
            pass

        def select(self, _timeout):
            return [True]

        def close(self):
            pass

    def read(_fd, limit):
        reads.append(limit)
        return chunks.pop(0) if chunks else b"helper-continues-forever\n"

    monkeypatch.setattr(module, "os", SimpleNamespace(name="posix", read=read))
    monkeypatch.setattr(module.selectors, "DefaultSelector", Selector)
    monkeypatch.setattr(module, "_posix_buffered_bytes", lambda _fd: snapshot_size, raising=False)

    result = "".join(module.iter_process_output(process, checkpoint=lambda: None))
    assert result == payload.decode() + "parent-last\n"
    assert reads == [module._CHUNK_BYTES, module._CHUNK_BYTES, len(b"parent-last\n")]
