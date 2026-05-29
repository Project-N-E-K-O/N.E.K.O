"""Cosplay 作品导出器。

支持导出为：
  - HTML（自包含页面，内嵌图片）
  - ZIP（图片集 + 元数据）
"""
from __future__ import annotations

import base64
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from plugin.logging_config import get_logger

_logger = get_logger("cosplay.exporter")


class WorkExporter:
    """作品导出。"""

    def __init__(self, works_dir: Path) -> None:
        self._works_dir = works_dir

    def export_html(self, work_id: str, output_path: Path | None = None) -> Path | None:
        """导出为自包含 HTML 文件。"""
        work_dir = self._works_dir / work_id
        if not work_dir.exists():
            return None

        meta = self._read_json(work_dir / "meta.json")
        script = self._read_json(work_dir / "script.json")
        dialogue = self._read_json(work_dir / "dialogue_record.json")

        title = meta.get("title", "未命名作品")
        mode = meta.get("mode", "theater")
        created = meta.get("created_at", "")
        tags = meta.get("tags", [])
        description = meta.get("description", "")

        # 收集图片
        assets_dir = work_dir / "assets"
        images = []
        if assets_dir.exists():
            for img_path in sorted(assets_dir.glob("*")):
                if img_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                    b64 = base64.b64encode(img_path.read_bytes()).decode()
                    mime = f"image/{img_path.suffix.lstrip('.').replace('jpg', 'jpeg')}"
                    images.append(f"data:{mime};base64,{b64}")

        # 构建内容
        if mode == "theater":
            content_html = self._build_theater_html(script, images)
        else:
            content_html = self._build_interactive_html(dialogue, images)

        tags_html = "".join(f'<span class="tag">{t}</span>' for t in tags)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{self._esc(title)} - Cosplay 作品</title>
