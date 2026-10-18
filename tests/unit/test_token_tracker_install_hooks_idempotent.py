"""Regression: ``install_hooks()`` must be idempotent, without stacking monkeypatches.

Merged single-process mode (packaged / Steam; see launcher's _run_merged) runs main / memory /
agent uvicorn apps in one process. Each app's startup calls
``install_hooks()`` against the same process-wide ``Completions.create``. Without an idempotence guard,
wrappers stack and each chat.completions call is recorded repeatedly, inflating telemetry for hooked
call types such as conversation / emotion / proactive / galgame_options by exactly N
(observed ×3 in the online three-app Steam build).

This test pins repeated ``install_hooks()`` calls to one record per create call.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import utils.token_tracker as tt
import utils.token_tracker._shared as tracker_shared


@pytest.fixture
def restore_openai_create():
    """Isolate global side effects: restore patched SDK methods after the test."""
    from openai.resources.chat.completions import AsyncCompletions, Completions

    orig_sync = Completions.create
    orig_async = AsyncCompletions.create
    try:
        yield Completions, AsyncCompletions
    finally:
        Completions.create = orig_sync
        AsyncCompletions.create = orig_async


def _fake_usage_response():
    usage = SimpleNamespace(
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        prompt_tokens_details=None,
    )
    return SimpleNamespace(usage=usage, model="fake-model")


def test_install_hooks_idempotent_no_stacking(restore_openai_create):
    Completions, _AsyncCompletions = restore_openai_create

    # Use a fake network-free create as the original SDK method (without our hook marker).
    def fake_create(self, *args, **kwargs):
        return _fake_usage_response()

    Completions.create = fake_create

    rec = MagicMock()
    fake_tracker = SimpleNamespace(record=rec)

    with patch.object(tt.TokenTracker, "get_instance", return_value=fake_tracker):
        # Simulate merged mode: main / memory / agent startups each install once.
        tt.install_hooks()
        installed_once = Completions.create
        tt.install_hooks()
        tt.install_hooks()

        # Guard works: installations 2 and 3 are no-ops; function identity stays unchanged with no added wrapper.
        assert Completions.create is installed_once
        assert getattr(Completions.create, "_neko_token_tracker_hooked", False) is True

        # One nonstreaming call.
        Completions.create(SimpleNamespace(), model="fake-model", stream=False)

    # Key invariant: exactly one record, not three (stacked wrappers would produce 3).
    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}（wrapper 叠层）"


def test_install_hooks_records_once_after_single_install(restore_openai_create):
    """Single-install baseline: one record per call, contrasting with stacked installations."""
    Completions, _AsyncCompletions = restore_openai_create

    def fake_create(self, *args, **kwargs):
        return _fake_usage_response()

    Completions.create = fake_create
    rec = MagicMock()

    with patch.object(
        tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
    ):
        tt.install_hooks()
        Completions.create(SimpleNamespace(), model="fake-model", stream=False)

    assert rec.call_count == 1


@pytest.mark.asyncio
async def test_install_hooks_idempotent_async_path(restore_openai_create):
    """The async path must not stack either.

    Production conversation uses LangChain astream → AsyncCompletions.create, making async
    the primary path. It shares _neko_token_tracker_hooked with the sync branch, but test it separately
    so future changes to only one branch cannot silently lose coverage.
    """
    _Completions, AsyncCompletions = restore_openai_create

    async def fake_async_create(self, *args, **kwargs):
        return _fake_usage_response()

    AsyncCompletions.create = fake_async_create
    rec = MagicMock()

    with patch.object(
        tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
    ):
        # Merged mode: main / memory / agent startups each install once.
        tt.install_hooks()
        installed_once = AsyncCompletions.create
        tt.install_hooks()
        tt.install_hooks()

        # Symmetric with the sync test: installs 2 and 3 are no-ops, preserving async function identity without extra wrappers,
        # and the guard marker is set. Without both assertions, removing the async guard while the wrapper
        # still works once could escape detection by record counts alone.
        assert AsyncCompletions.create is installed_once
        assert getattr(AsyncCompletions.create, "_neko_token_tracker_hooked", False) is True

        await AsyncCompletions.create(SimpleNamespace(), model="fake-model", stream=False)

    assert rec.call_count == 1, f"expected 1 record, got {rec.call_count}（async wrapper 叠层）"


def test_record_anthropic_usage_maps_messages_usage_fields():
    rec = MagicMock()

    with patch.object(
        tt.TokenTracker, "get_instance", return_value=SimpleNamespace(record=rec)
    ), tt.llm_call_context("conversation"):
        tt.record_anthropic_usage(
            "claude-test",
            {
                "input_tokens": 100,
                "output_tokens": 25,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 80,
            },
        )

    rec.assert_called_once_with(
        model="claude-test",
        prompt_tokens=200,
        completion_tokens=25,
        total_tokens=225,
        cached_tokens=80,
        call_type="conversation",
    )


def test_file_lock_closes_descriptor_after_initial_write_failure(monkeypatch, tmp_path):
    """A failed lock metadata write must not leak its opened descriptor."""
    real_write = tracker_shared.os.write
    real_close = tracker_shared.os.close
    write_attempts = 0
    closed_descriptors = []

    def flaky_write(fd, data):
        nonlocal write_attempts
        write_attempts += 1
        if write_attempts == 1:
            raise OSError("simulated lock metadata write failure")
        return real_write(fd, data)

    def tracked_close(fd):
        closed_descriptors.append(fd)
        return real_close(fd)

    monkeypatch.setattr(tracker_shared.os, "write", flaky_write)
    monkeypatch.setattr(tracker_shared.os, "close", tracked_close)

    with tracker_shared._file_lock(tmp_path / "token-tracker.lock"):
        pass

    assert write_attempts == 2
    assert len(closed_descriptors) == 2
