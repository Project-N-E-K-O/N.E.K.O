# -*- coding: utf-8 -*-
"""Truth table of the native google-genai route test.

The offline client (``_should_use_genai_sdk``) and the background warmup both
decide with ``is_native_genai_route``; this pins what counts as native.
"""
from __future__ import annotations

import pytest

from utils.genai_route import is_native_genai_route

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(("model", "base_url", "native"), [
    ("anything", "https://generativelanguage.googleapis.com/v1beta", True),
    ("anything", "https://us-central1-aiplatform.googleapis.com/v1", True),
    ("x", "HTTPS://GENERATIVELANGUAGE.GOOGLEAPIS.COM", True),
    ("gemini-2.5-pro", "", True),
    ("Gemini-2.5-Flash", None, True),
    ("google/gemini-2.5-flash", "https://openrouter.ai/api/v1", False),
    ("gemini-2.5-flash", "https://lanlan.app/v1", False),
    ("gpt-4.1-mini", "", False),
    ("gpt-4.1-mini", "https://api.openai.com/v1", False),
    (None, None, False),
])
def test_native_genai_route_truth_table(model, base_url, native):
    assert is_native_genai_route(model, base_url) is native
