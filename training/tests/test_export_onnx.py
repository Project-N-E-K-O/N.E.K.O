from __future__ import annotations

import torch

from training.classify.export_onnx import export_onnx


def test_export_onnx_uses_legacy_exporter_for_windows_console(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}

    def fake_export(*args, **kwargs) -> None:
        del args
        captured.update(kwargs)

    monkeypatch.setattr(torch.onnx, "export", fake_export)

    export_onnx(
        torch.nn.Identity(),
        tmp_path / "model.onnx",
        num_classes=11,
        device=torch.device("cpu"),
    )

    assert captured["dynamo"] is False
    assert captured["opset_version"] == 17
