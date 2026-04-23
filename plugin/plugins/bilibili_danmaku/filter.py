"""
Bilibili 弹幕过滤器模块

包含：
- 敏感词过滤（政治/暴力色情/违法违规/低俗内容）
- 用户等级过滤（登录用户专属高级过滤功能）
- 礼物价值过滤

词库加载：从 data/Vocabulary/ 目录动态加载词库文件
"""

from __future__ import annotations
import re
import os
from pathlib import Path
from typing import Dict, Any, Optional, List, Set


# ==========================================
# 词库加载器
# ==========================================

def _load_vocabulary_files(vocab_dir: Path) -> tuple[Set[str], Set[str], List[re.Pattern]]:
    """
    从 Vocabulary 目录加载所有词库文件
    
    Returns:
        (vulgar_words, spam_words, regex_patterns)
    """
    vulgar_words: Set[str] = set()
    spam_words: Set[str] = set()
    regex_patterns: List[re.Pattern] = []
    
    if not vocab_dir.exists():
        return vulgar_words, spam_words, regex_patterns
    
    # 遍历所有 .txt 文件
    for vocab_file in vocab_dir.glob("*.txt"):
        try:
            with open(vocab_file, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # 跳过空行和注释行
                    if not line or line.startswith('#'):
                        continue
                    
                    # 根据文件名分类
                    filename_lower = vocab_file.stem.lower()
                    
                    if any(kw in filename_lower for kw in ['广告', 'spam']):
                        spam_words.add(line)
                    elif any(kw in filename_lower for kw in ['色情', '暴恐', '暴力', '反动', '政治', '涉枪', '贪腐', '民生', '其他', 'gfw', 'covid']):
                        # 敏感词库 - 使用正则精确匹配
                        vulgar_words.add(line)
                    else:
                        # 其他词库 - 默认加入敏感词
                        vulgar_words.add(line)
                        
        except Exception as e:
            print(f"[Filter] 加载词库失败 {vocab_file.name}: {e}")
    
    return vulgar_words, spam_words, regex_patterns


def _build_compiled_filters(vocab_dir: Path) -> tuple[re.Pattern, re.Pattern]:
    """
    构建编译后的过滤正则
    
    Returns:
        (vulgar_pattern, spam_pattern)
    """
    vulgar_words, spam_words, _ = _load_vocabulary_files(vocab_dir)
    
    # 编译粗口/敏感词正则
    if vulgar_words:
        vulgar_pattern = re.compile(
            '|'.join(re.escape(w) for w in vulgar_words),
            re.IGNORECASE
        )
    else:
        # 无词库时使用空正则
        vulgar_pattern = re.compile(r'(?!>)')
    
    # 编译广告词正则
    if spam_words:
        spam_pattern = re.compile(
            '|'.join(re.escape(w) for w in spam_words),
            re.IGNORECASE
        )
    else:
        spam_pattern = re.compile(r'(?!>)')
    
    return vulgar_pattern, spam_pattern


# ==========================================
# 初始化过滤器
# ==========================================

def _get_plugin_root() -> Path:
    """获取插件根目录"""
    return Path(__file__).parent

def _get_vocab_dir() -> Path:
    """获取词库目录"""
    return _get_plugin_root() / "data" / "Vocabulary"

# 全局编译后的过滤器
_VULGAR_PATTERN: Optional[re.Pattern] = None
_SPAM_PATTERN: Optional[re.Pattern] = None


def _ensure_filters_loaded():
    """确保过滤器已加载"""
    global _VULGAR_PATTERN, _SPAM_PATTERN
    if _VULGAR_PATTERN is None:
        vocab_dir = _get_vocab_dir()
        _VULGAR_PATTERN, _SPAM_PATTERN = _build_compiled_filters(vocab_dir)
        print(f"[Filter] 词库加载完成")


def reload_filters():
    """重新加载词库"""
    global _VULGAR_PATTERN, _SPAM_PATTERN
    _VULGAR_PATTERN = None
    _ensure_filters_loaded()


def is_sensitive(text: str) -> bool:
    """检查文本是否含有敏感词"""
    if not text:
        return False
    _ensure_filters_loaded()
    text_lower = text.lower()
    # 检查敏感词
    if _VULGAR_PATTERN and _VULGAR_PATTERN.search(text_lower):
        return True
    return False


def is_spam(text: str) -> bool:
    """检查文本是否为广告/垃圾信息"""
    if not text:
        return False
    _ensure_filters_loaded()
    text_lower = text.lower()
    # 检查广告词
    if _SPAM_PATTERN and _SPAM_PATTERN.search(text_lower):
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

        # 2. 广告过滤（所有用户）
        if is_spam(content):
            return False, "spam"

        # 3. 等级过滤（仅登录用户且开启时）
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

        # 广告过滤
        if is_spam(content):
            return False, "spam"

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