<style>
  body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: #0a0a0f; color: #e0e0f0; max-width: 900px; margin: 0 auto; padding: 24px; }}
  h1 {{ background: linear-gradient(135deg, #ff6b9d, #c084fc); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }}
  .meta {{ color: #8888aa; margin: 8px 0 24px; }}
  .tags {{ display: flex; gap: 6px; margin: 8px 0; }}
  .tag {{ background: #1e1e2e; color: #c084fc; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; }}
  .scene {{ background: #14141f; border: 1px solid #2a2a3a; border-radius: 12px; padding: 20px; margin: 16px 0; }}
  .scene-title {{ color: #ff6b9d; font-weight: 600; margin-bottom: 12px; }}
  .scene img {{ max-width: 100%; border-radius: 8px; margin: 12px 0; }}
  .dialogue {{ margin: 8px 0; }}
  .char-name {{ color: #ff6b9d; font-weight: 600; }}
  .narration {{ color: #8888aa; font-style: italic; }}
  .inner {{ color: #c084fc; }}
  .msg {{ padding: 12px 16px; margin: 8px 0; border-radius: 12px; max-width: 80%; }}
  .msg.user {{ background: #ff6b9d; color: #fff; margin-left: auto; border-bottom-right-radius: 4px; }}
  .msg.assistant {{ background: #1e1e2e; border-bottom-left-radius: 4px; }}
  .gallery {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(250px, 1fr)); gap: 12px; margin: 24px 0; }}
  .gallery img {{ width: 100%; border-radius: 8px; cursor: pointer; }}
  .gallery img:hover {{ transform: scale(1.02); }}
  .footer {{ text-align: center; color: #8888aa; margin-top: 48px; font-size: 0.85em; }}
</style>
</head>
<body>
<h1>🎭 {self._esc(title)}</h1>
<div class="meta">{self._esc(description)}</div>
<div class="meta">创建时间：{self._esc(created)} · 模式：{'剧场' if mode == 'theater' else '互动'}</div>
<div class="tags">{tags_html}</div>
{content_html}
<div class="footer">由 N.E.K.O Cosplay 插件生成</div>
</body>
</html>"""

        out = output_path or (work_dir / f"export_{work_id}.html")
        out.write_text(html, encoding="utf-8")
        _logger.info("exported HTML: {}", out)
        return out

    def export_zip(self, work_id: str, output_path: Path | None = None) -> Path | None:
        """导出为 ZIP（图片集 + 元数据）。"""
        work_dir = self._works_dir / work_id
        if not work_dir.exists():
            return None

        meta = self._read_json(work_dir / "meta.json")
        title = meta.get("title", "未命名作品")

        out = output_path or (work_dir / f"export_{work_id}.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            # 写入元数据
            zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=2))

            # 写入剧本/对话
            script_path = work_dir / "script.json"
            if script_path.exists():
                zf.writestr("script.json", script_path.read_text(encoding="utf-8"))

            dialogue_path = work_dir / "dialogue_record.json"
            if dialogue_path.exists():
                zf.writestr("dialogue_record.json", dialogue_path.read_text(encoding="utf-8"))

            # 写入图片
            assets_dir = work_dir / "assets"
            if assets_dir.exists():
                for img_path in sorted(assets_dir.glob("*")):
                    if img_path.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp4"):
                        zf.write(img_path, f"assets/{img_path.name}")

            # 写入配置快照
            snap_path = work_dir / "config_snapshot.json"
            if snap_path.exists():
                zf.writestr("config_snapshot.json", snap_path.read_text(encoding="utf-8"))

        _logger.info("exported ZIP: {}", out)
        return out

    def _build_theater_html(self, script: list[dict], images: list[str]) -> str:
        if not script:
            return '<div style="color:#8888aa;text-align:center;padding:24px">无剧本数据</div>'

        parts = []
        img_idx = 0
        for node in script:
            title = node.get("title", "")
            dialogues = node.get("dialogues", [])
            scene_desc = node.get("scene_desc", "")

            parts.append('<div class="scene">')
            if title:
                parts.append(f'<div class="scene-title">🎬 {self._esc(title)}</div>')
            if scene_desc:
                parts.append(f'<div style="color:#8888aa;margin-bottom:8px">{self._esc(scene_desc)}</div>')

            # 插入图片
            if img_idx < len(images):
                parts.append(f'<img src="{images[img_idx]}" alt="场景{img_idx+1}">')
                img_idx += 1

            for d in dialogues:
                char = d.get("character", "")
                text = d.get("text", "")
                if d.get("is_narration"):
                    parts.append(f'<div class="dialogue narration">{self._esc(text)}</div>')
                elif d.get("is_inner"):
                    parts.append(f'<div class="dialogue inner">（{self._esc(char)}）{self._esc(text)}</div>')
                elif char:
                    parts.append(f'<div class="dialogue"><span class="char-name">{self._esc(char)}：</span>{self._esc(text)}</div>')
                else:
                    parts.append(f'<div class="dialogue">{self._esc(text)}</div>')
            parts.append('</div>')

        return "\n".join(parts)

    def _build_interactive_html(self, dialogues: list[dict], images: list[str]) -> str:
        if not dialogues:
            return '<div style="color:#8888aa;text-align:center;padding:24px">无对话记录</div>'

        parts = ['<div style="max-width:600px;margin:0 auto">']
        img_idx = 0
        for msg in dialogues:
            role = msg.get("role", "user")
            text = msg.get("text", "")
            parts.append(f'<div class="msg {role}">{self._esc(text)}</div>')
            if msg.get("image_url") and img_idx < len(images):
                parts.append(f'<img src="{images[img_idx]}" style="max-width:300px;border-radius:8px;margin:4px 0">')
                img_idx += 1
        parts.append('</div>')
        return "\n".join(parts)

    def _read_json(self, path: Path) -> dict | list:
        if not path.exists():
            return {} if "meta" in path.name else []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return {} if "meta" in path.name else []

    @staticmethod
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
