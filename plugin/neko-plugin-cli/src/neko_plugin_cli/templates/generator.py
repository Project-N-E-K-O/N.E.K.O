"""Generate plugin scaffolding files from collected options."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class PluginSpec:
    """All the information needed to generate a plugin scaffold."""

    plugin_id: str
    name: str = ""
    plugin_type: str = "plugin"  # plugin | extension | adapter
    description: str = ""
    version: str = "0.1.0"
    author_name: str = ""
    author_email: str = ""

    # Extension-specific
    host_plugin_id: str = ""
    host_prefix: str = ""

    # Features
    features: list[str] = field(default_factory=list)
    # Possible features:
    #   lifecycle, entry_point, timer, message, store, cross_plugin,
    #   static_ui, async_support, bus_events, settings

    create_pyproject: bool = True
    quick_start: bool = False

    @property
    def class_name(self) -> str:
        # Split on both _ and - for CamelCase conversion
        import re
        parts = re.split(r"[_-]", self.plugin_id)
        return "".join(p.capitalize() for p in parts if p) + "Plugin"

    @property
    def entry_point(self) -> str:
        return f"plugin.plugins.{self.plugin_id}:{self.class_name}"

    @property
    def module_path(self) -> str:
        return f"plugins.{self.plugin_id}"


def generate_plugin(spec: PluginSpec, target_dir: Path) -> list[Path]:
    """Generate all scaffold files and return the list of created paths."""
    target_dir.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []

    # plugin.toml
    toml_path = target_dir / "plugin.toml"
    toml_path.write_text(_render_plugin_toml(spec), encoding="utf-8", newline="\n")
    created.append(toml_path)

    # __init__.py
    init_path = target_dir / "__init__.py"
    init_path.write_text(_render_init_py(spec), encoding="utf-8", newline="\n")
    created.append(init_path)

    # pyproject.toml (optional)
    if spec.create_pyproject:
        pyproject_path = target_dir / "pyproject.toml"
        pyproject_path.write_text(_render_pyproject_toml(spec), encoding="utf-8", newline="\n")
        created.append(pyproject_path)

    return created


# ---------------------------------------------------------------------------
# plugin.toml
# ---------------------------------------------------------------------------

def _render_plugin_toml(spec: PluginSpec) -> str:
    lines = [
        "[plugin]",
        f'id = "{spec.plugin_id}"',
        f'name = "{_escape(spec.name or spec.plugin_id)}"',
    ]

    if spec.description:
        lines.append(f'description = "{_escape(spec.description)}"')

    lines.append(f'version = "{spec.version}"')
    lines.append(f'type = "{spec.plugin_type}"')
    lines.append(f'entry = "{spec.entry_point}"')

    if spec.author_name or spec.author_email:
        lines.append("")
        lines.append("[plugin.author]")
        if spec.author_name:
            lines.append(f'name = "{_escape(spec.author_name)}"')
        if spec.author_email:
            lines.append(f'email = "{_escape(spec.author_email)}"')

    lines.extend([
        "",
        "[plugin.sdk]",
        'recommended = ">=0.1.0,<0.2.0"',
        'supported = ">=0.1.0,<0.3.0"',
    ])

    if "store" in spec.features:
        lines.extend(["", "[plugin.store]", "enabled = true"])

    if spec.plugin_type == "extension" and spec.host_plugin_id:
        lines.extend([
            "",
            "[plugin.host]",
            f'plugin_id = "{spec.host_plugin_id}"',
        ])
        if spec.host_prefix:
            lines.append(f'prefix = "{_escape(spec.host_prefix)}"')

    auto_start = "true" if "timer" in spec.features or "message" in spec.features else "false"
    lines.extend([
        "",
        "[plugin_runtime]",
        "enabled = true",
        f"auto_start = {auto_start}",
    ])

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# __init__.py
# ---------------------------------------------------------------------------

def _render_init_py(spec: PluginSpec) -> str:
    if spec.quick_start:
        return _render_quick_start_init(spec)
    if spec.plugin_type == "extension":
        return _render_extension_init(spec)
    if spec.plugin_type == "adapter":
        return _render_adapter_init(spec)
    return _render_plugin_init(spec)


def _render_quick_start_init(spec: PluginSpec) -> str:
    return f'''from typing import Any
from plugin.sdk.plugin import (
    NekoPluginBase, neko_plugin, plugin_entry, lifecycle,
    Ok, Err, SdkError,
)


@neko_plugin
class {spec.class_name}(NekoPluginBase):
    """{_escape(spec.name or spec.plugin_id)}"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("{spec.class_name} started")
        return Ok({{"status": "ready"}})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("{spec.class_name} stopped")
        return Ok({{"status": "stopped"}})

    @plugin_entry(
        id="hello",
        name="Hello",
        description="Say hello",
        input_schema={{
            "type": "object",
            "properties": {{
                "name": {{"type": "string", "default": "World"}}
            }}
        }}
    )
    def hello(self, name: str = "World", **_):
        return Ok({{"message": f"Hello, {{name}}!"}})
