"""Hardware capability snapshots and pure runtime-selection policies."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class HardwareCapabilities:
    """The hardware facts consumed while selecting an embedding profile."""

    ram_gb: float | None
    has_vnni: bool
    vnni_absence_confirmed: bool
    has_avx2: bool
    avx2_absence_confirmed: bool


def detect_capabilities(
    detect_ram: Callable[[], float | None],
    detect_vnni: Callable[[], tuple[bool, bool]],
    detect_avx2: Callable[[], tuple[bool, bool]],
) -> HardwareCapabilities:
    """Collect capability probes behind an injectable compatibility bridge."""
    ram_gb = detect_ram()
    has_vnni, vnni_confirmed = detect_vnni()
    has_avx2, avx2_confirmed = detect_avx2()
    return HardwareCapabilities(
        ram_gb=ram_gb,
        has_vnni=has_vnni,
        vnni_absence_confirmed=vnni_confirmed,
        has_avx2=has_avx2,
        avx2_absence_confirmed=avx2_confirmed,
    )


def resolve_dim_for_ram(ram_gb: float | None, min_ram_gb: float) -> int | None:
    """Choose the default Matryoshka dimension for installed RAM."""
    if ram_gb is None or ram_gb < min_ram_gb:
        return None
    if ram_gb < 8:
        return 64
    if ram_gb < 16:
        return 128
    return 256


def auto_int8_or_none(
    has_vnni: bool,
    vnni_absence_confirmed: bool,
    has_avx2: bool,
    avx2_absence_confirmed: bool,
) -> str | None:
    """Select int8 unless both supported SIMD paths are confirmed absent."""
    if has_vnni or not vnni_absence_confirmed:
        return "int8"
    if has_avx2 or not avx2_absence_confirmed:
        return "int8"
    return None


def resolve_quantization(
    value: str | None,
    has_vnni: bool,
    *,
    vnni_absence_confirmed: bool,
    has_avx2: bool,
    avx2_absence_confirmed: bool,
) -> str | None:
    """Resolve the configured quantization without performing any I/O."""
    if value == "fp32":
        return "fp32"
    if value == "auto" or value is None or value not in ("int8", "fp32"):
        return auto_int8_or_none(
            has_vnni,
            vnni_absence_confirmed,
            has_avx2,
            avx2_absence_confirmed,
        )
    return "int8"
