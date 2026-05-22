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
    del num_classes
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    model.eval()
    dummy = torch.randn(1, 3, input_size[1], input_size[0], device=device)
    torch.onnx.export(
        model,
        dummy,
        str(output_path),
        input_names=["input"],
        output_names=["logits"],
        dynamic_axes={"input": {0: "batch"}, "logits": {0: "batch"}},
        opset_version=opset,
    )
