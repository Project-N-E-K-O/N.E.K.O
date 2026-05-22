from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import torch

from plugin.plugins.galgame_plugin.core.vision.preprocessing import IMAGENET_MEAN, IMAGENET_STD


_LOGGER = logging.getLogger(__name__)


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
                A.Normalize(mean=IMAGENET_MEAN.tolist(), std=IMAGENET_STD.tolist()),
                ToTensorV2(),
            ]
        )

        def _transform(image: object) -> torch.Tensor:
            return pipeline(image=np.asarray(image))["image"]

        return _transform
    except Exception as exc:
        _LOGGER.warning(
            "data augmentation disabled; falling back to eval transform: %s",
            exc,
        )
        return build_eval_transform(size)


def build_eval_transform(size: tuple[int, int] = (224, 224)) -> Callable[[object], torch.Tensor]:
    def _transform(image: object) -> torch.Tensor:
        if hasattr(image, "resize") and hasattr(image, "convert"):
            image = image.convert("RGB").resize(size)
        array = np.asarray(image, dtype=np.float32)
        if array.ndim == 2:
            array = np.stack([array, array, array], axis=-1)
        elif array.ndim == 3 and array.shape[2] == 4:
            array = array[:, :, :3]
        if array.ndim != 3 or array.shape[2] != 3:
            raise ValueError(f"expected image shape HxWx3, got {array.shape!r}")
        array = array / 255.0
        array = (array - IMAGENET_MEAN) / IMAGENET_STD
        array = np.transpose(array, (2, 0, 1))
        return torch.from_numpy(array.astype(np.float32, copy=False))

    return _transform
