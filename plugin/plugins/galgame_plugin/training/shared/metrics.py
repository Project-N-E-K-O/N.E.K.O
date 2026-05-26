from __future__ import annotations

import numpy as np


def top1_accuracy(logits: np.ndarray, labels: np.ndarray) -> float:
    if labels.size == 0:
        return 0.0
    preds = np.argmax(logits, axis=1)
    return float(np.mean(preds == labels))


def macro_f1(logits: np.ndarray, labels: np.ndarray, *, num_classes: int) -> float:
    if labels.size == 0 or num_classes <= 0:
        return 0.0
    preds = np.argmax(logits, axis=1)
    scores: list[float] = []
    for class_id in range(num_classes):
        tp = int(np.sum((preds == class_id) & (labels == class_id)))
        fp = int(np.sum((preds == class_id) & (labels != class_id)))
        fn = int(np.sum((preds != class_id) & (labels == class_id)))
        denom = (2 * tp) + fp + fn
        scores.append(0.0 if denom == 0 else (2 * tp) / denom)
    return float(np.mean(scores))
