from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from PIL import Image
import torch
from torch.utils.data import Dataset

from plugin.plugins.galgame_plugin.core.vision.labels import GALGAME_VISION_LABELS
from plugin.plugins.galgame_plugin.training.shared.augment import build_eval_transform, build_train_transform


_LOGGER = logging.getLogger(__name__)
GALGAME_SCREEN_LABELS: tuple[str, ...] = GALGAME_VISION_LABELS


class GameScreenDataset(Dataset):
    def __init__(
        self,
        data_dir: str | Path,
        num_classes: int,
        *,
        split: str = "train",
        augment: bool = False,
        labels: tuple[str, ...] = GALGAME_SCREEN_LABELS,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.num_classes = int(num_classes)
        self.labels = tuple(labels[: self.num_classes])
        self.label_to_idx = {label: index for index, label in enumerate(self.labels)}
        self.samples = self._load_samples(split)
        self.transform = build_train_transform() if augment else build_eval_transform()

    def _load_samples(self, split: str) -> list[dict[str, Any]]:
        path = self.data_dir / f"{split}.jsonl"
        samples: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                sample = json.loads(line)
                label = str(sample.get("label") or "").strip()
                if label not in self.label_to_idx:
                    raise ValueError(f"{path}:{line_no}: unknown label {label!r}")
                image_path = Path(str(sample.get("image_path") or ""))
                sample["image_path"] = str(
                    image_path if image_path.is_absolute() else self.data_dir / image_path
                )
                samples.append(sample)
        return samples

    def __len__(self) -> int:
        return len(self.samples)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, int]:
        sample = self.samples[idx]
        try:
            with Image.open(sample["image_path"]) as image_file:
                image = image_file.convert("RGB")
        except Exception as exc:
            _LOGGER.warning("failed to load training image %s: %s", sample["image_path"], exc)
            raise
        tensor = self.transform(image)
        return tensor, self.label_to_idx[str(sample["label"])]
