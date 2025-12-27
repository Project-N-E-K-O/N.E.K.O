from __future__ import annotations

import platform
import sys
from dataclasses import dataclass
from typing import Any, Dict


@dataclass
class SystemInfo:
    ctx: Any

    def get_system_config(self, *, timeout: float = 5.0) -> Dict[str, Any]:
        if not hasattr(self.ctx, "get_system_config"):
            raise RuntimeError("ctx.get_system_config is not available")
        result = self.ctx.get_system_config(timeout=timeout)
        if not isinstance(result, dict):
            return {"result": result}
        return result

    def get_python_env(self) -> Dict[str, Any]:
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
