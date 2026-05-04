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
    entry_point_override: str = ""

    # Extension-specific
    host_plugin_id: str = ""
    host_prefix: str = ""

    # Features
    features: list[str] = field(default_factory=list)
    # Possible features:
    #   lifecycle, entry_point, timer, message, store, cross_plugin,
    #   static_ui, async_support, bus_events, settings

    create_pyproject: bool = True
    create_readme: bool = True
    create_tests: bool = True
    create_gitignore: bool = True
    create_vscode: bool = True
    create_github_actions: bool = False
    neko_repository: str = "owner/N.E.K.O"
    neko_ref: str = "main"
    quick_start: bool = False

    @property
    def class_name(self) -> str:
        # Split on both _ and - for CamelCase conversion
        import re
        parts = re.split(r"[_-]", self.plugin_id)
        return "".join(p.capitalize() for p in parts if p) + "Plugin"

    @property
    def entry_point(self) -> str:
        if self.entry_point_override:
            return self.entry_point_override
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

    if spec.create_readme:
        readme_path = target_dir / "README.md"
        readme_path.write_text(_render_readme_md(spec), encoding="utf-8", newline="\n")
        created.append(readme_path)

    if spec.create_tests:
        tests_dir = target_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        smoke_path = tests_dir / "test_smoke.py"
        smoke_path.write_text(_render_smoke_test(spec), encoding="utf-8", newline="\n")
        created.append(smoke_path)

    if spec.create_gitignore:
        gitignore_path = target_dir / ".gitignore"
        gitignore_path.write_text(_render_gitignore(), encoding="utf-8", newline="\n")
        created.append(gitignore_path)

    if spec.create_vscode:
        vscode_dir = target_dir / ".vscode"
        vscode_dir.mkdir(parents=True, exist_ok=True)
        settings_path = vscode_dir / "settings.json"
        settings_path.write_text(_render_vscode_settings(), encoding="utf-8", newline="\n")
        created.append(settings_path)

        tasks_path = vscode_dir / "tasks.json"
        tasks_path.write_text(_render_vscode_tasks(spec), encoding="utf-8", newline="\n")
        created.append(tasks_path)

    if spec.create_github_actions:
        workflow_dir = target_dir / ".github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        workflow_path = workflow_dir / "verify.yml"
        workflow_path.write_text(_render_verify_workflow(spec), encoding="utf-8", newline="\n")
        created.append(workflow_path)

    return created


def generate_repo_support_files(
    spec: PluginSpec,
    target_dir: Path,
    *,
    overwrite: bool = False,
) -> list[Path]:
    """Generate repository support files for an existing plugin directory."""
    if not target_dir.is_dir():
        raise FileNotFoundError(f"plugin directory not found: {target_dir}")

    created: list[Path] = []

    if spec.create_readme:
        _write_support_file(
            target_dir / "README.md",
            _render_readme_md(spec),
            created=created,
            overwrite=overwrite,
        )

    if spec.create_tests:
        tests_dir = target_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        _write_support_file(
            tests_dir / "test_smoke.py",
            _render_smoke_test(spec),
            created=created,
            overwrite=overwrite,
        )

    if spec.create_gitignore:
        _write_support_file(
            target_dir / ".gitignore",
            _render_gitignore(),
            created=created,
            overwrite=overwrite,
        )

    if spec.create_vscode:
        vscode_dir = target_dir / ".vscode"
        vscode_dir.mkdir(parents=True, exist_ok=True)
        _write_support_file(
            vscode_dir / "settings.json",
            _render_vscode_settings(),
            created=created,
            overwrite=overwrite,
        )
        _write_support_file(
            vscode_dir / "tasks.json",
            _render_vscode_tasks(spec),
            created=created,
            overwrite=overwrite,
        )

    if spec.create_github_actions:
        workflow_dir = target_dir / ".github" / "workflows"
        workflow_dir.mkdir(parents=True, exist_ok=True)
        _write_support_file(
            workflow_dir / "verify.yml",
            _render_verify_workflow(spec),
            created=created,
            overwrite=overwrite,
        )

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
    Ok,
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
    imports = ["NekoPluginBase", "neko_plugin", "Ok"]
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
    Ok,
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
from plugin.sdk.plugin import neko_plugin, plugin_entry, lifecycle, Ok
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
# Repository support files
# ---------------------------------------------------------------------------

def _render_readme_md(spec: PluginSpec) -> str:
    name = spec.name or spec.plugin_id
    description = spec.description or "Describe what this plugin does and how to configure it."
    return f'''# {name}

{description}

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/{spec.plugin_id}
```

From the N.E.K.O `plugin/` directory:

```bash
uv run python neko-plugin-cli/cli.py pack {spec.plugin_id}
uv run python neko-plugin-cli/cli.py inspect neko-plugin-cli/target/{spec.plugin_id}.neko-plugin
uv run python neko-plugin-cli/cli.py verify neko-plugin-cli/target/{spec.plugin_id}.neko-plugin
```

## Entry

```toml
entry = "{spec.entry_point}"
```
'''


def _render_smoke_test(spec: PluginSpec) -> str:
    return f'''from pathlib import Path


def test_plugin_manifest_exists() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "plugin.toml"
    assert manifest.is_file()
    text = manifest.read_text(encoding="utf-8")
    assert 'id = "{spec.plugin_id}"' in text
    assert 'entry = "{spec.entry_point}"' in text
'''


def _render_gitignore() -> str:
    return '''__pycache__/
*.py[cod]
.pytest_cache/
.mypy_cache/
.ruff_cache/
.venv/
venv/
dist/
build/
*.egg-info/
.env
.DS_Store
'''


