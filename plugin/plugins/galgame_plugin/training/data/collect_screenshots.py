from __future__ import annotations

import argparse
import json
from pathlib import Path

from plugin.plugins.galgame_plugin.training.data.dataset import GALGAME_SCREEN_LABELS


def collect_from_filenames(
    screenshot_dir: str | Path,
    output: str | Path,
) -> int:
    screenshot_dir = Path(screenshot_dir)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for path in sorted(screenshot_dir.glob("*")):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                continue
            lower_name = path.stem.lower()
            label = next((item for item in GALGAME_SCREEN_LABELS if item in lower_name), "")
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
