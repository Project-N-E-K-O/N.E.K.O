# Create your first plugin with the CLI

This quick start has one goal: verify that Plugin CLI works on your computer, then use it to create a standalone plugin repository that is ready for development.

You will finish with a `hello_world` project that already contains example code, tests, code checks, and GitHub release workflows.

## 1. Check Git and uv

Open a terminal and run:

```bash
git --version
uv --version
```

These commands do not install anything. They verify that the current terminal can find both required tools before you continue:

| Command | What it verifies |
| --- | --- |
| `git --version` | Git is available. You will use it to clone N.E.K.O and later commit and push plugin versions. |
| `uv --version` | uv is available. You will use it to install the locked Python dependencies and run Plugin CLI. |

Both commands must print a version. If either command is not found, stop here:

- Install Git from the [official Git downloads](https://git-scm.com/downloads).
- Install uv using the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

Close and reopen the terminal after installation, then run both checks again. Continue only when both commands print a version.

## 2. Get the N.E.K.O source

Plugin CLI currently ships with the N.E.K.O source and cannot yet be installed separately. Clone the official repository the first time you use it:

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

If you already have the source, do not clone it again or delete the existing directory. Enter your existing checkout instead:

```bash
cd /path/to/N.E.K.O
```

::: warning Do not clone over an existing directory
`git clone` stops when a directory named `N.E.K.O` already exists. Check whether it is your existing source checkout. Do not delete it just to continue this guide; it may contain configuration or uncommitted work.
:::

## 3. Prepare the environment and verify the CLI

Run these commands from the N.E.K.O repository root:

```bash
uv sync --locked
uv run neko-plugin --help
```

The help output should include at least:

```text
init
check
sync
build
publish
```

When those commands appear, the CLI is ready. Continue using `uv run neko-plugin`; do not assume that a global `neko-plugin` executable is installed.

If `uv sync --locked` fails, stop and keep the complete error message. Network access, the Python platform, or dependency state may be responsible; do not regenerate `uv.lock` merely to suppress the failure. Run `git status --short` only before updating the source. If it shows local changes, preserve your work and do not pull or reset. If the checkout is clean and `init` or `publish` is missing, run `git pull --ff-only`, then repeat the two commands above.

## 4. Create a standalone plugin repository

Stay in the N.E.K.O repository root and run:

```bash
uv run neko-plugin init hello_world --type plugin --name "Hello World" --output ../n.e.k.o_plugin_hello_world
```

`--output` is the exact final directory. Keeping the plugin beside N.E.K.O avoids creating a nested Git repository inside the N.E.K.O checkout.

If the destination already exists, the CLI stops without overwriting it. Choose a new directory or inspect the existing one before continuing.

## 5. Run the first check

```bash
uv run neko-plugin check ../n.e.k.o_plugin_hello_world
```

A new project should report:

```text
[OK] hello_world: check found 0 error(s), 2 warning(s)
```

Warnings about a missing GitHub remote or uncommitted files are expected at this point: the new repository has not been committed or pushed. Stop and follow the suggested fix only when the command reports `[FAIL]` or an error.

## What the CLI created

```text
n.e.k.o_plugin_hello_world/
├── .git/
├── .gitignore
├── .vscode/
├── plugin.toml
├── config.example.toml
├── __init__.py
├── pyproject.toml
├── README.md
├── tests/test_smoke.py
├── ruff.toml
└── .github/workflows/
    ├── verify.yml
    └── release.yml
```

You do not need to hand-write the directory structure or assemble GitHub Actions. Continue with the generated files to build the first feature.

## 6. Understand the plugin configuration

Open `../n.e.k.o_plugin_hello_world/plugin.toml`. The CLI has already written the plugin identity and entry point:

```toml
[plugin]
id = "hello_world"
name = "Hello World"
version = "0.1.0"
type = "plugin"
entry = "plugin.plugins.hello_world:HelloWorldPlugin"

[plugin.sdk]
recommended = ">=0.1.0,<0.2.0"
supported = ">=0.1.0,<0.3.0"
```

- `id` is the plugin's stable identity and installed directory name.
- `version` is used by the next build and release.
- `entry` names the Python plugin class as `module.path:ClassName`.
- `[plugin.sdk]` declares the supported SDK versions.

User-controlled runtime defaults belong in `config.example.toml`, not in the identity manifest:

```toml
[plugin_runtime]
enabled = true
auto_start = false
```

`auto_start = false` means that you start the installed plugin manually in Plugin Manager. Set it to `true` only when the plugin should start with N.E.K.O.

## 7. Write the first plugin feature

Open the generated `__init__.py`. It already contains an entry that greets someone by name:

```python
from typing import Any

from plugin.sdk.plugin import (
    NekoPluginBase,
    Ok,
    lifecycle,
    neko_plugin,
    plugin_entry,
)


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    """Hello World"""

    def __init__(self, ctx: Any):
        super().__init__(ctx)
        self.logger = ctx.logger

    @lifecycle(id="startup")
    def on_startup(self, **_):
        self.logger.info("HelloWorldPlugin started")
        return Ok({"status": "ready"})

    @lifecycle(id="shutdown")
    def on_shutdown(self, **_):
        self.logger.info("HelloWorldPlugin stopped")
        return Ok({"status": "stopped"})

    @plugin_entry(
        id="hello",
        name="Hello",
        description="Say hello",
        input_schema={
            "type": "object",
            "properties": {
                "name": {"type": "string", "default": "World"}
            }
        }
    )
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})
```

| Code | Purpose |
| --- | --- |
| `@neko_plugin` | Declares the class as a N.E.K.O plugin |
| `NekoPluginBase` | Provides logging, configuration, storage, and other plugin facilities |
| `@lifecycle(...)` | Runs code when the plugin starts and stops |
| `@plugin_entry(...)` | Exposes a callable feature in Plugin Manager |
| `input_schema` | Describes the input controls shown by the interface |
| `Ok({...})` | Returns a successful result |

The `plugin_id` is `hello_world`; this feature's `entry_id` is `hello`. They identify different things and are not interchangeable.

::: tip Agents and LLM tools
When the user-plugin Agent selects this feature, it returns both `plugin_id` and `entry_id`, and the host validates them separately. This is different from registering a conversation-time tool with `@llm_tool`. You do not need to understand that distinction before the first run.
:::

Change the final line to:

```python
return Ok({"message": f"Hey {name}, welcome to N.E.K.O!"})
```

Save and check the project again:

```bash
uv run neko-plugin check ../n.e.k.o_plugin_hello_world
```

## 8. Build and run it in N.E.K.O

Build a local package after the check passes:

```bash
uv run neko-plugin build ../n.e.k.o_plugin_hello_world --out ../hello_world.neko-plugin
```

Start N.E.K.O, open **Plugin Manager**, select **Import**, and choose `hello_world.neko-plugin`. After the import:

1. Find and start **Hello World**.
2. Open its **Hello** entry.
3. Enter a name and execute it.
4. Confirm that the new greeting is returned.

If the source version of N.E.K.O is not running yet, follow [Development Setup](../guide/dev-setup) to build the frontend and start it.

## 9. Edit and load the new build

Continue editing `__init__.py` in the standalone repository, then run `check` and `build` again. Importing the same package in Plugin Manager now shows an upgrade confirmation. Once confirmed, N.E.K.O safely replaces the installed copy and restarts it if it was running.

Do not edit the installed copy under `plugin/plugins/hello_world`; those changes would not return to the standalone plugin repository.

## Next steps

| I want to… | Read |
| --- | --- |
| Check, package, and publish the plugin | [Create and publish plugins from the command line](./cli) |
| Understand `plugin.toml` | [Plugin Config](./plugin-toml) |
| Add callable plugin features | [Entries & Parameters](./entries) |
| Run code during startup or shutdown | [Decorators](./decorators) |
| Register a conversation-time LLM tool | [LLM Tool Calling](./tool-calling) |
| Build a UI panel | [Hosted UI](./hosted-ui) |
| Study real plugin examples | [Examples](./examples) |
| Handle errors correctly | [Best Practices](./best-practices) |
| Browse the complete SDK | [SDK Reference](./sdk-reference) |
