"""Cosplay 模板管理器。

提供预设世界观模板，降低角色设定门槛。
"""
from __future__ import annotations

from typing import Any

from .cosplay_types import (
    CosplayCharacter,
    Costume,
    SceneDefinition,
    StructuredAttributes,
    _new_id,
)

# ── 内置模板 ────────────────────────────────────────────────

BUILTIN_TEMPLATES: dict[str, dict[str, Any]] = {
    "japanese_maid": {
        "id": "japanese_maid",
        "name": "日系女仆",
        "description": "经典日系女仆咖啡厅风格",
        "category": "日常",
        "character": {
            "structured": {"gender": "女", "age_range": "青年", "hair_style": "双马尾", "hair_color": "棕色", "personality": "温柔元气"},
            "costume": {"name": "女仆装", "style": "日系", "colors": ["黑白"], "accessories": ["头饰", "蕾丝围裙", "白色过膝袜"], "description": "经典黑白配色女仆装，蕾丝边饰"},
            "scene": {"environment": "室内", "location": "女仆咖啡厅", "time_of_day": "下午", "mood": "温馨可爱", "description": "柔和灯光，粉色装饰，甜品展示柜"},
        },
    },
    "hanfu_maiden": {
        "id": "hanfu_maiden",
        "name": "汉服少女",
        "description": "古典中国风汉服",
        "category": "古风",
        "character": {
            "structured": {"gender": "女", "age_range": "青年", "hair_style": "古典发髻", "hair_color": "黑色", "eye_color": "棕色", "personality": "温婉端庄"},
            "costume": {"name": "齐胸襦裙", "style": "汉服", "colors": ["淡粉", "月白"], "accessories": ["发簪", "步摇", "团扇"], "description": "淡粉色上襦，月白色下裙，飘逸丝带"},
            "scene": {"environment": "室外", "location": "古典园林", "time_of_day": "黄昏", "season": "春", "weather": "晴", "mood": "诗意浪漫", "description": "亭台楼阁，桃花盛开，小桥流水"},
        },
    },
    "mecha_pilot": {
        "id": "mecha_pilot",
        "name": "机甲战士",
        "description": "科幻机甲驾驶员",
        "category": "科幻",
        "character": {
            "structured": {"gender": "男", "age_range": "青年", "hair_style": "短发", "hair_color": "银白", "eye_color": "冰蓝", "personality": "冷静坚定"},
            "costume": {"name": "机甲驾驶服", "style": "科幻", "colors": ["深蓝", "银灰"], "accessories": ["头盔", "能量护臂", "战术背心"], "description": "紧身战斗服，发光线路纹路，肩甲"},
            "scene": {"environment": "室内", "location": "机甲驾驶舱", "time_of_day": "夜晚", "mood": "紧张热血", "description": "全息投影仪表盘，红色警报灯，星空透过舷窗"},
        },
    },
    "magic_girl": {
        "id": "magic_girl",
        "name": "魔法少女",
        "description": "经典魔法少女变身",
        "category": "奇幻",
        "character": {
            "structured": {"gender": "女", "age_range": "少女", "hair_style": "长卷发", "hair_color": "粉色", "eye_color": "紫罗兰", "personality": "活泼勇敢"},
            "costume": {"name": "魔法少女装", "style": "奇幻", "colors": ["粉白", "金色"], "accessories": ["魔杖", "蝴蝶结", "星星耳环", "白色长靴"], "description": "蓬蓬裙，星光装饰，翅膀形缎带"},
            "scene": {"environment": "幻想", "location": "星空之巅", "time_of_day": "夜晚", "mood": "梦幻华丽", "description": "漫天星斗，极光流转，魔法阵在脚下旋转"},
        },
    },
    "miko_shrine": {
        "id": "miko_shrine",
        "name": "和风巫女",
        "description": "日式神社巫女",
        "category": "和风",
        "character": {
            "structured": {"gender": "女", "age_range": "青年", "hair_style": "黑色长直发", "hair_color": "黑色", "eye_color": "深红", "personality": "清冷神秘"},
            "costume": {"name": "巫女装", "style": "和风", "colors": ["白", "红"], "accessories": ["发带", "注连绳腰饰"], "description": "白衣红袴，背后蝴蝶结"},
            "scene": {"environment": "室外", "location": "古老神社", "time_of_day": "黄昏", "season": "秋", "mood": "神秘庄严", "description": "石灯笼，枫叶飘落，鸟居剪影"},
        },
    },
    "cyberpunk_hacker": {
        "id": "cyberpunk_hacker",
        "name": "赛博黑客",
        "description": "赛博朋克风黑客",
        "category": "科幻",
        "character": {
            "structured": {"gender": "女", "age_range": "青年", "hair_style": "不对称短发", "hair_color": "霓虹蓝", "eye_color": "金色（义眼）", "personality": "叛逆聪明"},
            "costume": {"name": "赛博街头装", "style": "赛博朋克", "colors": ["黑", "霓虹蓝", "紫"], "accessories": ["VR眼镜", "发光项圈", "机械手套"], "description": "皮夹克，发光线路纹身，不对称剪裁"},
            "scene": {"environment": "室内", "location": "地下黑客巢穴", "time_of_day": "深夜", "mood": "暗酷科技", "description": "多层全息屏幕，霓虹灯管，线缆交错"},
        },
    },
    "gothic_lolita": {
        "id": "gothic_lolita",
        "name": "哥特萝莉",
        "description": "哥特洛丽塔风格",
        "category": "暗黑",
        "character": {
            "structured": {"gender": "女", "age_range": "少女", "hair_style": "双环髻", "hair_color": "银白", "eye_color": "血红", "personality": "优雅诡异"},
            "costume": {"name": "哥特洛丽塔裙", "style": "哥特", "colors": ["黑", "暗红"], "accessories": ["蕾丝阳伞", "十字架项链", "蝴蝶结发饰"], "description": "黑色蕾丝蓬蓬裙，暗红缎带，层叠荷叶边"},
            "scene": {"environment": "室内", "location": "废弃洋馆", "time_of_day": "黄昏", "mood": "诡异华丽", "description": "哥特式彩窗，烛光摇曳，藤蔓爬满墙壁"},
        },
    },
    "wuxia_sword": {
        "id": "wuxia_sword",
        "name": "武侠剑客",
        "description": "中国武侠风",
        "category": "古风",
        "character": {
            "structured": {"gender": "男", "age_range": "青年", "hair_style": "束发", "hair_color": "黑色", "eye_color": "深邃黑", "personality": "潇洒不羁"},
            "costume": {"name": "侠客服", "style": "武侠", "colors": ["月白", "青"], "accessories": ["长剑", "玉佩", "斗笠"], "description": "宽袍大袖，腰束丝带，背负长剑"},
            "scene": {"environment": "室外", "location": "悬崖之巅", "time_of_day": "黎明", "season": "冬", "weather": "雪", "mood": "孤傲绝伦", "description": "云海翻涌，松柏挂雪，剑气纵横"},
        },
    },
}


