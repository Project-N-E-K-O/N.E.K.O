"""Workflow registry for PNGTuber Auto Compose.

The registry keeps ComfyUI workflow bindings declarative so the plugin can
grow by adding workflow specs instead of hard-coding every pipeline step into
the UI or routers.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class WorkflowRegistry:
    def __init__(self, root: Path):
        self.root = root

    def list(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json")):
            spec = self._read(path)
            if spec is None:
                continue
            items.append(self._summary(spec))
        return items

    def get(self, workflow_id: str) -> dict[str, Any] | None:
        normalized = self._normalize_id(workflow_id)
        if not normalized:
            return None
        path = self.root / f"{normalized}.json"
        return self._read(path)

    def _read(self, path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            with path.open("r", encoding="utf-8") as stream:
                data = json.load(stream)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None
        if not isinstance(data, dict):
            return None
        workflow_id = self._normalize_id(str(data.get("id", path.stem)))
        if not workflow_id:
            return None
        data["id"] = workflow_id
        return data

    def _summary(self, spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": spec.get("id", ""),
            "name": spec.get("name", spec.get("id", "")),
            "stage": spec.get("stage", ""),
            "status": spec.get("status", "planned"),
            "description": spec.get("description", ""),
            "tags": spec.get("tags", []),
            "inputs": spec.get("inputs", []),
            "outputs": spec.get("outputs", []),
            "depends_on": spec.get("depends_on", []),
            "next": spec.get("next", []),
        }

    def _normalize_id(self, value: str) -> str:
        normalized = (value or "").strip().lower().replace("-", "_")
        allowed = []
        for char in normalized:
            if char.isalnum() or char == "_":
                allowed.append(char)
        return "".join(allowed).strip("_")
