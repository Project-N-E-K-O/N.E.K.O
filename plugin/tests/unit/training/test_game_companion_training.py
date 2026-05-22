from __future__ import annotations

import builtins
import json

import numpy as np
from PIL import Image
import pytest

from plugin.plugins.galgame_plugin.training.shared.metrics import macro_f1, top1_accuracy


def test_game_screen_cnn_forward_shape() -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.model import GameScreenCNN

    model = GameScreenCNN(num_classes=11)
    output = model(torch.randn(2, 3, 224, 224))

    assert tuple(output.shape) == (2, 11)


def test_game_screen_dataset_loads_jsonl_and_returns_tensor(tmp_path) -> None:
    pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.data.dataset import GameScreenDataset

    image_path = tmp_path / "dialogue.png"
    Image.new("RGB", (32, 32), "black").save(image_path)
    (tmp_path / "train.jsonl").write_text(
        json.dumps({"image_path": "dialogue.png", "label": "dialogue"}) + "\n",
        encoding="utf-8",
    )

    dataset = GameScreenDataset(tmp_path, 11, split="train", augment=False)
    image, label = dataset[0]

    assert tuple(image.shape) == (3, 224, 224)
    assert label == 0


def test_game_screen_dataset_warns_when_image_cannot_be_loaded(tmp_path, caplog) -> None:
    pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.data.dataset import GameScreenDataset

    image_path = tmp_path / "broken.png"
    image_path.write_bytes(b"not an image")
    (tmp_path / "train.jsonl").write_text(
        json.dumps({"image_path": "broken.png", "label": "dialogue"}) + "\n",
        encoding="utf-8",
    )
    dataset = GameScreenDataset(tmp_path, 11, split="train", augment=False)

    with caplog.at_level("WARNING"), pytest.raises(Exception):
        dataset[0]

    assert "failed to load training image" in caplog.text
    assert "broken.png" in caplog.text


def test_pretrained_feature_loader_ignores_incompatible_shapes() -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.model import GameScreenCNN
    from plugin.plugins.galgame_plugin.training.classify.train import _load_compatible_feature_weights

    model = GameScreenCNN(num_classes=11)
    current = model.features.state_dict()
    key = next(iter(current))
    compatible = current[key].detach().clone() + 1
    incompatible = torch.randn(1)

    loaded_count = _load_compatible_feature_weights(
        model,
        {key: compatible, "missing.weight": incompatible},
    )

    assert loaded_count == 1
    assert torch.equal(model.features.state_dict()[key], compatible)


def test_pretrained_backbone_warning_when_torchvision_load_fails(monkeypatch, caplog) -> None:
    pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify import train as train_module

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "torchvision.models":
            raise RuntimeError("torchvision cache is corrupted")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("WARNING"):
        assert train_module._load_imagenet_pretrained_backbone() is None

    assert "torchvision cache is corrupted" in caplog.text
    assert "training from scratch" in caplog.text


def test_train_transform_warns_when_albumentations_fails(monkeypatch, caplog) -> None:
    pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.shared import augment

    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"albumentations", "albumentations.pytorch"}:
            raise RuntimeError("augmentation dependency broken")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with caplog.at_level("WARNING"):
        transform = augment.build_train_transform()

    assert transform is not None
    assert "augmentation dependency broken" in caplog.text
    assert "data augmentation disabled" in caplog.text


def test_train_epoch_rejects_non_finite_loss() -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.train import train_epoch

    class _BadModel(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.weight = torch.nn.Parameter(torch.ones(()))

        def forward(self, images):
            return torch.full((images.shape[0], 2), float("nan"), device=images.device) * self.weight

    model = _BadModel()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.1)
    criterion = torch.nn.CrossEntropyLoss()
    loader = [(torch.zeros((2, 3, 4, 4)), torch.tensor([0, 1]))]

    with pytest.raises(ValueError, match="non-finite training loss"):
        train_epoch(model, loader, optimizer, criterion, torch.device("cpu"), epoch=0)


def test_export_onnx_uses_legacy_exporter_for_windows_console(monkeypatch, tmp_path) -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.export_onnx import export_onnx

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


def test_training_metrics() -> None:
    logits = np.asarray([[4.0, 1.0], [0.5, 2.0], [3.0, 1.0]], dtype=np.float32)
    labels = np.asarray([0, 1, 1], dtype=np.int64)

    assert top1_accuracy(logits, labels) == pytest.approx(2 / 3)
    assert macro_f1(logits, labels, num_classes=2) == pytest.approx((2 / 3 + 2 / 3) / 2)
