"""Cosplay 三要素数据模型。

角色 + 服装 + 场景 的组合定义，是 cosplay 插件的核心概念。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any
import uuid
from datetime import datetime


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


# ── 结构化属性 ──────────────────────────────────────────────

@dataclass
class StructuredAttributes:
    """角色的结构化基础属性，用于表单输入和 prompt 组装。"""
    gender: str = ""            # 性别
    hair_style: str = ""        # 发型
    hair_color: str = ""        # 发色
    eye_color: str = ""         # 瞳色
    body_type: str = ""         # 体型
    age_range: str = ""         # 年龄段（少年/青年/中年...）
    personality: str = ""       # 性格关键词
    skin_tone: str = ""         # 肤色
    height: str = ""            # 身高特征

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StructuredAttributes:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_prompt_text(self) -> str:
        """组装为自然语言描述，用于生图 prompt。"""
        parts = []
        if self.gender:
            parts.append(self.gender)
        if self.age_range:
            parts.append(self.age_range)
        if self.body_type:
            parts.append(self.body_type)
        if self.height:
            parts.append(self.height)
        if self.skin_tone:
            parts.append(f"{self.skin_tone}肤色")
        if self.hair_style and self.hair_color:
            parts.append(f"{self.hair_color}{self.hair_style}发型")
        elif self.hair_style:
            parts.append(f"{self.hair_style}发型")
        elif self.hair_color:
            parts.append(f"{self.hair_color}头发")
        if self.eye_color:
            parts.append(f"{self.eye_color}瞳孔")
        if self.personality:
            parts.append(f"性格{self.personality}")
        return "，".join(parts)


# ── 服装 ────────────────────────────────────────────────────

@dataclass
class Costume:
    """服装定义。"""
    name: str = ""                      # 服装名称（如"红色和服"）
    style: str = ""                     # 风格（和风/洋装/现代/奇幻/古风...）
    description: str = ""               # 自由文字描述
    colors: list[str] = field(default_factory=list)       # 主色调
    accessories: list[str] = field(default_factory=list)   # 配饰
    reference_images: list[str] = field(default_factory=list)  # 参考图路径

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Costume:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_prompt_text(self) -> str:
        parts = []
        if self.name:
            parts.append(self.name)
        if self.style:
            parts.append(f"{self.style}风格")
        if self.colors:
            parts.append("、".join(self.colors) + "配色")
        if self.accessories:
            parts.append("佩戴" + "、".join(self.accessories))
        if self.description:
            parts.append(self.description)
        return "，".join(parts) if parts else ""


# ── 场景 ────────────────────────────────────────────────────

@dataclass
class SceneDefinition:
    """场景环境定义。"""
    name: str = ""                      # 场景名（如"樱花神社"）
    environment: str = ""               # 环境类型（室内/室外/幻想/水下...）
    location: str = ""                  # 具体地点
    time_of_day: str = ""               # 时间（黎明/白天/黄昏/夜晚/深夜）
    weather: str = ""                   # 天气（晴/阴/雨/雪/雾...）
    season: str = ""                    # 季节
    mood: str = ""                      # 氛围（温馨/紧张/浪漫/诡异...）
    description: str = ""               # 自由描述
    reference_images: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SceneDefinition:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    def to_prompt_text(self) -> str:
        parts = []
        if self.environment:
            parts.append(self.environment)
        if self.location:
            parts.append(self.location)
        if self.time_of_day:
            parts.append(self.time_of_day)
        if self.season:
            parts.append(self.season)
        if self.weather:
            parts.append(self.weather + "天")
        if self.mood:
            parts.append(f"{self.mood}氛围")
        if self.name:
            parts.append(self.name)
        if self.description:
            parts.append(self.description)
        return "，".join(parts) if parts else ""


# ── 角色 ────────────────────────────────────────────────────

@dataclass
class CosplayCharacter:
    """完整的 cosplay 角色定义（三合一）。"""
    id: str = field(default_factory=_new_id)
    name: str = ""                              # 角色名
    description: str = ""                       # 自由文字描述
    structured: StructuredAttributes = field(default_factory=StructuredAttributes)
    costume: Costume = field(default_factory=Costume)
    scene: SceneDefinition = field(default_factory=SceneDefinition)
    reference_images: list[str] = field(default_factory=list)  # 角色整体参考图
    template_id: str | None = None              # 来源模板 ID
    tags: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["structured"] = self.structured.to_dict()
        d["costume"] = self.costume.to_dict()
        d["scene"] = self.scene.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CosplayCharacter:
        structured = StructuredAttributes.from_dict(d.pop("structured", {}))
        costume = Costume.from_dict(d.pop("costume", {}))
        scene = SceneDefinition.from_dict(d.pop("scene", {}))
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(structured=structured, costume=costume, scene=scene, **known)

    def to_full_prompt_text(self) -> str:
        """组装完整的角色 prompt 文本。"""
        parts = []
        if self.name:
            parts.append(f"角色：{self.name}")
        structured_text = self.structured.to_prompt_text()
        if structured_text:
            parts.append(structured_text)
        costume_text = self.costume.to_prompt_text()
        if costume_text:
            parts.append(f"服装：{costume_text}")
        scene_text = self.scene.to_prompt_text()
        if scene_text:
            parts.append(f"场景：{scene_text}")
        if self.description:
            parts.append(self.description)
        return "，".join(parts)


# ── 作品 ────────────────────────────────────────────────────

@dataclass
class CosplayWork:
    """一个 cosplay 作品的元信息。"""
    id: str = field(default_factory=_new_id)
    title: str = ""                             # 作品标题
    mode: str = "theater"                       # theater / interactive
    character_ids: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    favorited: bool = False
    pinned: bool = False
    cover_image: str = ""                       # 封面图路径
    description: str = ""                       # 作品描述
    scene_count: int = 0                        # 场景/幕数
    image_count: int = 0                        # 生成图片数
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> CosplayWork:
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


# ── 剧本节点 ────────────────────────────────────────────────

@dataclass
class Action:
    """动作描述。"""
    description: str = ""
    character: str = ""         # 执行动作的角色名

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Action:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Dialogue:
    """对话条目。"""
    character: str = ""         # 说话角色
    text: str = ""              # 台词内容
    is_narration: bool = False  # 是否旁白
    is_inner: bool = False      # 是否内心独白

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Dialogue:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class StoryboardNode:
    """一个分镜节点——剧本的最小演出单位。"""
    scene_id: int = 0
    title: str = ""                         # 幕标题
    camera: str = ""                        # 镜头指令（全景/特写/远景/仰拍/俯拍）
    scene_desc: str = ""                    # 场景描述
    actions: list[Action] = field(default_factory=list)
    dialogues: list[Dialogue] = field(default_factory=list)
    characters: list[str] = field(default_factory=list)     # 出场角色名
    character_configs: dict[str, CosplayCharacter] = field(default_factory=dict)  # 角色配置快照
    image_prompt: str = ""                  # 组装后的生图 prompt
    image_url: str = ""                     # 生成的图片 URL
    image_path: str = ""                    # 本地图片路径
    mood: str = ""                          # 氛围
    is_climax: bool = False                 # 是否高潮节点
    bgm: str = ""                           # 背景音乐提示

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["actions"] = [a.to_dict() for a in self.actions]
        d["dialogues"] = [dl.to_dict() for dl in self.dialogues]
        d["character_configs"] = {k: v.to_dict() for k, v in self.character_configs.items()}
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> StoryboardNode:
        actions = [Action.from_dict(a) for a in d.pop("actions", [])]
        dialogues = [Dialogue.from_dict(dl) for dl in d.pop("dialogues", [])]
        char_configs = {k: CosplayCharacter.from_dict(v) for k, v in d.pop("character_configs", {}).items()}
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(actions=actions, dialogues=dialogues, character_configs=char_configs, **known)