'''


def _render_plugin_init(spec: PluginSpec) -> str:
    imports = ["NekoPluginBase", "neko_plugin", "Ok", "Err", "SdkError"]
    decorators_needed: list[str] = []

    if "lifecycle" in spec.features or "entry_point" in spec.features:
        # Always include these for non-quick-start plugins
        pass
    if "lifecycle" in spec.features:
        imports.append("lifecycle")
    if "entry_point" in spec.features:
        imports.append("plugin_entry")
    if "timer" in spec.features:
        imports.append("timer_interval")
    if "message" in spec.features:
        imports.append("message")

    extra_imports: list[str] = []
    if "store" in spec.features:
        extra_imports.append("from plugin.sdk.plugin import PluginStore")
    if "settings" in spec.features:
        extra_imports.append("from plugin.sdk.plugin import PluginSettings")

    is_async = "async_support" in spec.features

    lines = [
        "from typing import Any",
        f"from plugin.sdk.plugin import (",
        f"    {', '.join(imports)},",
        ")",
    ]
    for imp in extra_imports:
        lines.append(imp)

    lines.extend([
        "",
        "",
        "@neko_plugin",
        f"class {spec.class_name}(NekoPluginBase):",
        f'    """{_escape(spec.name or spec.plugin_id)}"""',
        "",
        "    def __init__(self, ctx: Any):",
        "        super().__init__(ctx)",
        "        self.logger = ctx.logger",
    ])

    if "store" in spec.features:
        lines.append("        self.store = PluginStore(ctx)")

    # lifecycle
    if "lifecycle" in spec.features:
        if is_async:
            lines.extend([
                "",
                '    @lifecycle(id="startup")',
                "    async def on_startup(self, **_):",
                f'        self.logger.info("{spec.class_name} started")',
                '        return Ok({"status": "ready"})',
                "",
                '    @lifecycle(id="shutdown")',
                "    async def on_shutdown(self, **_):",
                f'        self.logger.info("{spec.class_name} stopped")',
                '        return Ok({"status": "stopped"})',
            ])
        else:
            lines.extend([
                "",
                '    @lifecycle(id="startup")',
                "    def on_startup(self, **_):",
                f'        self.logger.info("{spec.class_name} started")',
                '        return Ok({"status": "ready"})',
                "",
                '    @lifecycle(id="shutdown")',
                "    def on_shutdown(self, **_):",
                f'        self.logger.info("{spec.class_name} stopped")',
                '        return Ok({"status": "stopped"})',
            ])

    # entry point
    if "entry_point" in spec.features:
        async_kw = "async " if is_async else ""
        lines.extend([
            "",
            "    @plugin_entry(",
            f'        id="example",',
            f'        name="Example Entry",',
            f'        description="An example entry point",',
            "        input_schema={",
            '            "type": "object",',
            '            "properties": {',
            '                "input": {"type": "string", "default": ""}',
            "            }",
            "        }",
            "    )",
            f"    {async_kw}def example(self, input: str = \"\", **_):",
            '        return Ok({"result": input})',
        ])

    # timer
    if "timer" in spec.features:
        lines.extend([
            "",
            '    @timer_interval(id="heartbeat", seconds=60, auto_start=True)',
            "    def heartbeat(self, **_):",
            '        self.logger.debug("heartbeat")',
            '        return Ok({"alive": True})',
        ])

    # message
    if "message" in spec.features:
        async_kw = "async " if is_async else ""
        lines.extend([
            "",
            '    @message(id="handle_message", auto_start=True)',
            f"    {async_kw}def handle_message(self, text: str = \"\", **_):",
            '        self.logger.info(f"Received: {text}")',
            '        return Ok({"handled": True})',
        ])

    lines.append("")
    return "\n".join(lines)


def _render_extension_init(spec: PluginSpec) -> str:
    return f'''from plugin.sdk.extension import (
    NekoExtensionBase, extension, extension_entry,
    Ok, Err,
)


@extension
class {spec.class_name}(NekoExtensionBase):
    """{_escape(spec.name or spec.plugin_id)}"""

    @extension_entry(id="example", description="An example extension entry")
    def example(self, param: str = "", **_):
        return Ok({{"extended": True, "param": param}})
'''


def _render_adapter_init(spec: PluginSpec) -> str:
    return f'''from typing import Any
from plugin.sdk.plugin import neko_plugin, plugin_entry, lifecycle, Ok, Err, SdkError
from plugin.sdk.adapter import AdapterGatewayCore, NekoAdapterPlugin


@neko_plugin
class {spec.class_name}(NekoAdapterPlugin):
    """{_escape(spec.name or spec.plugin_id)}"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    async def on_startup(self, **_):
        self.logger.info("{spec.class_name} started")
        return Ok({{"status": "ready"}})

    @lifecycle(id="shutdown")
    async def on_shutdown(self, **_):
        self.logger.info("{spec.class_name} stopped")
        return Ok({{"status": "stopped"}})

    @plugin_entry(id="handle_request")
    async def handle_request(self, raw_data: dict = None, **_):
        return Ok({{"received": raw_data}})
'''


# ---------------------------------------------------------------------------
# pyproject.toml
# ---------------------------------------------------------------------------

def _render_pyproject_toml(spec: PluginSpec) -> str:
    return f'''[project]
name = "{spec.plugin_id}"
version = "{spec.version}"
dependencies = []
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
