"""Provider registration changes fault semantics without shared vendor branches."""

from dataclasses import replace

import pytest

from main_logic.asr_client._registry_meta import ASR_PROVIDER_REGISTRY, AsrFailureRule
from main_logic.asr_client.recovery import FailureSource, RecoveryDisposition, decide_failure


@pytest.mark.parametrize("source", list(FailureSource))
@pytest.mark.parametrize("risk", [None, "ASR_INPUT_DELIVERY_UNCERTAIN"])
def test_registered_faults_keep_provenance_and_delivery_independent(monkeypatch, source, risk):
    registered = replace(
        ASR_PROVIDER_REGISTRY["qwen"],
        provider_key="example",
        failure_rules=(
            ("ASR_EXAMPLE_READ_LOST", AsrFailureRule(recover_on_provider_failure=True)),
            ("ASR_EXAMPLE_CONNECT_FAILED", AsrFailureRule(retry_connect=True)),
            ("ASR_EXAMPLE_WRITE_FAILED", AsrFailureRule(use_delivery_notice=True)),
        ),
    )
    monkeypatch.setitem(ASR_PROVIDER_REGISTRY, "example", registered)
    for code, permitted_source, disposition in (
        ("ASR_EXAMPLE_READ_LOST", FailureSource.PROVIDER, RecoveryDisposition.RECOVER),
        ("ASR_EXAMPLE_CONNECT_FAILED", FailureSource.CONNECT, RecoveryDisposition.RETRY_CONNECT),
    ):
        decision = decide_failure(code, source=source, delivery_risk=risk)
        assert decision.recovery_disposition is (
            disposition if source is permitted_source else RecoveryDisposition.STOP
        )
        assert decision.cause_code == decision.notification_code == code
        assert decision.delivery_risk == risk
    decision = decide_failure("ASR_EXAMPLE_WRITE_FAILED", source=source, delivery_risk=risk)
    assert decision.recovery_disposition is RecoveryDisposition.STOP
    assert decision.cause_code == "ASR_EXAMPLE_WRITE_FAILED"
    assert decision.notification_code == (risk or decision.cause_code)
    unknown = decide_failure("ASR_EXAMPLE_UNKNOWN", source=source, delivery_risk=risk)
    assert unknown.recovery_disposition is RecoveryDisposition.STOP
    assert unknown.notification_code == unknown.cause_code
