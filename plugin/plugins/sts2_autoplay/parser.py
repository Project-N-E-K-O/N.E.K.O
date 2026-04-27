from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Optional

import yaml


class StrategyParser:
    def __init__(self, logger) -> None:
        self.logger = logger
        self._strategy_prompt_cache: Dict[str, str] = {}
        self._strategy_constraints_cache: Dict[str, Dict[str, Any]] = {}

    @property
    def _strategies_dir(self) -> Path:
        return Path(__file__).with_name("strategies")

    _CHARACTER_STRATEGY_ALIASES = {
        "defect": "defect",
        "the_defect": "defect",
        "故障机器人": "defect",
        "鸡煲": "defect",
        "雞煲": "defect",
        "机器人": "defect",
        "機器人": "defect",
        "ironclad": "ironclad",
        "the_ironclad": "ironclad",
        "铁甲战士": "ironclad",
        "鐵甲戰士": "ironclad",
        "战士": "ironclad",
        "戰士": "ironclad",
        "铁甲": "ironclad",
        "鐵甲": "ironclad",
        "红战士": "ironclad",
        "紅戰士": "ironclad",
        "silent_hunter": "silent_hunter",
        "silent": "silent_hunter",
        "the_silent": "silent_hunter",
        "静默猎手": "silent_hunter",
        "靜默獵手": "silent_hunter",
        "猎手": "silent_hunter",
        "獵手": "silent_hunter",
        "necrobinder": "necrobinder",
        "the_necrobinder": "necrobinder",
        "死灵缚者": "necrobinder",
        "死靈縛者": "necrobinder",
        "死灵": "necrobinder",
        "死靈": "necrobinder",
        "regent": "regent",
        "the_regent": "regent",
        "摄政王": "regent",
        "攝政王": "regent",
    }

    def _available_character_strategies(self) -> list[str]:
        strategies_dir = self._strategies_dir
        if not strategies_dir.exists() or not strategies_dir.is_dir():
            return []
        return sorted(path.stem for path in strategies_dir.glob("*.md") if path.is_file())

    def _normalize_character_strategy_name(self, strategy_name: Any) -> str:
        raw = str(strategy_name or "defect").strip().lower().replace(" ", "_")
        alias = self._CHARACTER_STRATEGY_ALIASES.get(raw)
        if alias:
            return alias
        normalized = re.sub(r"[^a-z0-9_-]", "", raw)
        if not normalized:
            available = ", ".join(self._available_character_strategies()) or "无"
            raise RuntimeError(f"角色策略名称不能为空或不受支持: {strategy_name!r}；可用策略: {available}")
        return self._CHARACTER_STRATEGY_ALIASES.get(normalized, normalized)

    def _ensure_character_strategy_exists(self, strategy_name: str) -> Path:
        strategy_name = self._normalize_character_strategy_name(strategy_name)
        path = self._strategies_dir / f"{strategy_name}.md"
        if not path.exists() or not path.is_file():
            available = ", ".join(self._available_character_strategies()) or "无"
            raise RuntimeError(f"未找到角色策略文档: {strategy_name}；期望路径: {path}；可用策略: {available}")
        return path

    def _load_strategy_prompt(self, strategy: str) -> Optional[str]:
        strategy_name = self._normalize_character_strategy_name(strategy)
        path = self._strategies_dir / f"{strategy_name}.md"
        if not path.exists() or not path.is_file():
            self.logger.warning(f"策略文档不存在: {path}，回退到 defect")
            strategy_name = "defect"
            path = self._strategies_dir / "defect.md"
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

    def _empty_strategy_constraints(self) -> Dict[str, Any]:
        return {
            "required": {},
            "high_priority": {},
            "conditional": {},
            "low_priority": {},
            "combat_preferences": {},
            "combat_estimators": {},
            "map_preferences": {},
            "shop_preferences": {
                "relic": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}},
                "potion": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}},
                "card": {"required": {}, "high_priority": {}, "conditional": {}, "low_priority": {}, "unremovable": {}},
            },
        }

    def _parse_strategy_frontmatter(self, prompt: str) -> Optional[Dict[str, Any]]:
        text = prompt or ""
        if not text.startswith("---"):
            return None
        match = re.match(r"^---\s*\r?\n([\s\S]*?)\r?\n---\s*(?:\r?\n|$)", text)
        if not match:
            raise RuntimeError("策略 Frontmatter 格式错误: 找到起始分隔符，但缺少结束分隔符")
        try:
            data = yaml.safe_load(match.group(1)) or {}
        except yaml.YAMLError as exc:
            raise RuntimeError(f"策略 Frontmatter YAML 解析失败: {exc}") from exc
        if not isinstance(data, dict):
            raise RuntimeError("策略 Frontmatter 必须解析为映射对象")
        constraints_data = data.get("constraints")
        if constraints_data is None:
            return None
        if not isinstance(constraints_data, dict):
            raise RuntimeError("策略 Frontmatter 字段 constraints 必须是映射对象")
        return constraints_data

    def _normalize_constraint_items(self, items: Any, *, with_conditions: bool = False) -> Any:
        if items is None:
            return {"items": [], "conditions": []} if with_conditions else []
        if isinstance(items, str):
            values = [item.strip().lower() for item in re.split(r"[,，、]", items) if item.strip()]
            return {"items": values, "conditions": []} if with_conditions else values
        if isinstance(items, list):
            values: list[str] = []
            conditions: list[str] = []
            for item in items:
                if isinstance(item, dict):
                    raw_items = item.get("items", item.get("aliases", item.get("keywords", [])))
                    normalized_items = self._normalize_constraint_items(raw_items)
                    for value in normalized_items:
                        if value not in values:
                            values.append(value)
                    raw_condition = item.get("condition", item.get("description", ""))
                    if raw_condition:
                        condition = str(raw_condition).strip()
                        if condition and condition not in conditions:
                            conditions.append(condition)
                    raw_conditions = item.get("conditions", [])
                    if isinstance(raw_conditions, list):
                        for condition_item in raw_conditions:
                            condition = str(condition_item).strip()
                            if condition and condition not in conditions:
                                conditions.append(condition)
                    continue
                value = str(item).strip().lower()
                if value and value not in values:
                    values.append(value)
            return {"items": values, "conditions": conditions} if with_conditions else values
        value = str(items).strip().lower()
        values = [value] if value else []
        return {"items": values, "conditions": []} if with_conditions else values

    def _merge_named_constraint_bucket(self, target: Dict[str, Any], bucket: Any, *, with_conditions: bool = False) -> None:
        if not isinstance(bucket, dict):
            return
        for label, items in bucket.items():
            key = str(label).strip()
            if not key:
                continue
            if with_conditions:
                normalized = self._normalize_constraint_items(items, with_conditions=True)
                entry = target.setdefault(key, {"items": [], "conditions": []})
                for item in normalized.get("items", []):
                    if item not in entry["items"]:
                        entry["items"].append(item)
                for condition in normalized.get("conditions", []):
                    if condition not in entry["conditions"]:
                        entry["conditions"].append(condition)
                continue
            normalized_items = self._normalize_constraint_items(items)
            existing = target.setdefault(key, [])
            for item in normalized_items:
                if item not in existing:
                    existing.append(item)

    def _merge_frontmatter_constraints(self, constraints: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
        for category in ("required", "high_priority", "low_priority"):
            self._merge_named_constraint_bucket(constraints[category], data.get(category))
        self._merge_named_constraint_bucket(constraints["conditional"], data.get("conditional"), with_conditions=True)
        self._merge_named_constraint_bucket(constraints["map_preferences"], data.get("map_preferences"), with_conditions=True)

        combat_preferences = data.get("combat_preferences")
        if isinstance(combat_preferences, dict):
            for label, items in combat_preferences.items():
                key = str(label).strip()
                if not key:
                    continue
                normalized = self._normalize_constraint_items(items, with_conditions=True)
                entry = constraints["combat_preferences"].setdefault(key, {"keywords": [], "conditions": []})
                for item in normalized.get("items", []):
                    if item not in entry["keywords"]:
                        entry["keywords"].append(item)
                for condition in normalized.get("conditions", []):
                    if condition not in entry["conditions"]:
                        entry["conditions"].append(condition)

        combat_estimators = data.get("combat_estimators")
        if isinstance(combat_estimators, dict):
            for label, value in combat_estimators.items():
                key = str(label).strip()
                if not key:
                    continue
                entry = constraints["combat_estimators"].setdefault(key, {"keywords": [], "conditions": []})
                if isinstance(value, dict):
                    normalized = self._normalize_constraint_items(value.get("keywords", value.get("items", [])), with_conditions=True)
                    for item in normalized.get("items", []):
                        if item not in entry["keywords"]:
                            entry["keywords"].append(item)
                    for field_key, field_value in value.items():
                        if field_key in {"keywords", "items", "conditions", "condition", "description"}:
                            continue
                        entry[str(field_key).strip().lower()] = str(field_value).strip().lower()
                    raw_condition = value.get("condition", value.get("description", ""))
                    if raw_condition:
                        condition = str(raw_condition).strip()
                        if condition and condition not in entry["conditions"]:
                            entry["conditions"].append(condition)
                    raw_conditions = value.get("conditions", [])
                    if isinstance(raw_conditions, list):
                        for condition_item in raw_conditions:
                            condition = str(condition_item).strip()
                            if condition and condition not in entry["conditions"]:
                                entry["conditions"].append(condition)
                else:
                    normalized = self._normalize_constraint_items(value, with_conditions=True)
                    for item in normalized.get("items", []):
                        if item not in entry["keywords"]:
                            entry["keywords"].append(item)
                    for condition in normalized.get("conditions", []):
                        if condition not in entry["conditions"]:
                            entry["conditions"].append(condition)

        shop_preferences = data.get("shop_preferences")
        if isinstance(shop_preferences, dict):
            for shop_type in ("relic", "potion", "card"):
                shop_data = shop_preferences.get(shop_type)
                if not isinstance(shop_data, dict):
                    continue
                for category in ("required", "high_priority", "low_priority", "unremovable"):
                    if category in constraints["shop_preferences"][shop_type]:
                        self._merge_named_constraint_bucket(constraints["shop_preferences"][shop_type][category], shop_data.get(category))
                self._merge_named_constraint_bucket(constraints["shop_preferences"][shop_type]["conditional"], shop_data.get("conditional"), with_conditions=True)
        return constraints

    def _parse_strategy_constraints(self, prompt: str) -> Dict[str, Any]:
        constraints = self._empty_strategy_constraints()
        frontmatter_constraints = self._parse_strategy_frontmatter(prompt or "")
        if frontmatter_constraints is not None:
            return self._merge_frontmatter_constraints(constraints, frontmatter_constraints)
        match = re.search(r"^#{2,3}\s*程序约束\s*$([\s\S]*?)(?=^##\s+|\Z)", prompt or "", flags=re.MULTILINE)
        if match:
            section = match.group(1)
        else:
            section = self._strategy_sections_for_constraints(prompt or "")
            if not section:
                return {}
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
