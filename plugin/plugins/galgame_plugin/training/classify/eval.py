from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from plugin.plugins.galgame_plugin.training.shared.metrics import macro_f1, top1_accuracy


def write_eval_report(logits: np.ndarray, labels: np.ndarray, output_path: str | Path) -> dict[str, float]:
    report = {
        "top1_accuracy": top1_accuracy(logits, labels),
        "macro_f1": macro_f1(logits, labels, num_classes=int(logits.shape[1] if logits.ndim == 2 else 0)),
    }
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report
