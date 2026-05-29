"""Cosplay 作品管理器。

负责作品的 CRUD、文件系统管理、索引维护。
存储结构：
  data/works/{work_id}/
    meta.json           # 作品元信息
    config_snapshot.json # 角色配置快照
    script.json          # 剧本/对话记录
    assets/              # 生成的图片
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger
from .cosplay_types import CosplayWork, StoryboardNode, _now_iso

_logger = get_logger("cosplay.work_manager")

INDEX_KEY = "cosplay_works_index"
MAX_INDEX_CACHE = 200


class WorkManager:
    """作品生命周期管理。"""

    def __init__(self, data_root: Path, store: Any) -> None:
        self._works_dir = data_root / "works"
        self._works_dir.mkdir(parents=True, exist_ok=True)
        self._store = store  # PluginStore 实例

    # ── 创建 ────────────────────────────────────────────────

    def create_work(
        self,
        title: str = "",
        mode: str = "theater",
        character_ids: list[str] | None = None,
        tags: list[str] | None = None,
        description: str = "",
    ) -> CosplayWork:
        work = CosplayWork(
            title=title or f"未命名作品 {_now_iso()}",
            mode=mode,
            character_ids=character_ids or [],
            tags=tags or [],
            description=description,
        )
        work_dir = self._works_dir / work.id
        work_dir.mkdir(parents=True, exist_ok=True)
        (work_dir / "assets").mkdir(exist_ok=True)

        self._write_meta(work)
        self._update_index(work, action="add")
        _logger.info("work created: id={}, title={}", work.id, work.title)
        return work

    # ── 读取 ────────────────────────────────────────────────

    def get_work(self, work_id: str) -> CosplayWork | None:
        meta_path = self._works_dir / work_id / "meta.json"
        if not meta_path.exists():
            return None
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            return CosplayWork.from_dict(data)
        except Exception:
            _logger.warning("failed to read work meta: {}", work_id)
            return None

    def list_works(
        self,
        tag: str | None = None,
        favorited: bool | None = None,
        mode: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """列出作品（从索引读取，支持筛选）。"""
        index = self._load_index()
        items = list(index.values())

        if tag:
            items = [w for w in items if tag in w.get("tags", [])]
        if favorited is not None:
            items = [w for w in items if w.get("favorited") == favorited]
        if mode:
            items = [w for w in items if w.get("mode") == mode]

        # 排序：置顶优先，然后按更新时间倒序
        items.sort(key=lambda w: (not w.get("pinned", False), w.get("updated_at", "")), reverse=True)
        return items[offset:offset + limit]

    # ── 更新 ────────────────────────────────────────────────

    def update_work_meta(self, work_id: str, **kwargs: Any) -> CosplayWork | None:
        work = self.get_work(work_id)
        if not work:
            return None
        for k, v in kwargs.items():
            if hasattr(work, k):
                setattr(work, k, v)
        work.updated_at = _now_iso()
        self._write_meta(work)
        self._update_index(work, action="update")
        return work

    def favorite_work(self, work_id: str, favorited: bool = True) -> bool:
        work = self.update_work_meta(work_id, favorited=favorited)
        return work is not None

    def pin_work(self, work_id: str, pinned: bool = True) -> bool:
        work = self.update_work_meta(work_id, pinned=pinned)
        return work is not None

    def set_cover(self, work_id: str, image_path: str) -> bool:
        work = self.update_work_meta(work_id, cover_image=image_path)
        return work is not None

    # ── 剧本/对话记录 ───────────────────────────────────────

    def save_script(self, work_id: str, nodes: list[StoryboardNode]) -> bool:
        work_dir = self._works_dir / work_id
        if not work_dir.exists():
            return False
        script_path = work_dir / "script.json"
        data = [n.to_dict() for n in nodes]
        script_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.update_work_meta(work_id, scene_count=len(nodes))
        return True

    def load_script(self, work_id: str) -> list[StoryboardNode]:
        script_path = self._works_dir / work_id / "script.json"
        if not script_path.exists():
            return []
        try:
            data = json.loads(script_path.read_text(encoding="utf-8"))
            return [StoryboardNode.from_dict(d) for d in data]
        except Exception:
            _logger.warning("failed to load script: {}", work_id)
            return []

    def save_dialogue_record(self, work_id: str, dialogues: list[dict[str, Any]]) -> bool:
        """保存互动模式的对话记录。"""
        work_dir = self._works_dir / work_id
        if not work_dir.exists():
            return False
        record_path = work_dir / "dialogue_record.json"
        record_path.write_text(json.dumps(dialogues, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def load_dialogue_record(self, work_id: str) -> list[dict[str, Any]]:
        record_path = self._works_dir / work_id / "dialogue_record.json"
        if not record_path.exists():
            return []
        try:
            return json.loads(record_path.read_text(encoding="utf-8"))
        except Exception:
            return []

    # ── 资源管理 ────────────────────────────────────────────

    def add_asset(self, work_id: str, filename: str, data: bytes) -> str | None:
        """往作品中添加图片资源，返回相对路径。"""
        assets_dir = self._works_dir / work_id / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        asset_path = assets_dir / filename
        asset_path.write_bytes(data)
        rel_path = f"works/{work_id}/assets/{filename}"
        # 更新图片计数
        count = len(list(assets_dir.glob("*")))
        self.update_work_meta(work_id, image_count=count)
        return rel_path

    def get_assets_dir(self, work_id: str) -> Path:
        return self._works_dir / work_id / "assets"

    # ── 角色配置快照 ────────────────────────────────────────

    def save_config_snapshot(self, work_id: str, config: dict[str, Any]) -> bool:
        work_dir = self._works_dir / work_id
        if not work_dir.exists():
            return False
        snap_path = work_dir / "config_snapshot.json"
        snap_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return True

    def load_config_snapshot(self, work_id: str) -> dict[str, Any]:
        snap_path = self._works_dir / work_id / "config_snapshot.json"
        if not snap_path.exists():
            return {}
        try:
            return json.loads(snap_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    # ── 删除 ────────────────────────────────────────────────

    def delete_work(self, work_id: str) -> bool:
        work_dir = self._works_dir / work_id
        if not work_dir.exists():
            return False
        shutil.rmtree(work_dir)
        self._update_index_by_id(work_id, action="remove")
        _logger.info("work deleted: {}", work_id)
        return True

    # ── 索引管理 ────────────────────────────────────────────

    def _load_index(self) -> dict[str, dict[str, Any]]:
        try:
            raw = self._store.get(INDEX_KEY, "{}")
            if isinstance(raw, str):
                return json.loads(raw)
            return raw if isinstance(raw, dict) else {}
        except Exception:
            return {}

    def _save_index(self, index: dict[str, dict[str, Any]]) -> None:
        # 限制索引大小
        if len(index) > MAX_INDEX_CACHE:
            sorted_items = sorted(index.items(), key=lambda x: x[1].get("updated_at", ""), reverse=True)
            index = dict(sorted_items[:MAX_INDEX_CACHE])
        self._store.set(INDEX_KEY, json.dumps(index, ensure_ascii=False))

    def _update_index(self, work: CosplayWork, action: str) -> None:
        index = self._load_index()
        if action == "remove":
            index.pop(work.id, None)
        else:
            index[work.id] = {
                "id": work.id,
                "title": work.title,
                "mode": work.mode,
                "tags": work.tags,
                "favorited": work.favorited,
                "pinned": work.pinned,
                "cover_image": work.cover_image,
                "scene_count": work.scene_count,
                "image_count": work.image_count,
                "created_at": work.created_at,
                "updated_at": work.updated_at,
            }
        self._save_index(index)

    def _update_index_by_id(self, work_id: str, action: str) -> None:
        index = self._load_index()
        if action == "remove":
            index.pop(work_id, None)
        self._save_index(index)

    def _write_meta(self, work: CosplayWork) -> None:
        meta_path = self._works_dir / work.id / "meta.json"
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(
            json.dumps(work.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
