from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from plugin.plugins.galgame_plugin.core.vision.labels import GALGAME_VISION_LABELS

SUPPORTED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
GALGAME_SCREEN_LABELS: tuple[str, ...] = GALGAME_VISION_LABELS


def _label_from_filename(stem: str, labels: tuple[str, ...] = GALGAME_SCREEN_LABELS) -> str:
    tokens = [token for token in re.split(r"[^a-z0-9]+", stem.lower()) if token]
    for label in sorted(labels, key=lambda item: len(item.split("_")), reverse=True):
        label_tokens = label.split("_")
        token_count = len(label_tokens)
        for index in range(0, len(tokens) - token_count + 1):
            if tokens[index : index + token_count] == label_tokens:
                return label
    return ""


def collect_from_filenames(
    screenshot_dir: str | Path,
    output: str | Path,
) -> int:
    screenshot_dir = Path(screenshot_dir)
    if not screenshot_dir.exists() or not screenshot_dir.is_dir():
        raise FileNotFoundError(f"invalid screenshot_dir: {screenshot_dir}")
    image_paths = [
        path for path in sorted(screenshot_dir.glob("*")) if path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES
    ]
    if not image_paths:
        raise FileNotFoundError(f"no supported screenshots found in {screenshot_dir}")
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for path in image_paths:
            label = _label_from_filename(path.stem)
            if not label:
                continue
            handle.write(
                json.dumps(
                    {
                        "image_path": str(path),
                        "label": label,
                        "source": "filename",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Build weak labels from collected screenshots")
    parser.add_argument("--screenshot-dir", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    collect_from_filenames(args.screenshot_dir, args.output)


if __name__ == "__main__":
    main()
