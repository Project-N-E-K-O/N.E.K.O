from __future__ import annotations

import json
from pathlib import Path


def convert_label_studio_export(input_path: str | Path, output_path: str | Path) -> int:
    records = json.loads(Path(input_path).read_text(encoding="utf-8"))
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output.open("w", encoding="utf-8") as handle:
        for record in records:
            data = record.get("data") if isinstance(record, dict) else {}
            annotations = record.get("annotations") if isinstance(record, dict) else []
            image_path = str((data or {}).get("image") or "").strip()
            if not image_path or not annotations:
                continue
            label = _first_label(annotations)
            if not label:
                continue
            handle.write(json.dumps({"image_path": image_path, "label": label, "source": "manual"}) + "\n")
            count += 1
    return count


def _first_label(annotations: object) -> str:
    if not isinstance(annotations, list):
        return ""
    for annotation in annotations:
        results = annotation.get("result") if isinstance(annotation, dict) else []
        if not isinstance(results, list):
            continue
        for result in results:
            value = result.get("value") if isinstance(result, dict) else {}
            labels = value.get("choices") or value.get("labels") if isinstance(value, dict) else []
            if isinstance(labels, list) and labels:
                return str(labels[0])
    return ""