def _render_vscode_settings() -> str:
    return '''{
  "nekoPlugin.pluginRoot": "../..",
  "python.analysis.extraPaths": [
    "${workspaceFolder}/../.."
  ]
}
'''


def _render_vscode_tasks(spec: PluginSpec) -> str:
    return f'''{{
  "version": "2.0.0",
  "tasks": [
    {{
      "label": "N.E.K.O: pack {spec.plugin_id}",
      "type": "shell",
      "command": "uv run python neko-plugin-cli/cli.py pack {spec.plugin_id}",
      "options": {{
        "cwd": "${{config:nekoPlugin.pluginRoot}}"
      }},
      "problemMatcher": []
    }},
    {{
      "label": "N.E.K.O: verify {spec.plugin_id}",
      "type": "shell",
      "command": "uv run python neko-plugin-cli/cli.py verify {spec.plugin_id}.neko-plugin",
      "options": {{
        "cwd": "${{config:nekoPlugin.pluginRoot}}"
      }},
      "problemMatcher": []
    }},
    {{
      "label": "N.E.K.O: inspect {spec.plugin_id}",
      "type": "shell",
      "command": "uv run python neko-plugin-cli/cli.py inspect {spec.plugin_id}.neko-plugin",
      "options": {{
        "cwd": "${{config:nekoPlugin.pluginRoot}}"
      }},
      "problemMatcher": []
    }},
    {{
      "label": "N.E.K.O: test {spec.plugin_id}",
      "type": "shell",
      "command": "uv run pytest ${{workspaceFolder}}/tests",
      "options": {{
        "cwd": "${{config:nekoPlugin.pluginRoot}}"
      }},
      "problemMatcher": []
    }}
  ]
}}
'''


def _render_verify_workflow(spec: PluginSpec) -> str:
    return f'''name: Verify N.E.K.O Plugin

on:
  push:
  pull_request:
  workflow_dispatch:

env:
  PLUGIN_ID: {spec.plugin_id}
  NEKO_REPOSITORY: {spec.neko_repository}
  NEKO_REF: {spec.neko_ref}

jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout plugin repository
        uses: actions/checkout@v4
        with:
          path: plugin-repo

      - name: Checkout N.E.K.O
        uses: actions/checkout@v4
        with:
          repository: ${{{{ env.NEKO_REPOSITORY }}}}
          ref: ${{{{ env.NEKO_REF }}}}
          path: neko

      - name: Install uv
        uses: astral-sh/setup-uv@v5

      - name: Mount plugin into N.E.K.O tree
        run: |
          rm -rf "neko/plugin/plugins/${{PLUGIN_ID}}"
          mkdir -p neko/plugin/plugins
          cp -R plugin-repo "neko/plugin/plugins/${{PLUGIN_ID}}"

      - name: Test plugin
        working-directory: neko/plugin
        run: |
          if [ -d "plugins/${{PLUGIN_ID}}/tests" ]; then
            uv run pytest "plugins/${{PLUGIN_ID}}/tests"
          else
            echo "No plugin tests found; skipping."
          fi

      - name: Pack and verify
        working-directory: neko/plugin
        run: |
          uv run python neko-plugin-cli/cli.py pack "${{PLUGIN_ID}}"
          uv run python neko-plugin-cli/cli.py inspect "${{PLUGIN_ID}}.neko-plugin" | tee "neko-plugin-cli/target/${{PLUGIN_ID}}.inspect.txt"
          uv run python neko-plugin-cli/cli.py verify "${{PLUGIN_ID}}.neko-plugin" | tee "neko-plugin-cli/target/${{PLUGIN_ID}}.verify.txt"

      - name: Write verification summary
        working-directory: neko/plugin
        run: |
          PACKAGE="neko-plugin-cli/target/${{PLUGIN_ID}}.neko-plugin"
          PACKAGE_SHA256="$(sha256sum "$PACKAGE" | awk '{{print $1}}')"
          NEKO_COMMIT="$(git -C .. rev-parse HEAD)"

          {{
            echo "## N.E.K.O Plugin Verification"
            echo ""
            echo "| Field | Value |"
            echo "| --- | --- |"
            echo "| Plugin ID | ${{PLUGIN_ID}} |"
            echo "| Plugin commit | ${{GITHUB_SHA}} |"
            echo "| N.E.K.O repository | ${{NEKO_REPOSITORY}} |"
            echo "| N.E.K.O ref | ${{NEKO_REF}} |"
            echo "| N.E.K.O commit | ${{NEKO_COMMIT}} |"
            echo "| Package | ${{PLUGIN_ID}}.neko-plugin |"
            echo "| Package SHA256 | ${{PACKAGE_SHA256}} |"
            echo ""
            echo "### Inspect"
            echo '```text'"
            cat "neko-plugin-cli/target/${{PLUGIN_ID}}.inspect.txt"
            echo '```'
            echo ""
            echo "### Verify"
            echo '```text'"
            cat "neko-plugin-cli/target/${{PLUGIN_ID}}.verify.txt"
            echo '```'
          }} >> "$GITHUB_STEP_SUMMARY"

      - name: Upload verification artifact
        uses: actions/upload-artifact@v4
        with:
          name: ${{{{ env.PLUGIN_ID }}}}-verification
          path: |
            neko/plugin/neko-plugin-cli/target/${{{{ env.PLUGIN_ID }}}}.neko-plugin
            neko/plugin/neko-plugin-cli/target/${{{{ env.PLUGIN_ID }}}}.inspect.txt
            neko/plugin/neko-plugin-cli/target/${{{{ env.PLUGIN_ID }}}}.verify.txt
'''


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _write_support_file(path: Path, content: str, *, created: list[Path], overwrite: bool) -> None:
    if path.exists() and not overwrite:
        return
    path.write_text(content, encoding="utf-8", newline="\n")
    created.append(path)
