from __future__ import annotations

from plugin.runs import manager


def test_resolve_run_execution_timeout_uses_ctx_entry_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": 600}}) == 600.0


def test_resolve_run_execution_timeout_falls_back_for_zero_ctx_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": 0}}) == 300.0


def test_resolve_run_execution_timeout_falls_back_for_invalid_ctx_timeout(
    monkeypatch,
) -> None:
    monkeypatch.setattr(manager, "RUN_EXECUTION_TIMEOUT", 300.0)

    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": "bad"}}) == 300.0
    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": -1}}) == 300.0
    assert manager._resolve_run_execution_timeout({"_ctx": {"entry_timeout": True}}) == 300.0
