"""属性和操作的中文翻译工具

提供设备规格的中文翻译功能，支持：
1. 从内置 JSON 文件加载默认翻译
2. 从外部 JSON 文件加载自定义翻译
3. 通过代码传入自定义翻译字典
"""

import json
from pathlib import Path
from typing import Dict, Optional


class TranslationManager:
    """翻译管理器
    
    负责加载和管理属性、操作、类型和访问权限的中文翻译。
    支持多种翻译来源的合并，优先级：自定义字典 > 外部文件 > 内置文件
    """
    
    def __init__(
        self,
        custom_translations: Optional[Dict[str, Dict[str, str]]] = None,
        custom_file: Optional[Path] = None,
    ):
        """初始化翻译管理器
        
        Args:
            custom_translations: 自定义翻译字典，格式：
                {
                    "properties": {"English Name": "中文名称", ...},
                    "actions": {"English Name": "中文名称", ...},
                    "types": {"type_name": "类型名称", ...},
                    "access": {"access_name": "权限名称", ...}
                }
            custom_file: 自定义翻译文件路径（JSON格式）
        """
        # 加载内置翻译
        self._translations = self._load_builtin_translations()
        
        # 加载外部文件翻译（如果提供）
        if custom_file:
            self._merge_translations(self._load_from_file(custom_file))
        
        # 合并自定义翻译（如果提供）
        if custom_translations:
            self._merge_translations(custom_translations)
    
    def _load_builtin_translations(self) -> Dict[str, Dict[str, str]]:
        """加载内置翻译文件
        
        Returns:
            翻译字典
        """
        builtin_file = Path(__file__).parent / "translations.json"
        return self._load_from_file(builtin_file)
    
    def _load_from_file(self, file_path: Path) -> Dict[str, Dict[str, str]]:
        """从文件加载翻译
        
        Args:
            file_path: JSON文件路径
            
        Returns:
            翻译字典
            
        Raises:
            FileNotFoundError: 文件不存在
            json.JSONDecodeError: JSON格式错误
        """
        if not file_path.exists():
            raise FileNotFoundError(f"翻译文件不存在: {file_path}")
        
        with open(file_path, "r", encoding="utf-8") as f:
            data: Dict[str, Dict[str, str]] = json.load(f)
            return data
    
    def _merge_translations(self, new_translations: Dict[str, Dict[str, str]]) -> None:
        """合并新的翻译到现有翻译中
        
        Args:
            new_translations: 新的翻译字典
        """
        for category in ["properties", "actions", "types", "access"]:
            if category in new_translations:
                if category not in self._translations:
                    self._translations[category] = {}
                self._translations[category].update(new_translations[category])
    
    def get_property_translation(self, english_name: str) -> str:
        """获取属性的中文翻译
        
        Args:
            english_name: 英文属性名
            
        Returns:
            中文翻译，如果没有翻译则返回原英文名
        """
        return self._translations.get("properties", {}).get(english_name, english_name)
    
    def get_action_translation(self, english_name: str) -> str:
        """获取操作的中文翻译
        
        Args:
            english_name: 英文操作名
            
        Returns:
            中文翻译，如果没有翻译则返回原英文名
        """
        return self._translations.get("actions", {}).get(english_name, english_name)
    
    def get_type_translation(self, type_name: str) -> str:
        """获取类型的中文翻译
        
        Args:
            type_name: 类型名称
            
        Returns:
            中文翻译，如果没有翻译则返回原类型名
        """
        return self._translations.get("types", {}).get(type_name, type_name)
    
    def get_access_translation(self, access_name: str) -> str:
        """获取访问权限的中文翻译
        
        Args:
            access_name: 访问权限名称
            
        Returns:
            中文翻译，如果没有翻译则返回原权限名
        """
        return self._translations.get("access", {}).get(access_name, access_name)
    
    def add_property_translation(self, english_name: str, chinese_name: str) -> None:
        """添加属性翻译
        
        Args:
            english_name: 英文属性名
            chinese_name: 中文翻译
        """
        if "properties" not in self._translations:
            self._translations["properties"] = {}
        self._translations["properties"][english_name] = chinese_name
    
    def add_action_translation(self, english_name: str, chinese_name: str) -> None:
        """添加操作翻译
        
        Args:
            english_name: 英文操作名
            chinese_name: 中文翻译
        """
        if "actions" not in self._translations:
            self._translations["actions"] = {}
        self._translations["actions"][english_name] = chinese_name
    
    def export_to_file(self, file_path: Path) -> None:
        """导出翻译到文件
        
        Args:
            file_path: 目标文件路径
        """
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(self._translations, f, ensure_ascii=False, indent=2)


# 创建默认的全局翻译管理器实例
_default_manager = TranslationManager()


# 提供便捷的全局函数（使用默认管理器）
def get_property_translation(english_name: str) -> str:
    """获取属性的中文翻译（使用默认翻译管理器）
    
    Args:
        english_name: 英文属性名
        
    Returns:
        中文翻译，如果没有翻译则返回原英文名
    """
    return _default_manager.get_property_translation(english_name)


def get_action_translation(english_name: str) -> str:
    """获取操作的中文翻译（使用默认翻译管理器）
    
    Args:
        english_name: 英文操作名
        
    Returns:
        中文翻译，如果没有翻译则返回原英文名
    """
    return _default_manager.get_action_translation(english_name)


def get_type_translation(type_name: str) -> str:
    """获取类型的中文翻译（使用默认翻译管理器）
    
    Args:
        type_name: 类型名称
        
    Returns:
        中文翻译，如果没有翻译则返回原类型名
    """
    return _default_manager.get_type_translation(type_name)


def get_access_translation(access_name: str) -> str:
    """获取访问权限的中文翻译（使用默认翻译管理器）
    
    Args:
        access_name: 访问权限名称
        
    Returns:
        中文翻译，如果没有翻译则返回原权限名
    """
    return _default_manager.get_access_translation(access_name)


