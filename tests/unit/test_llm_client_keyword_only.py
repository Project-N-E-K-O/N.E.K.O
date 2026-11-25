"""Client options after ``api_key`` are keyword-only.

``temperature`` used to be the fourth parameter. With it removed, an old call
passing a fourth positional value would otherwise land in ``streaming``
silently; it must fail loudly instead.
"""
from __future__ import annotations

import pytest

from utils.llm_client import ChatAnthropic, ChatOpenAI


@pytest.mark.unit
@pytest.mark.parametrize("client_cls", [ChatOpenAI, ChatAnthropic])
def test_fourth_positional_argument_is_rejected(client_cls):
    with pytest.raises(TypeError):
        client_cls("m", "https://api.example.test/v1", "k", 0.7)


@pytest.mark.unit
@pytest.mark.parametrize("client_cls", [ChatOpenAI, ChatAnthropic])
def test_model_endpoint_and_key_stay_positional(client_cls):
    client = client_cls("m", "https://api.example.test/v1", "k", streaming=True)
    assert client.model == "m"
