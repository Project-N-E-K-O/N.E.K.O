# -*- coding: utf-8 -*-
"""Unit tests for the known-bad-CPU brand-string blocklist in
``memory.embeddings``.

Context: the E5-2666 v3 (AWS-custom Haswell-EP) looks fine through every
numeric signal we trust (family/model, numpy AVX2/VNNI flags), so ``auto``
picks int8 and loads onnxruntime as usual — but MLAS then SIGILLs at
runtime. family/model can not single out this one part (06_3FH is shared by
the whole Haswell-EP cohort, most of which run fine), so the only way to
blocklist it is a brand-string substring match. On a hit the service is
disabled before the session loads, and callers fall back to the pre-vector
path. ``XIAO8_VECTORS_FORCE_ENABLE=1`` is a runtime ``os.getenv`` escape
hatch (still effective after Nuitka compilation) for a false positive.

The tests touch no heavy deps: brand reads and the construction path are
monkeypatched / injected, so they run without onnxruntime/tokenizers/numpy.
"""
from __future__ import annotations

import pytest

# 顶层 import 是纯 Python 加载(重依赖都在函数内 lazy import),importorskip
# 兜底罕见的打包剥离场景。
embeddings = pytest.importorskip("memory.embeddings")

_E5_2666 = "Intel(R) Xeon(R) CPU E5-2666 v3 @ 2.90GHz"
_E5_1680 = "Intel(R) Xeon(R) CPU E5-1680 v4 @ 3.40GHz"


def test_blocklist_matches_brand_substring_case_insensitive(monkeypatch):
    """A brand string containing ``E5-2666 v3`` (case-insensitive, as a
    substring) trips the blocklist."""
    monkeypatch.delenv("XIAO8_VECTORS_FORCE_ENABLE", raising=False)
    monkeypatch.setattr(embeddings, "_read_cpu_brand_string", lambda: _E5_2666)
    assert embeddings._cpu_is_blocklisted() is True


def test_blocklist_passes_sibling_haswell_cohort_cpu(monkeypatch):
    """A sibling SKU (e.g. Broadwell E5-1680 v4) must not be caught: the
    list matches an exact brand substring, never family/model wholesale."""
    monkeypatch.delenv("XIAO8_VECTORS_FORCE_ENABLE", raising=False)
    monkeypatch.setattr(embeddings, "_read_cpu_brand_string", lambda: _E5_1680)
    assert embeddings._cpu_is_blocklisted() is False


def test_blocklist_missing_brand_string_does_not_disable(monkeypatch):
    """When the brand string can not be read (None), treat it as unknown
    and do not disable — same optimistic stance the module takes for
    inconclusive CPU detection."""
    monkeypatch.delenv("XIAO8_VECTORS_FORCE_ENABLE", raising=False)
    monkeypatch.setattr(embeddings, "_read_cpu_brand_string", lambda: None)
    assert embeddings._cpu_is_blocklisted() is False


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_force_enable_env_overrides_blocklist(monkeypatch, val):
    """A truthy ``XIAO8_VECTORS_FORCE_ENABLE`` forces the CPU through even
    when the brand matches (per-machine escape hatch, Nuitka-safe)."""
    monkeypatch.setattr(embeddings, "_read_cpu_brand_string", lambda: _E5_2666)
    monkeypatch.setenv("XIAO8_VECTORS_FORCE_ENABLE", val)
    assert embeddings._cpu_is_blocklisted() is False


def test_force_enable_env_falsey_keeps_blocklist(monkeypatch):
    """A non-truthy value (empty / ``0``) is not the switch; the blocklist
    still applies."""
    monkeypatch.setattr(embeddings, "_read_cpu_brand_string", lambda: _E5_2666)
    monkeypatch.setenv("XIAO8_VECTORS_FORCE_ENABLE", "0")
    assert embeddings._cpu_is_blocklisted() is True


def test_blocklisted_cpu_disables_service_at_construction(monkeypatch):
    """A blocklisted CPU drives the service straight to DISABLED at
    construction with reason ``cpu_on_known_bad_blocklist`` — ahead of the
    RAM / quantization checks and before any session load, so the SIGILL
    never happens."""
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: True)
    svc = embeddings.EmbeddingService(
        model_dir="/nonexistent",
        enabled=True,
        ram_gb=32.0,            # 足够高,排除 LOW_RAM 干扰
        has_vnni=False,         # 注入检测结果,构造时不跑真 CPUID
        vnni_absence_confirmed=True,
        has_avx2=True,          # 否则 auto 会因无 SIMD 路径 disable,混淆 reason
        avx2_absence_confirmed=True,
    )
    assert svc.is_disabled() is True
    assert svc.disable_reason() == embeddings._DisableReason.CPU_BLOCKLISTED.value
    assert svc.disable_reason() == "cpu_on_known_bad_blocklist"
    # A blocklisted machine must not stamp embedding rows.
    assert svc.model_id() is None


def test_unblocklisted_cpu_constructs_enabled(monkeypatch):
    """Dual case: with the same injected params but no blocklist hit, this
    gate does not disable the service (it reaches INIT, loading deferred to
    request_load) — proving the gate does not catch healthy CPUs."""
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)
    svc = embeddings.EmbeddingService(
        model_dir="/nonexistent",
        enabled=True,
        ram_gb=32.0,
        has_vnni=False,
        vnni_absence_confirmed=True,
        has_avx2=True,
        avx2_absence_confirmed=True,
    )
    assert svc.is_disabled() is False
    assert svc.disable_reason() == embeddings._DisableReason.NONE.value
