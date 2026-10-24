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


def _module_facts(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Return (fastapi_app_bindings, gated_bindings) parsed from one module's AST.

    Both dicts map a variable name to a count. Counting (rather than using sets)
    is what makes a rebinding visible: 'app = FastAPI(); app.add_middleware(...);
    app = FastAPI()' binds twice but gates once, so the second application would
    be unprotected while a set-based check still matches on the name 'app'.

    Everything is AST-driven, not text-driven, for three reasons:

    * FastAPI may be imported under an alias ('from fastapi import FastAPI as
      API') or reached through the module object ('import fastapi' then
      'fastapi.FastAPI()'), so the constructor name is resolved from the import
      statements instead of being hard-coded.
    * A middleware mount that only exists inside a comment is not in the AST, so
      a commented-out mount cannot satisfy the contract.
    * The mount must target a variable that this same module created from the
      FastAPI constructor, not some unrelated object.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    ctor_names: set[str] = set()
    fastapi_modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] == "fastapi":
                for alias in node.names:
                    if alias.name == "FastAPI":
                        ctor_names.add(alias.asname or "FastAPI")
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "fastapi":
                    fastapi_modules.add(alias.asname or "fastapi")

    def is_ctor(call: ast.Call) -> bool:
        func = call.func
        if isinstance(func, ast.Name):
            return func.id in ctor_names
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "FastAPI"
            and isinstance(func.value, ast.Name)
            and func.value.id in fastapi_modules
        )

    bindings: dict[str, int] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and is_ctor(node.value)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = bindings.get(target.id, 0) + 1

    def is_middleware_ref(expr: ast.expr) -> bool:
        if isinstance(expr, ast.Name):
            return expr.id == MIDDLEWARE
        return isinstance(expr, ast.Attribute) and expr.attr == MIDDLEWARE

    gated: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "add_middleware"):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        if node.args and is_middleware_ref(node.args[0]):
            name = func.value.id
            gated[name] = gated.get(name, 0) + 1

    return bindings, gated


def _scan() -> dict[str, tuple[dict[str, int], dict[str, int]]]:
    """Return {relative path: (bindings, gated)} for every FastAPI module."""
    facts: dict[str, tuple[dict[str, int], dict[str, int]]] = {}
    for root in SCAN_ROOTS:
        for path in sorted((ROOT / root).rglob("*.py")):
            posix = path.as_posix()
            if "/tests/" in posix or path.name.startswith("test_"):
                continue
            try:
                bindings, gated = _module_facts(path)
            except (OSError, SyntaxError):
                continue
            if bindings:
                facts[path.relative_to(ROOT).as_posix()] = (bindings, gated)
    return facts


def _fastapi_sites() -> set[str]:
    """Files that create at least one FastAPI application."""
    return set(_scan())


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
    facts = _scan()
    for rel, label in GATED.items():
        assert rel in facts, f"{rel}（{label}）没有被识别出 FastAPI 应用"
        bindings, gated = facts[rel]
        # 必须是"这个文件自己创建的**每一个** app 实例上都调用了
        # add_middleware(InstanceAccessMiddleware)"。
        # 比的是次数而不是集合：app 被重新赋值成新的 FastAPI() 时，集合判定仍会
        # 因为名字相同而通过，但最后那个实例其实没挂门禁。
        # 注释掉的代码不在 AST 里，给别的对象挂中间件也不算。
        ungated = {
            name: count
            for name, count in bindings.items()
            if gated.get(name, 0) < count
        }
        assert not ungated, (
            f"{rel}（{label}）没有在它自己创建的 FastAPI 应用上挂 InstanceAccessMiddleware —— "
            "该服务的 HTTP/WS 不受实例门禁保护"
        )


def test_exempt_apps_are_still_present():
    # 文件被移动/删除时豁免清单要跟着更新，否则会悄悄覆盖新文件。
    for rel, reason in EXEMPT.items():
        assert (ROOT / rel).exists(), f"豁免清单里的 {rel} 不存在了（理由：{reason}）"
