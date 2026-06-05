# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownMemberType=false, reportUnknownParameterType=false
"""Cosplay 插件的独立数据层 / 业务类（与其它插件零耦合）。

提供：
- CosplayCharacter / Work 数据类
- WorkManager       作品 CRUD
- CharacterLibrary  角色库 CRUD
- TemplateManager   内置剧本/角色模板
- WorkExporter      作品导出（HTML / ZIP）
"""

from __future__ import annotations

import json
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


# ══════════════════════════════════════════════════════
#  数据类
# ══════════════════════════════════════════════════════


@dataclass
class CosplayCharacter:
    """角色三要素：身份 + 服装 + 场景。"""

    id: str = ""
    name: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    structured: dict[str, Any] = field(default_factory=dict)  # 物理特征：发型/瞳色/身高/种族…
    costume: dict[str, Any] = field(default_factory=dict)    # 服装：风格/颜色/配饰…
    scene: dict[str, Any] = field(default_factory=dict)      # 场景：时间/地点/氛围…
    created_at: float = 0.0
    updated_at: float = 0.0

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CosplayCharacter":
        data = data or {}
        return cls(
            id=str(data.get("id", "") or ""),
            name=str(data.get("name", "") or ""),
            description=str(data.get("description", "") or ""),
            tags=list(data.get("tags") or []),
            structured=dict(data.get("structured") or {}),
            costume=dict(data.get("costume") or {}),
            scene=dict(data.get("scene") or {}),
            created_at=float(data.get("created_at", 0) or 0),
            updated_at=float(data.get("updated_at", 0) or 0),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Work:
    """剧场 / 互动模式作品。"""

    id: str = ""
    title: str = ""
    mode: str = "theater"  # "theater" | "interactive"
    description: str = ""
    tags: list[str] = field(default_factory=list)
    favorited: bool = False
    pinned: bool = False
    nodes: list[dict[str, Any]] = field(default_factory=list)   # 幕列表
    roles: list[dict[str, Any]] = field(default_factory=list)  # 角色映射
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ══════════════════════════════════════════════════════
#  作品管理
# ══════════════════════════════════════════════════════


class WorkManager:
    """作品 CRUD；以 JSON 文件持久化到 ``data_root/works/``。"""

    def __init__(self, data_root: Path, store: Any = None):
        self._dir = Path(data_root) / "works"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._store = store  # 预留 PluginStore，备用

    def _path(self, work_id: str) -> Path:
        return self._dir / f"{work_id}.json"

    def create_work(
        self,
        title: str,
        mode: str = "theater",
        tags: list[str] | None = None,
        description: str = "",
    ) -> Work:
        now = time.time()
        work = Work(
            id=uuid.uuid4().hex[:12],
            title=title or "未命名作品",
            mode=mode if mode in ("theater", "interactive") else "theater",
            description=description or "",
            tags=list(tags or []),
            created_at=now,
            updated_at=now,
        )
        self._save(work)
        return work

    def get_work(self, work_id: str) -> Work | None:
        p = self._path(work_id)
        if not p.exists():
            return None
        try:
            return Work(**json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_works(
        self,
        tag: str | None = None,
        favorited: bool | None = None,
        mode: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for p in sorted(self._dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                w = Work(**json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
            if tag and tag not in w.tags:
                continue
            if favorited is not None and w.favorited != favorited:
                continue
            if mode and w.mode != mode:
                continue
            items.append(w.to_dict())
            if len(items) >= max(limit, 1) * 3:  # 收集稍多以便排序后再裁剪
                break

        items.sort(
            key=lambda x: (
                not x.get("pinned", False),
                -float(x.get("updated_at", 0) or 0),
            )
        )
        return items[: max(limit, 1)]

    def delete_work(self, work_id: str) -> bool:
        p = self._path(work_id)
        if p.exists():
            p.unlink()
            return True
        return False

    def favorite_work(self, work_id: str, favorited: bool) -> bool:
        w = self.get_work(work_id)
        if not w:
            return False
        w.favorited = bool(favorited)
        w.updated_at = time.time()
        self._save(w)
        return True

    def pin_work(self, work_id: str, pinned: bool) -> bool:
        w = self.get_work(work_id)
        if not w:
            return False
        w.pinned = bool(pinned)
        w.updated_at = time.time()
        self._save(w)
        return True

    def update_work_meta(self, work_id: str, **fields: Any) -> bool:
        w = self.get_work(work_id)
        if not w:
            return False
        for k, v in fields.items():
            if hasattr(w, k):
                setattr(w, k, v)
        w.updated_at = time.time()
        self._save(w)
        return True

    def save_nodes(self, work_id: str, nodes: list[dict[str, Any]]) -> bool:
        w = self.get_work(work_id)
        if not w:
            return False
        w.nodes = list(nodes or [])
        w.updated_at = time.time()
        self._save(w)
        return True

    def _save(self, w: Work) -> None:
        self._path(w.id).write_text(
            json.dumps(w.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


# ══════════════════════════════════════════════════════
#  角色库
# ══════════════════════════════════════════════════════


class CharacterLibrary:
    """角色库 CRUD；以 JSON 文件持久化到 ``data_root/characters/``。"""

    _WRITABLE_FIELDS = ("name", "description", "tags", "structured", "costume", "scene")

    def __init__(self, data_root: Path, store: Any = None):
        self._dir = Path(data_root) / "characters"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._store = store

    def _path(self, cid: str) -> Path:
        return self._dir / f"{cid}.json"

    def create_character(self, char: CosplayCharacter) -> CosplayCharacter:
        if not char.id:
            char.id = uuid.uuid4().hex[:12]
        now = time.time()
        if not char.created_at:
            char.created_at = now
        char.updated_at = now
        self._path(char.id).write_text(
            json.dumps(char.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return char

    def get_character(self, cid: str) -> CosplayCharacter | None:
        p = self._path(cid)
        if not p.exists():
            return None
        try:
            return CosplayCharacter.from_dict(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            return None

    def list_characters(self, tag: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for p in sorted(self._dir.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
            try:
                c = CosplayCharacter.from_dict(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
            if tag and tag not in c.tags:
                continue
            items.append(c.to_dict())
            if len(items) >= limit:
                break
        return items

    def update_from_dict(self, cid: str, fields: dict[str, Any]) -> CosplayCharacter | None:
        c = self.get_character(cid)
        if not c:
            return None
        for k in self._WRITABLE_FIELDS:
            if k in fields:
                setattr(c, k, fields[k])
        c.updated_at = time.time()
        self._path(c.id).write_text(
            json.dumps(c.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return c

    def delete_character(self, cid: str) -> bool:
        p = self._path(cid)
        if p.exists():
            p.unlink()
            return True
        return False


# ══════════════════════════════════════════════════════
#  模板
# ══════════════════════════════════════════════════════


class TemplateManager:
    """内置剧本/角色模板。无需持久化，启动时即可用。"""

    _TEMPLATES: list[dict[str, Any]] = [
        {
            "id": "school_rooftop_confession",
            "category": "校园",
            "name": "校园天台告白",
            "description": "经典日漫天台场景：黄昏、樱花、两人独处、暧昧告白。",
            "default_character": {
                "name": "校园女生",
                "structured": {"age": "17-18", "hair": "黑色长发", "eyes": "棕色", "height": "165cm"},
                "costume": {"style": "JK制服", "color": "藏青+白", "accessory": "发卡"},
                "scene": {"time": "黄昏", "location": "学校天台", "mood": "暧昧温馨"},
            },
        },
        {
            "id": "fantasy_elf_archer",
            "category": "奇幻",
            "name": "奇幻精灵弓手",
            "description": "中世纪奇幻风：精灵族、女弓手、银发绿瞳、披风。",
            "default_character": {
                "name": "精灵弓手",
                "structured": {"race": "精灵", "age": "永生外貌", "hair": "银色长发", "eyes": "翡翠绿"},
                "costume": {"style": "轻甲", "color": "深绿+金", "accessory": "长弓+披风"},
                "scene": {"time": "黎明", "location": "古老森林", "mood": "宁静警惕"},
            },
        },
        {
            "id": "cyberpunk_neon_alley",
            "category": "赛博朋克",
            "name": "霓虹小巷相遇",
            "description": "未来都市、霓虹灯、雨夜、神秘女性。",
            "default_character": {
                "name": "神秘女性",
                "structured": {"age": "25-30", "hair": "紫色短发", "eyes": "机械义眼"},
                "costume": {"style": "机能风", "color": "黑+紫", "accessory": "机械臂"},
                "scene": {"time": "深夜", "location": "霓虹小巷", "mood": "紧张神秘"},
            },
        },
        {
            "id": "ancient_chinese_scholar",
            "category": "古风",
            "name": "古风才子佳人",
            "description": "古代中国、月下、亭台、书生与小姐。",
            "default_character": {
                "name": "古代闺秀",
                "structured": {"age": "18", "hair": "乌黑云髻", "eyes": "杏眼"},
                "costume": {"style": "襦裙", "color": "鹅黄+烟青", "accessory": "团扇"},
                "scene": {"time": "月夜", "location": "后花园", "mood": "含蓄优雅"},
            },
        },
        {
            "id": "magic_academy_rival",
            "category": "奇幻",
            "name": "魔法学院对手",
            "description": "霍格沃茨式魔法学院、同学对手、魔法对决。",
            "default_character": {
                "name": "魔法学徒",
                "structured": {"age": "16-17", "hair": "深红卷发", "eyes": "金"},
                "costume": {"style": "魔法学徒袍", "color": "墨绿+银", "accessory": "魔杖+围巾"},
                "scene": {"time": "清晨", "location": "魔法学院走廊", "mood": "活力紧张"},
            },
        },
        {
            "id": "modern_office_colleague",
            "category": "现代都市",
            "name": "职场办公室",
            "description": "现代写字楼、加班夜晚、暧昧的同事关系。",
            "default_character": {
                "name": "女同事",
                "structured": {"age": "26", "hair": "深栗短发", "eyes": "黑色"},
                "costume": {"style": "职业装", "color": "黑色+米白", "accessory": "细框眼镜"},
                "scene": {"time": "深夜", "location": "写字楼办公室", "mood": "克制暧昧"},
            },
        },
    ]

    def list_templates(self, category: str | None = None) -> list[dict[str, Any]]:
        if not category:
            return list(self._TEMPLATES)
        return [t for t in self._TEMPLATES if t.get("category") == category]

    def get_categories(self) -> list[str]:
        return sorted({t.get("category", "") for t in self._TEMPLATES if t.get("category")})

    def get_template(self, tid: str) -> dict[str, Any] | None:
        for t in self._TEMPLATES:
            if t.get("id") == tid:
                return t
        return None

    def apply_template(self, tid: str, overrides: dict[str, Any] | None = None) -> CosplayCharacter | None:
        tpl = self.get_template(tid)
        if not tpl:
            return None
        defaults = dict(tpl.get("default_character") or {})
        merged = {**defaults, **(overrides or {})}
        char = CosplayCharacter.from_dict(merged)
        char.description = str(tpl.get("description", "") or "")
        cat = tpl.get("category")
        if cat and cat not in char.tags:
            char.tags.append(cat)
        return char


# ══════════════════════════════════════════════════════
#  导出器
# ══════════════════════════════════════════════════════


class WorkExporter:
    """作品导出（HTML / ZIP）。"""

    def __init__(self, data_root: Path):
        self._works_dir = Path(data_root) / "works"
        self._export_dir = Path(data_root) / "exports"
        self._export_dir.mkdir(parents=True, exist_ok=True)

    def _load(self, work_id: str) -> dict[str, Any] | None:
        p = self._works_dir / f"{work_id}.json"
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    @staticmethod
    def _html_escape(text: str) -> str:
        return (
            str(text or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _build_html(self, work: dict[str, Any]) -> str:
        nodes = work.get("nodes") or []
        parts: list[str] = [
            "<!DOCTYPE html>",
            "<html lang=zh-CN><head><meta charset='utf-8'>",
            f"<title>{self._html_escape(work.get('title', '作品'))}</title>",
            (
                "<style>"
                "body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
                "max-width:960px;margin:0 auto;padding:24px;background:#0b0b0f;color:#eee;line-height:1.6;}"
                ".scene{margin:32px 0;padding:20px;border-radius:10px;background:#15151c;box-shadow:0 2px 8px rgba(0,0,0,0.4);}"
                ".scene h2{margin:0 0 12px;color:#aef0c4;}"
                ".scene img{width:100%;border-radius:8px;display:block;}"
                ".scene video{width:100%;border-radius:8px;display:block;}"
                ".line{margin:8px 0;padding:10px 14px;border-left:3px solid #5ae0a0;background:#1c1c25;border-radius:4px;}"
                ".line .role{color:#5ae0a0;font-weight:600;margin-right:6px;}"
                ".meta{color:#888;font-size:0.9em;}"
                "h1{color:#fff;border-bottom:2px solid #5ae0a0;padding-bottom:8px;}"
                "</style></head><body>"
            ),
            f"<h1>{self._html_escape(work.get('title', '未命名作品'))}</h1>",
            f"<p class='meta'>{self._html_escape(work.get('description', ''))}</p>",
            f"<p class='meta'>模式：{self._html_escape(work.get('mode', ''))}"
            f"　|　标签：{self._html_escape(', '.join(work.get('tags') or []))}</p>",
        ]
        for idx, n in enumerate(nodes):
            parts.append(f"<div class='scene'><h2>第 {idx+1} 幕</h2>")
            img = str(n.get("image_url") or "")
            if img:
                parts.append(f"<img src='{self._html_escape(img)}' alt='scene {idx+1}' />")
            vid = str(n.get("video_url") or "")
            if vid:
                parts.append(f"<video controls src='{self._html_escape(vid)}'></video>")
            for ln in n.get("lines") or []:
                who = self._html_escape(ln.get("role") or "旁白")
                text = self._html_escape(ln.get("text") or "")
                parts.append(f"<div class='line'><span class='role'>{who}：</span>{text}</div>")
            parts.append("</div>")
        parts.append("</body></html>")
        return "\n".join(parts)

    def export_html(self, work_id: str) -> Path | None:
        work = self._load(work_id)
        if not work:
            return None
        out = self._export_dir / f"{work_id}.html"
        out.write_text(self._build_html(work), encoding="utf-8")
        return out

    def export_zip(self, work_id: str) -> Path | None:
        work = self._load(work_id)
        if not work:
            return None
        out = self._export_dir / f"{work_id}.zip"
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("work.json", json.dumps(work, ensure_ascii=False, indent=2))
            zf.writestr("preview.html", self._build_html(work))
        return out
