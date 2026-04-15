"""
Bilibili 弹幕过滤器模块

包含：
- 敏感词过滤（政治/暴力色情/违法违规/低俗内容）
- 用户等级过滤（登录用户专属高级过滤功能）
- 礼物价值过滤
"""

from __future__ import annotations
import re
from typing import Dict, Any, Optional


# ==========================================
# 敏感词列表（基础版）
# ==========================================
# 注意：此处仅为示例关键词类别，不穷举具体词汇
# 实际过滤使用模式匹配

# 1. 政治敏感类 - 使用模糊正则匹配
_POLITICAL_PATTERNS = [
    r'颠覆.*政权',
    r'推翻.*制度',
    r'邪教',
    r'极端主义',
    r'分裂.*国家',
]

# 2. 暴力色情类
_VIOLENCE_PATTERNS = [
    r'色情|淫秽|裸露|性器',
    r'自杀|自残|杀人',
    r'毒品|冰毒|海洛因|大麻',
    r'爆炸|炸弹|制造武器',
]

# 3. 违法违规类
_ILLEGAL_PATTERNS = [
    r'赌博|赌场|洗钱',
    r'诈骗|欺诈',
    r'传销',
]

# 4. 低俗类 - 粗口词汇（使用拼音首字母等变体匹配）
_VULGAR_WORDS = [
    '操你', '草你', '妈的', '他妈', '尼玛', '傻逼', '煞笔', 'sb',
    '滚', '去死', '废物', '脑残', '智障', '白痴', '混蛋',
    '婊子', '妓女', '鸡巴', '屌', '逼',
    # 歧视性词汇
    '歧视', '侮辱残疾', '嘲笑老人',
]

# 5. 广告/垃圾信息
_SPAM_PATTERNS = [
    r'加.*v.*看',
    r'私信.*链接',
    r'qq群.*\d{5,}',
    r'微信.*\d{4,}',
    r'关注.*涨粉',
]

# 组合所有模式
_ALL_PATTERNS = (
    _POLITICAL_PATTERNS
    + _VIOLENCE_PATTERNS
    + _ILLEGAL_PATTERNS
    + _SPAM_PATTERNS
)

# 预编译正则
_COMPILED_PATTERNS = [re.compile(p, re.IGNORECASE) for p in _ALL_PATTERNS]
_COMPILED_VULGAR = re.compile(
    '|'.join(re.escape(w) for w in _VULGAR_WORDS),
    re.IGNORECASE
)


def is_sensitive(text: str) -> bool:
    """检查文本是否含有敏感词"""
    if not text:
        return False
    text_lower = text.lower()
    # 检查粗口词汇
    if _COMPILED_VULGAR.search(text_lower):
        return True
    # 检查正则模式
    for pattern in _COMPILED_PATTERNS:
        if pattern.search(text_lower):
            return True
    return False


# ==========================================
# B站用户等级定义
# ==========================================
def get_level_tier(level: int) -> str:
    """根据用户等级返回等级段"""
    if level < 10:
        return "new"       # 新用户
    elif level < 20:
        return "basic"     # 基础用户（10-19）
    elif level < 30:
        return "regular"   # 普通用户（20-29）
    elif level < 40:
        return "veteran"   # 老用户（30-39）
    elif level < 50:
        return "elite"     # 精英用户（40-49，绿色弹幕）
    else:
        return "master"    # 大佬（50+）


def get_level_weekly_bonus(level: int) -> int:
    """获取用户等级对应的周常辣条数"""
    if level < 10:
        return 0
    elif level < 15:
        return 10
    elif level < 20:
        return 20
    elif level < 25:
        return 30
    elif level < 30:
        return 50
    elif level < 35:
        return 75
    elif level < 40:
        return 100
    elif level < 45:
        return 150
    elif level < 50:
        return 200
    else:
        return 300


# ==========================================
# 过滤器核心类
# ==========================================
class DanmakuFilter:
    """
    弹幕过滤器
    
    基础模式（游客）：仅过滤敏感词
    高级模式（已登录）：额外支持等级过滤和礼物价值过滤
    """

    def __init__(self, config: dict):
        """
        config 结构：
        {
            "is_logged_in": bool,
            "filter": {
                "min_user_level": int,   # 最低用户等级 (登录用户专属)
                "min_gift_value": float, # 最低礼物价值(元)，0表示不过滤
                "filter_level_enabled": bool,
                "filter_gift_enabled": bool
            }
        }
        """
        self.is_logged_in: bool = config.get("is_logged_in", False)
        filter_cfg = config.get("filter", {})
        self.min_user_level: int = filter_cfg.get("min_user_level", 0)
        self.min_gift_value: float = filter_cfg.get("min_gift_value", 0.0)
        self.filter_level_enabled: bool = filter_cfg.get("filter_level_enabled", False)
        self.filter_gift_enabled: bool = filter_cfg.get("filter_gift_enabled", False)

    def check_danmaku(self, data: Dict[str, Any]) -> tuple[bool, str]:
        """
        检查弹幕是否通过过滤
        返回: (是否通过, 拒绝原因)
        """
        content = data.get("content", "")
        user_level = data.get("user_level", 0)

        # 1. 敏感词过滤（所有用户）
        if is_sensitive(content):
            return False, "sensitive"

        # 2. 等级过滤（仅登录用户且开启时）
        if self.is_logged_in and self.filter_level_enabled:
            if user_level < self.min_user_level:
                return False, f"level_too_low({user_level}<{self.min_user_level})"

        return True, ""

    def check_gift(self, data: Dict[str, Any]) -> tuple[bool, str]:
        """
        检查礼物是否通过过滤
        返回: (是否通过, 拒绝原因)
        """
        # 礼物价值过滤（仅登录用户且开启时）
        if self.is_logged_in and self.filter_gift_enabled:
            gift_value = data.get("total_coin", 0)  # 总金瓜子数
            rmb_value = gift_value / 1000.0  # 金瓜子换算 RMB
            if rmb_value < self.min_gift_value:
                return False, f"gift_value_too_low({rmb_value:.2f}<{self.min_gift_value})"

        return True, ""

    def check_sc(self, data: Dict[str, Any]) -> tuple[bool, str]:
        """
        检查 SuperChat 是否通过过滤（SC 价格是人民币）
        """
        content = data.get("message", "")

        # 敏感词过滤
        if is_sensitive(content):
            return False, "sensitive"

        # SC 价值过滤
        if self.is_logged_in and self.filter_gift_enabled:
            price = data.get("price", 0)
            if price < self.min_gift_value:
                return False, f"sc_value_too_low({price}<{self.min_gift_value})"

        return True, ""

    def describe_mode(self) -> str:
        """描述当前过滤模式"""
        if not self.is_logged_in:
            return "游客模式（仅敏感词过滤）"
        parts = ["已登录模式（敏感词过滤"]
        if self.filter_level_enabled:
            parts.append(f"等级≥{self.min_user_level}")
        if self.filter_gift_enabled:
            parts.append(f"礼物≥{self.min_gift_value}元")
        return "、".join(parts) + "）"
