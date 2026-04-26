from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional


class StrategyParser:
    def __init__(self, logger) -> None:
        self.logger = logger
        self._strategy_prompt_cache: Dict[str, str] = {}
        self._strategy_constraints_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def _strategies_dir(self) -> Path:
        return Path(__file__).with_name("strategies")

    def _normalize_character_strategy_name(self, strategy_name: Any) -> str:
        raw = str(strategy_name or "defect").strip().lower().replace(" ", "_")
        normalized = re.sub(r"[^a-z0-9_-]", "", raw)
        if not normalized:
            raise RuntimeError("角色策略名称不能为空")
        return normalized

    def _ensure_character_strategy_exists(self, strategy_name: str) -> Path:
        path = self._strategies_dir / f"{strategy_name}.md"
        if not path.exists() or not path.is_file():
            raise RuntimeError(f"未找到角色策略文档: {strategy_name}")
        return path

    def _load_strategy_prompt(self, strategy: str) -> Optional[str]:
        strategy_name = self._normalize_character_strategy_name(strategy)
        path = self._ensure_character_strategy_exists(strategy_name)
        cached = self._strategy_prompt_cache.get(strategy_name)
        if cached is not None:
            return cached
        try:
            prompt = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self.logger.warning(f"未找到策略文档: {path}")
            self._strategy_prompt_cache[strategy_name] = ""
            return None
        except Exception as exc:
            self.logger.warning(f"读取策略文档失败 {path}: {exc}")
            self._strategy_prompt_cache[strategy_name] = ""
            return None
        self._strategy_prompt_cache[strategy_name] = prompt
        return prompt or None

    def _load_strategy_constraints(self, strategy: str) -> Dict[str, Any]:
        strategy_name = self._normalize_character_strategy_name(strategy)
        cached = self._strategy_constraints_cache.get(strategy_name)
        if cached is not None:
            return cached
        prompt = self._load_strategy_prompt(strategy_name) or ""
        constraints = self._parse_strategy_constraints(prompt)
        self._strategy_constraints_cache[strategy_name] = constraints
        return constraints

    def _strategy_sections_for_constraints(self, prompt: str) -> str:
        headings = self._parse_strategy_heading_sections(prompt)
        supported_detail_titles = (
            "战斗偏好",
            "战斗估算",
            "估算规则",
            "商店遗物",
            "商店药水",
            "商店不可删除",
            "商店不可移除",
            "不可删除卡牌",
            "不可移除卡牌",
            "商店删牌规则",
            "流派必需牌",
            "流派高优先补强",
            "条件卡",
            "慎抓",
            "低优先",
            "高优先",
            "必需",
        )
        lines: list[str] = []
        for section in headings:
            title = str(section.get("title") or "")
            lines.extend(section.get("body_lines", []))
            for detail in section.get("details", []):
                detail_title = str(detail.get("title") or "")
                if any(token in detail_title for token in supported_detail_titles):
                    lines.append(f"#### {detail_title}")
                    lines.extend(detail.get("body_lines", []))
            if title == "战斗" and section.get("body_lines"):
                for detail in section.get("details", []):
                    detail_title = str(detail.get("title") or "")
                    if any(token in detail_title for token in {"战斗偏好", "战斗估算", "估算规则"}):
                        continue
        return "\n".join(lines).strip()

    def _parse_strategy_heading_sections(self, prompt: str) -> list[Dict[str, Any]]:
        sections: list[Dict[str, Any]] = []
        current_section: Optional[Dict[str, Any]] = None
        current_detail: Optional[Dict[str, Any]] = None
        for raw_line in (prompt or "").splitlines():
            section_match = re.match(r"^##\s+(.+?)\s*$", raw_line)
            if section_match:
                current_section = {"title": section_match.group(1).strip(), "body_lines": [], "details": []}
                sections.append(current_section)
                current_detail = None
                continue
            detail_match = re.match(r"^###\s+(.+?)\s*$", raw_line)
            if detail_match and current_section is not None:
                current_detail = {"title": detail_match.group(1).strip(), "body_lines": []}
                current_section["details"].append(current_detail)
                continue
            if current_detail is not None:
                current_detail["body_lines"].append(raw_line)
            elif current_section is not None:
                current_section["body_lines"].append(raw_line)
        return sections

    def _strategy_prompt_for_llm(self, strategy: str) -> Optional[str]:
        prompt = self._load_strategy_prompt(strategy)
        if not prompt:
            return None
        sections = self._parse_strategy_heading_sections(prompt)
        if not sections:
            return prompt
        rendered: list[str] = []
        for section in sections:
            title = str(section.get("title") or "").strip()
            if not title:
                continue
            rendered.append(f"## {title}")
            body_lines = section.get("body_lines") if isinstance(section.get("body_lines"), list) else []
            while body_lines and not str(body_lines[0]).strip():
                body_lines = body_lines[1:]
            rendered.extend(body_lines)
            for detail in section.get("details", []):
                detail_title = str(detail.get("title") or "").strip()
                if not detail_title:
                    continue
                rendered.append(f"### {detail_title}")
                rendered.extend(detail.get("body_lines") if isinstance(detail.get("body_lines"), list) else [])
            rendered.append("")
        return "\n".join(rendered).strip() or prompt

    def _parse_strategy_constraints(self, prompt: str) -> Dict[str, Any]:
        match = re.search(r"^#{2,3}\s*程序约束\s*$([\s\S]*?)(?=^##\s+|\Z)", prompt or "", flags=re.MULTILINE)
        if match:
            section = match.group(1)
        else:
            section = self._strategy_sections_for_constraints(prompt or "")
            if not section:
                return {}
        constraints: Dict[str, Any] = {
            "required": {},
            "high_priority": {},
            "conditional": {},
            "low_priority": {},
            "combat_preferences": {},
            "combat_estimators": {},
            "shop_preferences": {
                "relic": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}},
                "potion": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}},
                "card": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}, "unremovable": {}},
            },
        }
        current_category = ""
        current_shop_type = ""
        for raw_line in section.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            heading = re.match(r"^#{3,4}\s*(.+?)\s*$", line)
            if heading:
                title = heading.group(1).strip()
                if title == "程序约束":
                    continue
                current_shop_type = ""
                if "战斗偏好" in title or "战斗策略" in title:
                    current_category = "combat_preferences"
                elif "战斗估算" in title or "估算规则" in title:
                    current_category = "combat_estimators"
                elif "商店不可删除" in title or "商店不可移除" in title or "不可删除卡牌" in title or "不可移除卡牌" in title:
                    current_shop_type = "card"
                    current_category = "unremovable"
                elif "商店遗物" in title:
                    current_shop_type = "relic"
                    if "必需" in title:
                        current_category = "required"
                    elif "高优先" in title or "补强" in title:
                        current_category = "high_priority"
                    elif "条件" in title:
                        current_category = "conditional"
                    elif "慎买" in title or "低优先" in title:
                        current_category = "low_priority"
                    else:
                        current_category = ""
                elif "商店药水" in title:
                    current_shop_type = "potion"
                    if "必需" in title:
                        current_category = "required"
                    elif "高优先" in title or "补强" in title:
                        current_category = "high_priority"
                    elif "条件" in title:
                        current_category = "conditional"
                    elif "慎买" in title or "低优先" in title:
                        current_category = "low_priority"
                    else:
                        current_category = ""
                elif "必需" in title:
                    current_category = "required"
                elif "高优先" in title or "补强" in title:
                    current_category = "high_priority"
                elif "条件" in title:
                    current_category = "conditional"
                elif "慎抓" in title or "低优先" in title:
                    current_category = "low_priority"
                else:
                    current_category = ""
                continue
            if not current_category or not line.startswith("-"):
                continue
            body = line.lstrip("-").strip()
            if ":" in body:
                key, values = body.split(":", 1)
            elif "：" in body:
                key, values = body.split("：", 1)
            else:
                key, values = current_category, body
            key = key.strip()
            value_part = values.strip()
            target_constraints: Any = constraints["shop_preferences"][current_shop_type][current_category] if current_shop_type else constraints[current_category]
            if current_category == "combat_preferences":
                primary_value, *qualifiers = re.split(r"\|", value_part, maxsplit=1)
                keywords = [item.strip().lower() for item in re.split(r"[,，、]", primary_value) if item.strip()]
                entry = constraints[current_category].setdefault(key, {"keywords": [], "conditions": []})
                for keyword in keywords:
                    if keyword not in entry["keywords"]:
                        entry["keywords"].append(keyword)
                if qualifiers:
                    condition_text = qualifiers[0].strip()
                    if condition_text and condition_text not in entry["conditions"]:
                        entry["conditions"].append(condition_text)
                continue
            if current_category == "combat_estimators":
                primary_value, *qualifiers = re.split(r"\|", value_part, maxsplit=1)
                fields: Dict[str, str] = {}
                keywords: list[str] = []
                for item in re.split(r"[,，、]", primary_value):
                    item = item.strip()
                    if not item:
                        continue
                    if "=" in item:
                        field_key, field_value = item.split("=", 1)
                        fields[field_key.strip().lower()] = field_value.strip().lower()
                    else:
                        keywords.append(item.lower())
                entry = constraints[current_category].setdefault(key, {"keywords": [], "conditions": []})
                entry.update(fields)
                for keyword in keywords:
                    if keyword not in entry["keywords"]:
                        entry["keywords"].append(keyword)
                if qualifiers:
                    condition_text = qualifiers[0].strip()
                    if condition_text and condition_text not in entry["conditions"]:
                        entry["conditions"].append(condition_text)
                continue
            if current_category == "conditional":
                primary_value, *qualifiers = re.split(r"\|", value_part, maxsplit=1)
                cards = [card.strip().lower() for card in re.split(r"[,，、]", primary_value) if card.strip()]
                if not cards:
                    continue
                entry = target_constraints.setdefault(key, {"items": [], "conditions": []})
                for card in cards:
                    if card not in entry["items"]:
                        entry["items"].append(card)
                if qualifiers:
                    condition_text = qualifiers[0].strip()
                    if condition_text and condition_text not in entry["conditions"]:
                        entry["conditions"].append(condition_text)
                continue
            if current_category == "unremovable":
                cards = [card.strip().lower() for card in re.split(r"[,，、]", value_part.split("|", 1)[0]) if card.strip()]
                if not cards:
                    continue
                existing = target_constraints.setdefault(key, [])
                for card in cards:
                    if card not in existing:
                        existing.append(card)
                continue
            cards = [card.strip().lower() for card in re.split(r"[,，、]", value_part.split("|", 1)[0]) if card.strip()]
            if not cards:
                continue
            existing = target_constraints.setdefault(key, [])
            for card in cards:
                if card not in existing:
                    existing.append(card)
        return constraints
