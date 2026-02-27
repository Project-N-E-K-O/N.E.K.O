from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Dict

if TYPE_CHECKING:
    from .types import PluginContextProtocol


@dataclass
class SystemInfo:
    """系统信息查询器
    
    提供系统配置和环境信息的查询功能。通过self.ctx.bus.system_info或直接实例化使用。
    
    Attributes:
        ctx: 插件上下文
    """
    ctx: "PluginContextProtocol"

    async def get_system_config(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """获取系统配置
        
        查询插件服务器的系统配置信息。
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            系统配置字典
        
        Raises:
            RuntimeError: 如果ctx.get_system_config不可用
            TimeoutError: 如果查询超时
        
        Example:
            >>> system_info = SystemInfo(ctx)
            >>> config = await system_info.get_system_config()
        """
        if not hasattr(self.ctx, "get_system_config"):
            raise RuntimeError("ctx.get_system_config is not available")
        result = await self.ctx.get_system_config(timeout=timeout)
        if not isinstance(result, dict):
            return {"result": result}
        return result

    async def get_server_settings(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        """获取插件服务器设置
        
        这是get_system_config()的便捷封装,直接返回服务器配置字典。
        从IPC响应中提取config字段。
        
        Args:
            timeout: 超时时间(秒)
        
        Returns:
            服务器设置字典
        
        Raises:
            RuntimeError: 如果ctx.get_system_config不可用
            TimeoutError: 如果查询超时
        
        Example:
            >>> settings = await system_info.get_server_settings()
            >>> print(settings.get("plugin_dir"))
        """

        result = await self.get_system_config(timeout=timeout)
        if "data" in result and isinstance(result.get("data"), dict):
            result = result["data"]
        cfg = result.get("config") if isinstance(result, dict) else None
        return cfg if isinstance(cfg, dict) else {}

    def get_python_env(self) -> Dict[str, Any]:
        """获取Python环境信息
        
        收集当前Python运行环境的详细信息,包括版本、平台、架构等。
        
        Returns:
            包含Python和操作系统信息的字典,格式为:
            {
                "python": {
                    "version": "3.11.0",
                    "version_info": {"major": 3, "minor": 11, ...},
                    "implementation": "CPython",
                    "executable": "/usr/bin/python3",
                    ...
                },
                "os": {
                    "platform": "linux",
                    "system": "Linux",
                    "release": "5.15.0",
                    "machine": "x86_64",
                    ...
                }
            }
        
        Example:
            >>> env = system_info.get_python_env()
            >>> print(f"Python {env['python']['version']}")
            >>> print(f"OS: {env['os']['system']}")
        """
        impl = platform.python_implementation()

        try:
            uname = platform.uname()
        except Exception:
            uname = None

        try:
            plat_str = platform.platform()
        except Exception:
            plat_str = None

        try:
            arch = platform.architecture()
        except Exception:
            arch = None

        win32_ver = None
        try:
            win32_ver = platform.win32_ver()
        except Exception:
            win32_ver = None

        mac_ver = None
        try:
            mac_ver = platform.mac_ver()
        except Exception:
            mac_ver = None

        libc_ver = None
        try:
            libc_ver = platform.libc_ver()
        except Exception:
            libc_ver = None

        return {
            "python": {
                "version": sys.version,
                "version_info": {
                    "major": sys.version_info.major,
                    "minor": sys.version_info.minor,
                    "micro": sys.version_info.micro,
                    "releaselevel": sys.version_info.releaselevel,
                    "serial": sys.version_info.serial,
                },
                "implementation": impl,
                "executable": sys.executable,
                "prefix": sys.prefix,
                "base_prefix": getattr(sys, "base_prefix", None),
                "platform": {
                    "python_build": platform.python_build(),
                    "python_compiler": platform.python_compiler(),
                },
            },
            "os": {
                "platform": sys.platform,
                "platform_str": plat_str,
                "system": getattr(uname, "system", None),
                "release": getattr(uname, "release", None),
                "version": getattr(uname, "version", None),
                "machine": getattr(uname, "machine", None),
                "processor": getattr(uname, "processor", None),
                "architecture": {
                    "bits": arch[0] if isinstance(arch, (tuple, list)) and len(arch) > 0 else None,
                    "linkage": arch[1] if isinstance(arch, (tuple, list)) and len(arch) > 1 else None,
                },
                "details": {
                    "win32_ver": {
                        "release": win32_ver[0] if isinstance(win32_ver, (tuple, list)) and len(win32_ver) > 0 else None,
                        "version": win32_ver[1] if isinstance(win32_ver, (tuple, list)) and len(win32_ver) > 1 else None,
                        "csd": win32_ver[2] if isinstance(win32_ver, (tuple, list)) and len(win32_ver) > 2 else None,
                        "ptype": win32_ver[3] if isinstance(win32_ver, (tuple, list)) and len(win32_ver) > 3 else None,
                    },
                    "mac_ver": {
                        "release": mac_ver[0] if isinstance(mac_ver, (tuple, list)) and len(mac_ver) > 0 else None,
                        "versioninfo": mac_ver[1] if isinstance(mac_ver, (tuple, list)) and len(mac_ver) > 1 else None,
                        "machine": mac_ver[2] if isinstance(mac_ver, (tuple, list)) and len(mac_ver) > 2 else None,
                    },
                    "libc_ver": {
                        "lib": libc_ver[0] if isinstance(libc_ver, (tuple, list)) and len(libc_ver) > 0 else None,
                        "version": libc_ver[1] if isinstance(libc_ver, (tuple, list)) and len(libc_ver) > 1 else None,
                    },
                },
            },
        }
