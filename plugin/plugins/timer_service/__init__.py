"""
定时器服务插件

提供定时器管理功能，其他插件可以通过调用此插件来实现定时任务。
使用自定义事件实现定时触发。
"""
from .main import TimerServicePlugin

__all__ = ["TimerServicePlugin"]

