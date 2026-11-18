"""Keep private inference inputs out of shared encoder diagnostics."""

import logging

import pytest

from memory.embeddings import EmbeddingService

pytestmark = pytest.mark.unit_fast


def test_shared_encoder_nonsticky_error_logs_type_without_exception_text(caplog):
    """Exercise actual diagnostics without constructing or loading native models."""
    encoder = object.__new__(EmbeddingService)
    # Open the soft-failure log gate without replacing a process-wide clock.
    encoder._soft_failure_logged_at = float("-inf")
    secret = "Private-Vector-Input-Canary-1907"
    caplog.set_level(logging.WARNING)
    encoder._on_inference_error(RuntimeError(secret), sticky=False)
    assert "RuntimeError" in caplog.text
    assert secret not in caplog.text
