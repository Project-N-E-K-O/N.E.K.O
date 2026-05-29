"""Cosplay 角色库管理器。

负责角色的 CRUD、参考图管理。
存储结构：
  data/character_library/{character_id}/
    character.json       # 角色定义
    reference/           # 参考图片
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger
from .cosplay_types import CosplayCharacter, _now_iso

_logger = get_logger("cosplay.character_library")

INDEX_KEY = "cosplay_character_library_index"


class CharacterLibrary:
    """角色库管理。"""

    def __init__(self, data_root: Path, store: Any) -> None:
        self._lib_dir = data_root / "character_library"
        self._lib_dir.mkdir(parents=True, exist_ok=True)
        self._store = store

    # ── 创建 ────────────────────────────────────────────────

    def create_character(self, character: CosplayCharacter) -> CosplayCharacter:
        char_dir = self._lib_dir / character.id
        char_dir.mkdir(parents=True, exist_ok=True)
        (char_dir / "reference").mkdir(exist_ok=True)

        character.created_at = _now_iso()
        character.updated_at = _now_iso()
        self._write_character(character)
        self._update_index(character, action="add")
        _logger.info("character created: id={}, name={}", character.id, character.name)
        return character

    def create_from_dict(self, data: dict[str, Any]) -> CosplayCharacter:
        char = CosplayCharacter.from_dict(data)
        return self.create_character(char)

    # ── 读取 ────────────────────────────────────────────────

    def get_character(self, char_id: str) -> CosplayCharacter | None:
        char_path = self._lib_dir / char_id / "character.json"
        if not char_path.exists():
            return None
        try:
            data = json.loads(char_path.read_text(encoding="utf-8"))
            return CosplayCharacter.from_dict(data)
        except Exception:
            _logger.warning("failed to read character: {}", char_id)
            return None

    def list_characters(
        self,
        tag: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        index = self._load_index()
        items = list(index.values())
        if tag:
            items = [c for c in items if tag in c.get("tags", [])]
        items.sort(key=lambda c: c.get("updated_at", ""), reverse=True)
        return items[offset:offset + limit]

    # ── 更新 ────────────────────────────────────────────────

    def update_character(self, char_id: str, **kwargs: Any) -> CosplayCharacter | None:
        char = self.get_character(char_id)
        if not char:
            return None
        for k, v in kwargs.items():
            if hasattr(char, k):
                setattr(char, k, v)
        char.updated_at = _now_iso()
        self._write_character(char)
        self._update_index(char, action="update")
        return char

    def update_from_dict(self, char_id: str, data: dict[str, Any]) -> CosplayCharacter | None:
        existing = self.get_character(char_id)
        if not existing:
            return None
        updated = CosplayCharacter.from_dict({**existing.to_dict(), **data, "id": char_id})
        updated.updated_at = _now_iso()
        self._write_character(updated)
        self._update_index(updated, action="update")
        return updated

    # ── 参考图 ──────────────────────────────────────────────

    def add_reference_image(self, char_id: str, filename: str, data: bytes) -> str | None:
        ref_dir = self._lib_dir / char_id / "reference"
        ref_dir.mkdir(parents=True, exist_ok=True)
        ref_path = ref_dir / filename
        ref_path.write_bytes(data)
        rel_path = f"character_library/{char_id}/reference/{filename}"

        # 更新角色的参考图列表
        char = self.get_character(char_id)
        if char:
            if rel_path not in char.reference_images:
                char.reference_images.append(rel_path)
                char.updated_at = _now_iso()
                self._write_character(char)
        return rel_path

    def get_reference_dir(self, char_id: str) -> Path:
        return self._lib_dir / char_id / "reference"

    # ── 删除 ────────────────────────────────────────────────

    def delete_character(self, char_id: str) -> bool:
        char_dir = self._lib_dir / char_id
        if not char_dir.exists():
            return False
        shutil.rmtree(char_dir)
        self._update_index_by_id(char_id, action="remove")
        _logger.info("character deleted: {}", char_id)
        return True

    # ── 索引 ────────────────────────────────────────────────

    def _load_index(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self._store.get(INDEX_KEY, "{}")
            if isinstance(raw, str):
                return json.loads(raw)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        self._store.set(INDEX_KEY, json.dumps(index, ensure_ascii=False))

    def _update_index(self, char: CosplayCharacter, action: str) -> None:
        index = self._load_index()
        if action == "remove":
            index.pop(char.id, None)
        else:
            index[char.id] = {
                "id": char.id,
                "name": char.name,
                "costume_name": char.costume.name,
                "scene_name": char.scene.name,
                "tags": char.tags,
                "template_id": char.template_id,
                "created_at": char.created_at,
                "updated_at": char.updated_at,
            }
        self._save_index(index)

    def _update_index_by_id(self, char_id: str, action: str) -> None:
        index = self._load_index()
        if action == "remove":
            index.pop(char_id, None)
        self._save_index(index)

    def _write_character(self, char: CosplayCharacter) -> None:
        char_path = self._lib_dir / char.id / "character.json"
        char_path.parent.mkdir(parents=True, exist_ok=True)
        char_path.write_text(
            json.dumps(char.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
