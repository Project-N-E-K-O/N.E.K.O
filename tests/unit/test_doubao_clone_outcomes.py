"""The clone endpoint's diagnostic fields are not proof of non-acceptance."""

import io

import httpx
import pytest

from utils.doubao_tts import DoubaoTtsError, DoubaoVoiceCloneClient


@pytest.mark.asyncio
@pytest.mark.parametrize("status,body,business", [
    (400, {"code": 1109, "message": "private upstream text"}, 1109),
    (403, {"code": "Denied", "message": "private upstream text"}, "Denied"),
    (500, {"code": "Internal"}, "Internal"),
    (200, {"code": 1109}, 1109),
    (200, {"code": 0, "data": {}}, 0),
    (200, [], None),
])
async def test_clone_errors_preserve_structured_diagnostics_without_inventing_rejection(
    monkeypatch, status, body, business,
):
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(status, json=body)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)))
    with pytest.raises(DoubaoTtsError) as caught:
        await DoubaoVoiceCloneClient("fake-key").clone_voice(io.BytesIO(b"wav"), speaker_id="S_isolated")
    error = caught.value
    assert error.attempt_outcome == "unknown"
    assert error.http_status == status
    assert error.business_code == business
    assert len(requests) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [httpx.ReadTimeout, httpx.WriteTimeout, httpx.ConnectError])
async def test_transport_errors_do_not_claim_a_confirmed_rejection(monkeypatch, kind):
    async def handle(request):
        raise kind("private transport diagnostic", request=request)

    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handle)))
    with pytest.raises(DoubaoTtsError) as caught:
        await DoubaoVoiceCloneClient("fake-key").clone_voice(io.BytesIO(b"wav"), speaker_id="S_isolated")
    assert caught.value.attempt_outcome == "unknown"
    assert caught.value.http_status is None
    assert caught.value.business_code is None


def test_legacy_error_call_sites_keep_the_base_exception_contract():
    error = DoubaoTtsError("native cloning still catches this")
    assert str(error) == "native cloning still catches this"
    assert error.attempt_outcome == "unknown"
