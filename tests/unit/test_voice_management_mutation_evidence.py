"""Parallel providers preserve diagnostics without inferring safe resubmission."""

import httpx
import pytest

from utils.voice_management.providers._shared import request_json
from utils.voice_management.providers.cosyvoice import CosyVoiceAdapter
from utils.voice_management.types import VoiceManagementError, VoiceRuntime


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(400, "UPSTREAM_REJECTED"), (401, "AUTH_FAILED"),
                                        (403, "PERMISSION_DENIED"), (429, "RATE_LIMITED")])
async def test_http_status_alone_is_not_mutation_rejection_evidence(monkeypatch, status, code):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(status, json={"secret": "hidden"})),
    ))
    with pytest.raises(VoiceManagementError) as caught:
        await request_json("POST", "https://isolated.example/update", mutation=True)
    assert caught.value.code == code
    assert caught.value.details == {"attempt_outcome": "unknown"}


@pytest.mark.asyncio
@pytest.mark.parametrize("business", ["InvalidApiKey", "Forbidden", "VoiceNotFound", "SomeOtherFailure"])
async def test_cosy_business_diagnostics_do_not_unlock_without_acceptance_contract(monkeypatch, business):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(
        **kwargs, transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"code": business})),
    ))
    runtime = VoiceRuntime("cosyvoice", "fake-key", "https://isolated.example", "scope", "bucket")
    with pytest.raises(VoiceManagementError) as caught:
        await CosyVoiceAdapter()._call(runtime, "update_voice", mutation=True, voice_id="fake-voice")
    assert caught.value.details == {"attempt_outcome": "unknown"}
