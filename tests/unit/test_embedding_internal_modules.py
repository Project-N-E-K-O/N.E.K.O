"""Contract tests for the internal ``memory._embeddings`` split."""
from __future__ import annotations

from memory import embeddings
from memory._embeddings import hardware, lifecycle, profiles, schema


def test_public_schema_facade_keeps_callable_identity():
    assert embeddings.build_model_id is schema.build_model_id
    assert embeddings.clear_embedding_fields is schema.clear_embedding_fields


def test_schema_facade_keeps_dynamic_private_helper_monkeypatch(monkeypatch):
    monkeypatch.setattr(embeddings, "_decode_vector_fp16", lambda _value: "sentinel")
    assert embeddings.decode_embedding("encoded") == "sentinel"


def test_hardware_capability_snapshot_uses_injected_probes():
    capabilities = hardware.detect_capabilities(
        lambda: 12.0,
        lambda: (False, True),
        lambda: (True, True),
    )
    assert capabilities == hardware.HardwareCapabilities(
        ram_gb=12.0,
        has_vnni=False,
        vnni_absence_confirmed=True,
        has_avx2=True,
        avx2_absence_confirmed=True,
    )


def test_service_default_detection_still_uses_facade_monkeypatches(monkeypatch):
    monkeypatch.setattr(embeddings, "detect_total_ram_gb", lambda: 12.0)
    monkeypatch.setattr(
        embeddings, "detect_avx_vnni_details", lambda: (False, True),
    )
    monkeypatch.setattr(embeddings, "detect_avx2_details", lambda: (True, True))
    monkeypatch.setattr(embeddings, "_cpu_is_blocklisted", lambda: False)

    service = embeddings.EmbeddingService(model_dir="/nonexistent")

    assert service.dim() == 128
    assert service.is_disabled() is False


def test_profile_facade_preserves_file_check_monkeypatch(tmp_path, monkeypatch):
    profile_dir = tmp_path / "p" / "onnx"
    profile_dir.mkdir(parents=True)
    checked: list[str] = []

    def fake_check(path: str) -> bool:
        checked.append(path)
        return True

    monkeypatch.setattr(embeddings, "_is_nonempty_file", fake_check)
    assert embeddings._profile_is_complete(str(tmp_path), "p", "int8")
    assert len(checked) == 3


def test_profile_selection_prefers_complete_bundle(tmp_path):
    app_dir = tmp_path / "app"
    bundled_dir = tmp_path / "bundle"

    def complete(root: str, _profile: str, _quantization: str | None) -> bool:
        return root == str(bundled_dir)

    assert profiles.select_model_dir(
        str(app_dir),
        "p",
        "int8",
        [str(bundled_dir)],
        completeness_check=complete,
    ) == str(bundled_dir)


def test_lifecycle_get_or_create_is_lazy_and_stable():
    calls = 0

    def factory():
        nonlocal calls
        calls += 1
        return object()

    first = lifecycle.get_or_create(None, factory)
    second = lifecycle.get_or_create(first, factory)
    assert second is first
    assert calls == 1
    assert lifecycle.reset_for_tests() is None
