"""A generator written for SDK 0.1.0 may still pass a temperature to call_llm."""
from __future__ import annotations

from services.theater_workshop.sdk.model import ModelAgent, ModelReply


def test_call_llm_drops_a_temperature_before_calling_the_model():
    received: list[dict] = []

    def model_call(messages, **options):
        received.append(options)
        return ModelReply("{}", "test")

    agent = ModelAgent("test", model_call)
    assert agent.call_llm([], temperature=0.35, max_tokens=10, operation="probe") == "{}"
    assert received == [{"max_tokens": 10, "operation": "probe"}]


def test_host_model_call_without_a_temperature_parameter_still_works():
    # NekoWorkshopModel.__call__ no longer accepts temperature; an old
    # generator's value must not reach it as an unexpected keyword.
    def strict_model_call(messages, *, max_tokens, operation):
        return ModelReply("ok", "test")

    agent = ModelAgent("test", strict_model_call)
    assert agent.call_llm([], temperature=0.1, max_tokens=5, operation="probe") == "ok"
