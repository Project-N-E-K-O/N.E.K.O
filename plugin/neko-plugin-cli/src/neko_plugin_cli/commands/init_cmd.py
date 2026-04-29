"""neko-plugin init — interactive plugin scaffolding.

Flow:
  Page 1: plugin_id → name → type → quick start?
  If quick start: generate hello-world template and exit.
  Page 2: description → author → features → pyproject
  Generate files.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from ..paths import CliDefaults
from ..templates.generator import PluginSpec, generate_plugin
from ._prompt import ask_checkbox, ask_confirm, ask_select, ask_text

_PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def register(subparsers: argparse._SubParsersAction, *, defaults: CliDefaults) -> None:
    parser = subparsers.add_parser("init", help="Create a new plugin from template")
    parser.add_argument("plugin_id", nargs="?", help="Plugin ID (optional, will prompt if omitted)")
    parser.add_argument("--type", dest="plugin_type", choices=("plugin", "extension", "adapter"), help="Plugin type")
    parser.add_argument("--name", help="Display name")
    parser.add_argument("--no-interactive", action="store_true", help="Skip interactive prompts")
    parser.set_defaults(handler=handle, _defaults=defaults)


def handle(args: argparse.Namespace) -> int:
    defaults: CliDefaults = args._defaults

    if args.no_interactive:
        return _handle_non_interactive(args, defaults=defaults)

    return _handle_interactive(args, defaults=defaults)


# ---------------------------------------------------------------------------
# Interactive flow
# ---------------------------------------------------------------------------

def _handle_interactive(args: argparse.Namespace, *, defaults: CliDefaults) -> int:
    # ── Page 1: Basic info ──

    # Plugin ID
    plugin_id = args.plugin_id
    if not plugin_id:
        plugin_id = ask_text(
            "插件 ID (Plugin ID)",
            validate=_validate_plugin_id,
        )
    if not plugin_id:
        return _cancelled()
    plugin_id = plugin_id.strip()
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        print(f"[FAIL] invalid plugin ID: '{plugin_id}' (only A-Z, a-z, 0-9, _, -)", file=sys.stderr)
        return 1

    # Check if directory already exists
    target_dir = defaults.plugins_root / plugin_id
    if target_dir.exists():
        print(f"[FAIL] directory already exists: {target_dir}", file=sys.stderr)
        return 1

    # Display name
    name = args.name
    if not name:
        name = ask_text("显示名称 (Display Name)", default=plugin_id)
    if name is None:
        return _cancelled()

    # Plugin type
    plugin_type = args.plugin_type
    if not plugin_type:
        plugin_type = ask_select(
            "插件类型 (Plugin Type)",
            choices=[
                {"value": "plugin", "name": "Plugin — 独立功能插件"},
                {"value": "extension", "name": "Extension — 为现有插件添加路由/钩子"},
                {"value": "adapter", "name": "Adapter — 对接外部协议 (MCP 等)"},
            ],
            default="plugin",
        )
    if not plugin_type:
        return _cancelled()

    # Quick start?
    quick_start = ask_confirm("快速开始? (生成 Hello World 模板，跳过高级配置)", default=True)
    if quick_start is None:
        return _cancelled()

    if quick_start:
        spec = PluginSpec(
            plugin_id=plugin_id,
            name=name,
            plugin_type=plugin_type,
            quick_start=True,
            features=["lifecycle", "entry_point"],
        )
        return _generate_and_report(spec, target_dir)

    # ── Page 2: Advanced config ──

    # Description
    description = ask_text("插件描述 (Description)", default="")
    if description is None:
        return _cancelled()

    # Author
    author_name = ask_text("作者名称 (Author Name)", default="")
    if author_name is None:
        return _cancelled()

    author_email = ""
    if author_name:
        author_email = ask_text("作者邮箱 (Author Email)", default="") or ""

    # Extension-specific: host plugin
    host_plugin_id = ""
    host_prefix = ""
    if plugin_type == "extension":
        host_plugin_id = ask_text("宿主插件 ID (Host Plugin ID)") or ""
        if not host_plugin_id:
            print("[FAIL] extension type requires a host plugin ID", file=sys.stderr)
            return 1
        if not _PLUGIN_ID_RE.fullmatch(host_plugin_id):
            print(f"[FAIL] invalid host plugin ID: '{host_plugin_id}'", file=sys.stderr)
            return 1
        host_prefix = ask_text("路由前缀 (Route Prefix)", default="") or ""

    # Features
    feature_choices = _get_feature_choices(plugin_type)
    default_features = ["lifecycle", "entry_point"]
    features = ask_checkbox(
        "选择功能 (Features)",
        choices=feature_choices,
        defaults=default_features,
    )
    if features is None:
        return _cancelled()

    # pyproject.toml
    create_pyproject = ask_confirm("创建 pyproject.toml?", default=True)
    if create_pyproject is None:
        return _cancelled()

    spec = PluginSpec(
        plugin_id=plugin_id,
        name=name,
        plugin_type=plugin_type,
        description=description,
        author_name=author_name,
        author_email=author_email,
        host_plugin_id=host_plugin_id,
        host_prefix=host_prefix,
        features=features,
        create_pyproject=create_pyproject,
    )
    return _generate_and_report(spec, target_dir)


# ---------------------------------------------------------------------------
# Non-interactive flow
# ---------------------------------------------------------------------------

def _handle_non_interactive(args: argparse.Namespace, *, defaults: CliDefaults) -> int:
    plugin_id = args.plugin_id
    if not plugin_id:
        print("[FAIL] plugin_id is required in non-interactive mode", file=sys.stderr)
        return 1
    if not _PLUGIN_ID_RE.fullmatch(plugin_id):
        print(f"[FAIL] invalid plugin ID: '{plugin_id}'", file=sys.stderr)
        return 1

    target_dir = defaults.plugins_root / plugin_id
    if target_dir.exists():
        print(f"[FAIL] directory already exists: {target_dir}", file=sys.stderr)
        return 1

    plugin_type = args.plugin_type or "plugin"
    if plugin_type == "extension":
        print("[FAIL] --type extension requires interactive setup for host plugin ID", file=sys.stderr)
        return 1

    spec = PluginSpec(
        plugin_id=plugin_id,
        name=args.name or plugin_id,
        plugin_type=plugin_type,
        quick_start=True,
        features=["lifecycle", "entry_point"],
    )
    return _generate_and_report(spec, target_dir)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_and_report(spec: PluginSpec, target_dir: Path) -> int:
    try:
        created = generate_plugin(spec, target_dir)
    except Exception as exc:
        print(f"[FAIL] {exc}", file=sys.stderr)
        return 1

    print(f"\n[OK] 已创建 {target_dir}/")
    for path in created:
        print(f"  └── {path.name}")
    print(f"\n  入口类: {spec.class_name}")
    print(f"  entry:  {spec.entry_point}")
    return 0


def _cancelled() -> int:
    print("\n已取消。", file=sys.stderr)
    return 1


def _validate_plugin_id(text: str) -> bool | str:
    text = text.strip()
    if not text:
        return "Plugin ID 不能为空"
    if not _PLUGIN_ID_RE.fullmatch(text):
        return "只允许字母、数字、下划线和连字符"
    return True


def _get_feature_choices(plugin_type: str) -> list[dict[str, str]]:
    """Return feature choices appropriate for the plugin type."""
    choices = [
        {"value": "lifecycle", "name": "生命周期 (startup/shutdown)"},
        {"value": "entry_point", "name": "入口点 (plugin_entry)"},
        {"value": "timer", "name": "定时任务 (timer_interval)"},
        {"value": "message", "name": "消息处理 (message handler)"},
        {"value": "store", "name": "持久化存储 (PluginStore)"},
        {"value": "cross_plugin", "name": "跨插件调用 (self.plugins)"},
        {"value": "static_ui", "name": "静态 Web UI"},
        {"value": "async_support", "name": "异步支持 (async entry points)"},
        {"value": "bus_events", "name": "事件总线 (Bus pub/sub)"},
        {"value": "settings", "name": "类型安全配置 (PluginSettings)"},
    ]

    if plugin_type == "extension":
        # Extensions don't need some features
        skip = {"timer", "message", "static_ui"}
        choices = [c for c in choices if c["value"] not in skip]

    return choices
