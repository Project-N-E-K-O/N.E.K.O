from __future__ import annotations

import json
import shutil
from pathlib import Path

from ..core.config import OUTPUT_DIR
from ..core.session_id import validate_session_id
from ..core.types import ProcessResult


class SessionStore:
    def __init__(self, root: str | Path = OUTPUT_DIR):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def list_sessions(self) -> list[dict[str, object]]:
        sessions: list[dict[str, object]] = []
        for session_dir in sorted(self.root.iterdir(), key=lambda path: path.name, reverse=True):
            if not session_dir.is_dir():
                continue
            manifest = self.load(session_dir.name)
            if manifest is None:
                continue
            data = manifest.to_dict()
            data["exists"] = self.artifacts_exist(manifest)
            sessions.append(data)
        return sessions

    def save(self, result: ProcessResult) -> Path:
        manifest_path = Path(result.manifest_path)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(
            json.dumps(result.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return manifest_path

    def load(self, session_id: str) -> ProcessResult | None:
        manifest_path = self._session_dir(session_id) / "manifest.json"
        if not manifest_path.is_file():
            return None
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(data, dict):
            return None
        return ProcessResult.from_dict(data)

    def delete(self, session_id: str) -> bool:
        session_dir = self._session_dir(session_id)
        if not session_dir.is_dir():
            return False
        shutil.rmtree(session_dir)
        return True

    def artifacts_exist(self, result: ProcessResult) -> dict[str, bool]:
        return {
            "manifest": Path(result.manifest_path).is_file(),
            "preview": Path(result.preview_path).is_file(),
            "zip": Path(result.zip_path).is_file(),
            "layers": all(Path(layer.path).is_file() for layer in result.layers),
        }

    def _session_dir(self, session_id: str) -> Path:
        return self.root / validate_session_id(session_id)
