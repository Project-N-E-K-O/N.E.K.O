from __future__ import annotations

import numpy as np


IMAGENET_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
IMAGENET_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)


def softmax(values: np.ndarray) -> np.ndarray:
    shifted = values.astype(np.float32, copy=False) - np.max(values)
    exp = np.exp(shifted)
    total = float(np.sum(exp))
    if total <= 0.0:
        return np.zeros_like(exp)
    return exp / total
