from __future__ import annotations

from typing import Callable

import numpy as np
import torch


_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)


def build_train_transform(size: tuple[int, int] = (224, 224)) -> Callable[[object], torch.Tensor]:
    try:
        import albumentations as A
        from albumentations.pytorch import ToTensorV2

        pipeline = A.Compose(
            [
                A.RandomResizedCrop(size[1], size[0], scale=(0.8, 1.0)),
                A.HorizontalFlip(p=0.3),
                A.ColorJitter(brightness=0.2, contrast=0.2, saturation=0.1, hue=0.05),
                A.GaussianBlur(blur_limit=(3, 7), sigma_limit=(0.1, 2.0), p=0.2),
                A.GaussNoise(var_limit=(0.0, 5.0), p=0.2),
                A.Normalize(mean=_MEAN.tolist(), std=_STD.tolist()),
                ToTensorV2(),
            ]
        )

        def _transform(image: object) -> torch.Tensor:
            return pipeline(image=np.asarray(image))["image"]

        return _transform
    except Exception:
        return build_eval_transform(size)


def build_eval_transform(size: tuple[int, int] = (224, 224)) -> Callable[[object], torch.Tensor]:
    def _transform(image: object) -> torch.Tensor:
        if hasattr(image, "resize") and hasattr(image, "convert"):
            image = image.convert("RGB").resize(size)
        array = np.asarray(image, dtype=np.float32) / 255.0
        array = (array - _MEAN) / _STD
        array = np.transpose(array, (2, 0, 1))
        return torch.from_numpy(array.astype(np.float32, copy=False))

    return _transform
