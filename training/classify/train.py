from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

import torch
from torch import nn
from torch.optim import AdamW
from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader

from training.classify.export_onnx import export_onnx
from training.classify.model import GameScreenCNN
from training.data.dataset import GALGAME_SCREEN_LABELS, GameScreenDataset


_LOGGER = logging.getLogger(__name__)
DEFAULT_OUTPUT_DIR = "plugin/plugins/galgame_plugin/models/vision/screen_classifier"


def _load_imagenet_pretrained_backbone():
    try:
        from torchvision.models import MobileNet_V3_Small_Weights, mobilenet_v3_small

        full_model = mobilenet_v3_small(weights=MobileNet_V3_Small_Weights.IMAGENET1K_V1)
        return full_model.features.state_dict()
    except Exception as exc:
        _LOGGER.warning(
            "failed to load ImageNet pretrained backbone; training from scratch: %s",
            exc,
        )
        return None


def _set_backbone_trainable(model: GameScreenCNN, trainable: bool) -> None:
    for parameter in model.features.parameters():
        parameter.requires_grad = trainable


def _load_compatible_feature_weights(model: GameScreenCNN, pretrained: dict[str, object]) -> int:
    current = model.features.state_dict()
    compatible = {
        key: value
        for key, value in pretrained.items()
        if key in current and getattr(current[key], "shape", None) == getattr(value, "shape", None)
    }
    if compatible:
        model.features.load_state_dict(compatible, strict=False)
    return len(compatible)


def train_epoch(model, loader, optimizer, criterion, device, epoch: int) -> float:
    del epoch
    model.train()
    total_loss = 0.0
    total = 0
    for images, labels in loader:
        images = images.to(device)
        labels = labels.to(device)
        optimizer.zero_grad(set_to_none=True)
        loss = criterion(model(images), labels)
        if not torch.isfinite(loss).item():
            raise ValueError(f"non-finite training loss at epoch {epoch}: {float(loss.item())}")
        loss.backward()
        optimizer.step()
        batch_size = int(labels.shape[0])
        total_loss += float(loss.item()) * batch_size
        total += batch_size
    return total_loss / max(1, total)


@torch.no_grad()
def validate(model, loader, device) -> float:
    model.eval()
    correct = 0
    total = 0
    for images, labels in loader:
        images = images.to(device)
        labels = labels.to(device)
        preds = torch.argmax(model(images), dim=1)
        correct += int((preds == labels).sum().item())
        total += int(labels.shape[0])
    return correct / max(1, total)


def train(args) -> None:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    train_ds = GameScreenDataset(data_dir, args.num_classes, split="train", augment=True)
    val_ds = GameScreenDataset(data_dir, args.num_classes, split="val", augment=False)
    train_loader = DataLoader(
        train_ds,
        batch_size=args.batch_size,
        shuffle=True,
        num_workers=args.num_workers,
    )
    val_loader = DataLoader(
        val_ds,
        batch_size=args.batch_size,
        shuffle=False,
        num_workers=max(0, args.num_workers // 2),
    )

    model = GameScreenCNN(num_classes=args.num_classes).to(device)
    pretrained = _load_imagenet_pretrained_backbone()
    if pretrained is not None:
        _load_compatible_feature_weights(model, pretrained)

    criterion = nn.CrossEntropyLoss(label_smoothing=args.label_smoothing)
    _set_backbone_trainable(model, False)
    optimizer = AdamW(
        model.classifier.parameters(),
        lr=args.learning_rate_head,
        weight_decay=args.weight_decay,
    )
    for epoch in range(args.freeze_backbone_epochs):
        train_epoch(model, train_loader, optimizer, criterion, device, epoch)
    validate(model, val_loader, device)

    _set_backbone_trainable(model, True)
    optimizer = AdamW(
        model.parameters(),
        lr=args.learning_rate_full,
        weight_decay=args.weight_decay,
    )
    remaining_epochs = max(0, args.epochs - args.freeze_backbone_epochs)
    scheduler = CosineAnnealingLR(optimizer, T_max=max(1, remaining_epochs))
    best_acc = -1.0
    best_path = output_dir / "best.pth"
    for epoch in range(remaining_epochs):
        train_epoch(
            model,
            train_loader,
            optimizer,
            criterion,
            device,
            epoch + args.freeze_backbone_epochs,
        )
        val_acc = validate(model, val_loader, device)
        scheduler.step()
        if val_acc >= best_acc:
            best_acc = val_acc
            torch.save(model.state_dict(), best_path)

    if best_path.exists():
        model.load_state_dict(torch.load(best_path, map_location=device))
    elif best_acc < 0.0:
        best_acc = validate(model, val_loader, device)

    export_onnx(
        model,
        output_dir / "v1_galgame.onnx",
        num_classes=args.num_classes,
        device=device,
    )
    _write_model_config(
        output_dir / "v1_config.json",
        num_classes=args.num_classes,
        input_size=(224, 224),
        best_val_accuracy=best_acc,
    )


def _write_model_config(
    output_path: Path,
    *,
    num_classes: int,
    input_size: tuple[int, int],
    best_val_accuracy: float,
) -> None:
    payload = {
        "model_name": "v1_galgame",
        "labels": list(GALGAME_SCREEN_LABELS[:num_classes]),
        "input_size": list(input_size),
        "threshold": 0.75,
        "best_val_accuracy": round(max(0.0, float(best_val_accuracy)), 6),
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train the galgame screen classifier CNN")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--num-classes", type=int, default=11)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--freeze-backbone-epochs", type=int, default=5)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--num-workers", type=int, default=4)
    parser.add_argument("--learning-rate-head", type=float, default=1e-3)
    parser.add_argument("--learning-rate-full", type=float, default=1e-4)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--label-smoothing", type=float, default=0.1)
    return parser


def main() -> None:
    train(build_parser().parse_args())


if __name__ == "__main__":
    main()
