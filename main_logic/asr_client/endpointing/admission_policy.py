"""Immutable, explicitly opted-in admission experiment for one ASR session."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os

from main_logic.voice_turn.admission import AdmissionConfig


@dataclass(frozen=True, slots=True)
class AdmissionPolicySnapshot:
    enabled: bool
    mode: str
    config: AdmissionConfig | None = None
    shadow_config: AdmissionConfig | None = None


def resolve_admission_policy(
    environment: Mapping[str, str] | None = None,
) -> AdmissionPolicySnapshot:
    """Resolve before transport awaits; recovery reuses the same detector policy.

    The trial is deliberately not a calibrated production default. It is not
    enabled by the resource-optimization setting or by merely enabling admission.
    """
    values = os.environ if environment is None else environment
    enabled = values.get("NEKO_ASR_ADMISSION", "0") == "1"
    mode = values.get("NEKO_ASR_SHORT_SPEECH", "off").strip().lower()
    if mode not in {"off", "shadow", "enforce"}:
        raise ValueError("invalid short speech admission mode")
    if mode != "off" and not enabled:
        raise ValueError("short speech experiment requires ASR admission")
    if mode == "off":
        return AdmissionPolicySnapshot(enabled, mode)
    trial = AdmissionConfig(experimental_short_speech=True)
    return AdmissionPolicySnapshot(
        enabled,
        mode,
        config=trial if mode == "enforce" else None,
        shadow_config=trial if mode == "shadow" else None,
    )
