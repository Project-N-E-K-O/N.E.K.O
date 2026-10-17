"""Contract: every externally exposed ASGI app must mount InstanceAccessMiddleware.

Instance access is enforced at the application layer, not in nginx, because the
same "local bypass / remote requires the key" decision has to cover both Docker
(behind nginx) and desktop/source deployments (no nginx at all).

The price of that choice: a new ASGI app that forgets the middleware is left
completely unprotected -- and there is no symptom at all. It builds, the
container starts, features work. This test turns that class of omission into a
CI failure.

Scope: app/ and plugin/, i.e. the four services behind nginx. local_server/
holds stand-alone side services that sit outside this instance's proxy and do
not share its access gate.

Background: docs/design/security/community-remote-access.md.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MIDDLEWARE = "InstanceAccessMiddleware"
IMPORT = "from utils.instance_access import"

# 必须挂门禁的应用：文件 → 说明
GATED = {
    "app/main_server/__init__.py": "主服务（额外挂社区跨域交接授权器）",
    "app/memory_server/runtime.py": "记忆服务",
    "app/agent_server/api_shared.py": "Agent / 工具服务",
    "plugin/server/http_app.py": "用户插件服务",
}

# 有意不挂的 ASGI 应用：文件 → 理由
EXEMPT = {
    "app/monitor.py": "Monitor 自带 token / 会话认证（#3235），且 Docker 入口不启动它",
}

SCAN_ROOTS = ("app", "plugin")


def _fastapi_sites() -> set[str]:
    """Statically locate every FastAPI(...) site without importing app modules."""
    sites: set[str] = set()
    for root in SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            posix = path.as_posix()
            if "/tests/" in posix or path.name.startswith("test_"):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
                if name == "FastAPI":
                    sites.add(path.relative_to(ROOT).as_posix())
    return sites


def test_scan_actually_finds_the_known_services():
    # 先证明扫描本身没失效：路径改名或扫描逻辑坏掉时，后面的断言会变成空转。
    sites = _fastapi_sites()
    assert sites, "扫描不到任何 FastAPI 实例化点，扫描逻辑或目录结构已失效"
    assert set(GATED) <= sites, f"清单里的服务没被扫到：{sorted(set(GATED) - sites)}"


def test_every_asgi_app_is_accounted_for():
    # 新加服务若既不挂门禁也不登记豁免，这里必须红。
    unknown = _fastapi_sites() - set(GATED) - set(EXEMPT)
    assert not unknown, (
        "以下 ASGI 应用既没有挂 InstanceAccessMiddleware，也没有登记为有意豁免："
        f"{sorted(unknown)}。\n"
        "要么给它挂上门禁，要么把它加进 EXEMPT 并写明理由 —— "
        "门禁在应用层，漏挂就是该服务 HTTP/WS 完全裸奔。"
    )


def test_gated_services_mount_the_instance_access_middleware():
    for rel, label in GATED.items():
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert IMPORT in text, f"{rel}（{label}）没有导入实例门禁中间件"
        assert f"add_middleware({MIDDLEWARE}" in text, (
            f"{rel}（{label}）导入了中间件却没有 add_middleware —— "
            "该服务的 HTTP/WS 不受实例门禁保护"
        )


def test_exempt_apps_are_still_present():
    # 文件被移动/删除时豁免清单要跟着更新，否则会悄悄覆盖新文件。
    for rel, reason in EXEMPT.items():
        assert (ROOT / rel).exists(), f"豁免清单里的 {rel} 不存在了（理由：{reason}）"
