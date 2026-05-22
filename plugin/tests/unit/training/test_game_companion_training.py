from __future__ import annotations

import builtins
import json
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from plugin.plugins.galgame_plugin.training.shared.metrics import macro_f1, top1_accuracy


def test_game_screen_cnn_forward_shape() -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.model import FEATURE_DIM, GameScreenCNN

    model = GameScreenCNN(num_classes=11)
    output = model(torch.randn(2, 3, 224, 224))

    assert tuple(output.shape) == (2, 11)
    assert model.classifier[0].in_features == FEATURE_DIM


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

    with caplog.at_level("WARNING"), pytest.raises(OSError):
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

    with pytest.raises(ValueError, match="epoch 0"):
        train_epoch(model, loader, optimizer, criterion, torch.device("cpu"), epoch=0)


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


def test_eval_report_creates_parent_directory(tmp_path) -> None:
    from plugin.plugins.galgame_plugin.training.classify.eval import write_eval_report

    output_path = tmp_path / "nested" / "report.json"

    report = write_eval_report(
        np.asarray([[4.0, 1.0], [0.5, 2.0]], dtype=np.float32),
        np.asarray([0, 1], dtype=np.int64),
        output_path,
    )

    assert output_path.exists()
    assert report["top1_accuracy"] == 1.0


def test_eval_transform_accepts_grayscale_and_rgba_arrays() -> None:
    torch = pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.shared.augment import build_eval_transform

    transform = build_eval_transform()

    gray = transform(np.zeros((16, 16), dtype=np.uint8))
    rgba = transform(np.zeros((16, 16, 4), dtype=np.uint8))

    assert isinstance(gray, torch.Tensor)
    assert tuple(gray.shape) == (3, 16, 16)
    assert tuple(rgba.shape) == (3, 16, 16)


def test_collect_screenshots_validates_input_dir_before_writing(tmp_path) -> None:
    from plugin.plugins.galgame_plugin.training.data.collect_screenshots import collect_from_filenames

    output_path = tmp_path / "labels" / "train.jsonl"

    with pytest.raises(FileNotFoundError, match="invalid screenshot_dir"):
        collect_from_filenames(tmp_path / "missing", output_path)
    assert not output_path.exists()

    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    with pytest.raises(FileNotFoundError, match="no supported screenshots"):
        collect_from_filenames(empty_dir, output_path)
    assert not output_path.exists()


def test_collect_screenshots_uses_token_exact_label_matching(tmp_path) -> None:
    from plugin.plugins.galgame_plugin.training.data.collect_screenshots import collect_from_filenames

    (tmp_path / "dialogue_001.png").write_bytes(b"fake")
    (tmp_path / "choice-menu-round.jpg").write_bytes(b"fake")
    (tmp_path / "notdialogue.webp").write_bytes(b"fake")
    output_path = tmp_path / "labels.jsonl"

    count = collect_from_filenames(tmp_path, output_path)
    rows = [
        json.loads(line)
        for line in output_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert count == 2
    assert [row["label"] for row in rows] == ["choice_menu", "dialogue"]


def test_train_validates_num_classes_range() -> None:
    pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify.train import _validate_num_classes
    from plugin.plugins.galgame_plugin.training.data.dataset import GALGAME_SCREEN_LABELS

    assert _validate_num_classes(1) == 1
    with pytest.raises(ValueError, match=f"\\[1, {len(GALGAME_SCREEN_LABELS)}\\]"):
        _validate_num_classes(len(GALGAME_SCREEN_LABELS) + 1)


def test_train_caps_freeze_epochs_to_total(monkeypatch, tmp_path) -> None:
    pytest.importorskip("torch")
    from plugin.plugins.galgame_plugin.training.classify import train as train_module

    (tmp_path / "train.jsonl").write_text(
        json.dumps({"image_path": "unused.png", "label": "dialogue"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "val.jsonl").write_text(
        json.dumps({"image_path": "unused.png", "label": "dialogue"}) + "\n",
        encoding="utf-8",
    )
    trained_epochs: list[int] = []
    trainable_calls: list[bool] = []

    monkeypatch.setattr(train_module, "_load_imagenet_pretrained_backbone", lambda: None)
    monkeypatch.setattr(
        train_module,
        "_set_backbone_trainable",
        lambda _model, trainable: trainable_calls.append(trainable),
    )
    monkeypatch.setattr(
        train_module,
        "train_epoch",
        lambda _model, _loader, _optimizer, _criterion, _device, epoch: trained_epochs.append(epoch),
    )
    monkeypatch.setattr(train_module, "validate", lambda _model, _loader, _device: 0.75)
    monkeypatch.setattr(train_module, "export_onnx", lambda *_args, **_kwargs: None)

    train_module.train(
        SimpleNamespace(
            data_dir=tmp_path,
            output_dir=tmp_path / "model",
            num_classes=11,
            epochs=1,
            freeze_backbone_epochs=5,
            batch_size=1,
            num_workers=0,
            learning_rate_head=1e-3,
            learning_rate_full=1e-4,
            weight_decay=0.01,
            label_smoothing=0.0,
        )
    )

    assert trained_epochs == [0]
    assert trainable_calls == [False]


def test_training_metrics() -> None:
    logits = np.asarray([[4.0, 1.0], [0.5, 2.0], [3.0, 1.0]], dtype=np.float32)
    labels = np.asarray([0, 1, 1], dtype=np.int64)

    assert top1_accuracy(logits, labels) == pytest.approx(2 / 3)
    assert macro_f1(logits, labels, num_classes=2) == pytest.approx((2 / 3 + 2 / 3) / 2)
