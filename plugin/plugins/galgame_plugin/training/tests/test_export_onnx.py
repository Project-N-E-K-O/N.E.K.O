from __future__ import annotations

import pytest


def test_export_onnx_uses_legacy_exporter_for_windows_console(monkeypatch, tmp_path) -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.export_onnx import export_onnx

    class _FixedModel(torch.nn.Module):
        def forward(self, images):
            return images.new_zeros((images.shape[0], 11))

    captured: dict[str, object] = {}

    def fake_export(*args, **kwargs) -> None:
        del args
        captured.update(kwargs)

    monkeypatch.setattr(torch.onnx, "export", fake_export)

    export_onnx(
        _FixedModel(),
        tmp_path / "model.onnx",
        num_classes=11,
        device=torch.device("cpu"),
    )

    assert captured["dynamo"] is False
    assert captured["opset_version"] == 17


def test_export_onnx_rejects_num_class_mismatch(monkeypatch, tmp_path) -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.export_onnx import export_onnx

    class _WrongClassCountModel(torch.nn.Module):
        def forward(self, images):
            return images.new_zeros((images.shape[0], 2))

    monkeypatch.setattr(
        torch.onnx,
        "export",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("export should not run")),
    )

    with pytest.raises(ValueError, match="num_classes=3"):
        export_onnx(
            _WrongClassCountModel(),
            tmp_path / "model.onnx",
            num_classes=3,
            device=torch.device("cpu"),
        )
