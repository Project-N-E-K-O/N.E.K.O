from __future__ import annotations

from pathlib import Path

import torch


def export_onnx(
    model: torch.nn.Module,
    output_path: str | Path,
    *,
    num_classes: int,
    device: torch.device,
    input_size: tuple[int, int] = (224, 224),
    opset: int = 17,
) -> None:
    num_classes = int(num_classes)
    if num_classes <= 0:
        raise ValueError(f"num_classes must be positive, got {num_classes}")
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.randn(1, 3, input_size[1], input_size[0], device=device)
    with torch.no_grad():
        logits = model(dummy)
    if (
        not isinstance(logits, torch.Tensor)
        or logits.ndim < 2
        or int(logits.shape[-1]) != num_classes
    ):
        actual = tuple(logits.shape) if isinstance(logits, torch.Tensor) else type(logits).__name__
        raise ValueError(
            f"model output classes must match num_classes={num_classes}, got {actual}"
        )
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=opset,
        dynamo=False,
    )
