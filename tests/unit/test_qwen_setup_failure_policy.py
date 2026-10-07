"""Real worker setup failures must not classify unknown errors as transient."""

import asyncio
import errno
import socket
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client._infra import AsrSessionConfig
from main_logic.asr_client.recovery import FailureSource, RecoveryDisposition, classify_failure
from main_logic.asr_client.workers import qwen


pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize("error,expected", [
    (TimeoutError("no message inference"), RecoveryDisposition.RETRY_CONNECT),
    (ConnectionRefusedError(), RecoveryDisposition.RETRY_CONNECT),
    (socket.gaierror(), RecoveryDisposition.RETRY_CONNECT),
    (OSError(errno.ENETUNREACH, "network unreachable"), RecoveryDisposition.RETRY_CONNECT),
    (RuntimeError("timeout connection failed"), RecoveryDisposition.STOP),
    (ValueError("bad configuration"), RecoveryDisposition.STOP),
    (PermissionError(), RecoveryDisposition.STOP),
    (OSError("unknown"), RecoveryDisposition.STOP),
])
async def test_worker_connect_failure_retries_only_typed_network_errors(monkeypatch, error, expected):
    connect = AsyncMock(side_effect=error)
    monkeypatch.setattr(qwen.websockets, "connect", connect)
    responses = asyncio.Queue()
    await qwen.qwen_asr_worker(asyncio.Queue(), responses, "key", AsrSessionConfig())
    event = responses.get_nowait()
    assert event.kind == "error"
    assert classify_failure(event.error_code, source=FailureSource.CONNECT) is expected
    connect.assert_awaited_once()


@pytest.mark.parametrize("status,expected", [
    (400, "ASR_QWEN_SETUP_FAILED"),
    (401, "ASR_CREDENTIALS_REJECTED"),
    (403, "ASR_CREDENTIALS_REJECTED"),
    (404, "ASR_QWEN_SETUP_FAILED"),
    (429, "ASR_QWEN_CONNECTION_FAILED"),
    (500, "ASR_QWEN_CONNECTION_FAILED"),
    (503, "ASR_QWEN_CONNECTION_FAILED"),
])
async def test_worker_http_setup_status_is_classified_without_message_matching(monkeypatch, status, expected):
    error = RuntimeError("connection timeout retry me")
    error.response = SimpleNamespace(status_code=status)
    monkeypatch.setattr(qwen.websockets, "connect", AsyncMock(side_effect=error))
    responses = asyncio.Queue()
    await qwen.qwen_asr_worker(asyncio.Queue(), responses, "key", AsrSessionConfig())
    assert responses.get_nowait().error_code == expected


async def test_worker_session_update_configuration_failure_does_not_retry(monkeypatch):
    class Socket:
        send = AsyncMock(side_effect=ValueError("invalid setup"))
        closed = False
        close_calls = 0

        async def close(self):
            self.close_calls += 1
            self.closed = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            await asyncio.Event().wait()

    connection = Socket()
    connect = AsyncMock(return_value=connection)
    monkeypatch.setattr(qwen.websockets, "connect", connect)
    responses = asyncio.Queue()
    await qwen.qwen_asr_worker(asyncio.Queue(), responses, "key", AsrSessionConfig())
    assert responses.get_nowait().error_code == "ASR_QWEN_SETUP_FAILED"
    connect.assert_awaited_once()
    connection.send.assert_awaited_once()
    assert connection.close_calls == 1
    assert connection.closed