class TemplateManager:
    """模板管理。"""

    def __init__(self, custom_templates: dict[str, dict[str, Any]] | None = None) -> None:
        self._templates = {**BUILTIN_TEMPLATES}
        if custom_templates:
            self._templates.update(custom_templates)

    def list_templates(self, category: str | None = None) -> list[dict[str, Any]]:
        items = list(self._templates.values())
        if category:
            items = [t for t in items if t.get("category") == category]
        return [{"id": t["id"], "name": t["name"], "description": t["description"], "category": t.get("category", "")} for t in items]

    def get_template(self, template_id: str) -> dict[str, Any] | None:
        return self._templates.get(template_id)

    def get_categories(self) -> list[str]:
        return sorted(set(t.get("category", "") for t in self._templates.values()))

    def apply_template(self, template_id: str, overrides: dict[str, Any] | None = None) -> CosplayCharacter | None:
        """应用模板，生成一个 CosplayCharacter 实例。"""
        tmpl = self.get_template(template_id)
        if not tmpl:
            return None

        char_data = tmpl.get("character", {})
        structured = StructuredAttributes.from_dict(char_data.get("structured", {}))
        costume = Costume.from_dict(char_data.get("costume", {}))
        scene = SceneDefinition.from_dict(char_data.get("scene", {}))

        # 应用用户覆盖
        if overrides:
            if "name" in overrides:
                pass  # handled below
            if "structured" in overrides:
                for k, v in overrides["structured"].items():
                    if hasattr(structured, k):
                        setattr(structured, k, v)
            if "costume" in overrides:
                for k, v in overrides["costume"].items():
                    if hasattr(costume, k):
                        setattr(costume, k, v)
            if "scene" in overrides:
                for k, v in overrides["scene"].items():
                    if hasattr(scene, k):
                        setattr(scene, k, v)

        char = CosplayCharacter(
            id=_new_id(),
            name=overrides.get("name", "") if overrides else "",
            description=tmpl.get("description", ""),
            structured=structured,
            costume=costume,
            scene=scene,
            template_id=template_id,
        )
        return char
