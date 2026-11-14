# Create your first plugin with the N.E.K.O Plugin CLI

## Develop with an installed N.E.K.O application in the plugin workbench

You do not need to clone the main repository or run a source checkout of the backend; plugin source can live in any folder on the machine:

1. Open **Plugin studio** in the plugin manager sidebar. Choose **New plugin** to generate a standard project, or **Add existing project** to select a folder containing `plugin.toml`. In a browser, the folder is chosen on the **backend machine**.
2. Registered projects appear in the plugin list and plugin details like the source plugins under `plugin/plugins/`, but they never start automatically. Start, stop and reload them from the plugin pages.
3. Edit in your own editor and click **Reload**. With hot reload enabled, a running project reloads after its source changes; source that cannot load keeps the running instance.
4. Select the project in the plugin CLI build view to produce a `.neko-plugin` package. A stopped plugin can also be packaged.

Desktop packages that bundle the plugin tools support GitHub repository creation, local prechecks, and publication from the **Publish** tab. Install Git, the GitHub CLI (`gh`), and `uv`. On first use the workbench asks to prepare the plugin tools once (it downloads Python 3.11 and the plugin dependencies). Sign in with `gh auth login` to create the repository from the workbench, commit and push the plugin source, then run the checks or start a GitHub release.

The project folder name must match the plugin ID and the entry package name, and the plugin ID must not be used by an installed plugin or another project. Existing dependency declarations and `vendor/` rules apply; dependencies are not installed automatically.

**Remove from workbench** stops a running plugin and removes it from the plugin list; the source and runtime data are kept. Workbench projects cannot be uninstalled through the plugin page's **Delete**.

The CLI tutorial below covers source-based scaffolding, checks and release automation.

This page confirms that the Plugin CLI included with N.E.K.O works, then uses it to create a Hello World plugin that you can run and continue developing immediately.

Plugins under development live directly in `N.E.K.O/plugin/plugins/`. You will finish with a `hello_world` project containing example code, configuration, tests, code checks, and GitHub release workflows.

## 1. Check Git and uv

Open a terminal and run:

```bash
git --version
uv --version
```

| Command | What it verifies |
| --- | --- |
| `git --version` | Git is available. You will use it to clone N.E.K.O and later commit and push plugin versions. |
| `uv --version` | uv is available. You will use it to install the locked Python dependencies and run Plugin CLI. |

Both commands must print a version. If either command is not found, install the missing tool first:

- Install Git from the [official Git downloads](https://git-scm.com/downloads).
- Install uv using the [official uv installation guide](https://docs.astral.sh/uv/getting-started/installation/).

## 2. Get the N.E.K.O source

Plugin CLI ships with the N.E.K.O source and cannot be installed separately. The recommended way to start developing a **N.E.K.O plugin** is to get the source directly:

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
```

If you already have the source, do not clone it again. Enter your existing checkout instead:

```bash
cd /path/to/N.E.K.O
```

::: warning Do not clone over an existing directory
`git clone` stops when a directory named `N.E.K.O` already exists. Check whether it is your existing source checkout. Do not delete it just to continue this guide; it may contain configuration or uncommitted work.
:::

## 3. Prepare the environment and verify the CLI

Run these commands from the N.E.K.O repository root:

```bash
uv sync
uv run neko-plugin --help
```

The `neko-plugin` help output should include at least:

```text
init
check
sync
build
publish
```

When those commands appear, the CLI is ready. Continue using `uv run neko-plugin`.

## 4. Create the plugin in the source tree

Stay in the N.E.K.O repository root and run:

```bash
uv run neko-plugin init hello_world \
  --type plugin \
  --name "Hello World"
```

This creates **Hello World** directly at:

```text
plugin/plugins/hello_world/
```

N.E.K.O scans `plugin/plugins/`, so this is the source code that actually runs during development. Do not copy it to a user plugin directory and do not create a symbolic link.

This only describes where code is loaded from. Runtime configuration, persistent data, and cache still belong in the user data directory.

### What belongs in each directory

The same plugin uses two locations during source development:

```text
N.E.K.O/plugin/plugins/hello_world/       <- edit this source
├── plugin.toml                           <- plugin identity and code entry
├── config.example.toml                   <- initial runtime config template
├── __init__.py                           <- plugin code
└── tests/

<user data root>/plugins/hello_world/     <- written by N.E.K.O at runtime
├── config/plugin.toml                    <- this user's runtime config
├── data/                                 <- persistent plugin data
└── cache/                                <- disposable plugin cache
```

Unless the storage location or related environment variables have been changed, the default user data root is:

| System | Default directory |
| --- | --- |
| Linux | `~/.local/share/N.E.K.O` |
| macOS | `~/Library/Application Support/N.E.K.O` |
| Windows | `%LOCALAPPDATA%\N.E.K.O` |

The root `plugin.toml` is the plugin manifest. `config/plugin.toml` in user data is that user's runtime configuration. On first use, N.E.K.O copies `config.example.toml` to the runtime configuration path; it does not overwrite an existing configuration.

Installed package code and writable runtime state are stored separately. This guide is about source development, so only edit `N.E.K.O/plugin/plugins/hello_world/`; N.E.K.O manages the installed and user-data locations.

If the destination already exists, the CLI stops without overwriting it. Choose a new directory or inspect the existing one before continuing.

## 5. Run the first check

```bash
uv run neko-plugin check hello_world
```

A new project should report:

```text
[OK] hello_world: check found 0 error(s), 4 warning(s)
```

The four warnings are:

- no `startup` lifecycle hook
- no `shutdown` lifecycle hook
- no configured Git remote
- uncommitted files in the Git worktree

This Hello World plugin does not need startup or shutdown hooks. The Git remote and commit warnings only affect publishing. None of these warnings prevent the plugin from running. Stop and follow the suggested fix when the command reports `[FAIL]` or an error.

## What the CLI created

```text
plugin/plugins/hello_world/
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

Open `plugin/plugins/hello_world/plugin.toml`. The CLI has already written the plugin identity and entry point:

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

- `id` is the plugin's stable identity and should match the source directory name.
- `name` is the name shown in Plugin Manager.
- `version` is used by the next build and release.
- `entry` names the Python plugin class as `module.path:ClassName`.
- `[plugin.sdk]` declares the supported SDK versions.

Before others use the plugin, add a `description` and a one-line `short_description` under `[plugin]` (see [Plugin Config](/plugins/plugin-toml)). NekoClaw picks plugins for a task by them; without a `short_description`, a long `description` is summarized by a model, which costs a request.

The scaffold also creates `config.example.toml` as the initial runtime configuration for new users:

```toml
[plugin_runtime]
enabled = true
auto_start = false
```

`enabled = true` allows the plugin to load by default. `auto_start = false` means the user starts it manually in Plugin Manager.

After the real configuration is created, the user edits `config/plugin.toml` under the user data directory. Later changes to `config.example.toml` do not overwrite existing user configuration.

## 7. Write the first plugin feature

Open `plugin/plugins/hello_world/__init__.py`. It already contains an entry that greets someone by name:

```python
from plugin.sdk.plugin import NekoPluginBase, Ok, neko_plugin, plugin_entry


@neko_plugin
class HelloWorldPlugin(NekoPluginBase):
    @plugin_entry(id="hello", name="Hello", description="Say hello")
    async def hello(self, name: str = "World", **_):
        return Ok({"message": f"Hello, {name}!"})
```

| Code | Purpose |
| --- | --- |
| `@neko_plugin` | Declares the class as a N.E.K.O plugin |
| `NekoPluginBase` | Provides logging, configuration, storage, and other plugin facilities |
| `@plugin_entry(...)` | Exposes a callable feature in Plugin Manager |
| `async def hello(...)` | Defines the work performed by the entry. Plugin entries must use `async def`. |
| `name: str = "World"` | Declares an optional string parameter; it uses `World` when omitted. |
| `**_` | Accepts extra information supplied by the N.E.K.O runtime; this entry does not need to use it. |
| `Ok({...})` | Returns a successful result |

The `plugin_id` is `hello_world`; this feature's `entry_id` is `hello`. They identify different things and are not interchangeable.

::: tip Plugin entries and LLM tools are different
`@plugin_entry` declares a runtime plugin entry. `@llm_tool` registers a tool used during a conversation. The first run only needs `@plugin_entry`; no LLM tool setup is required.
:::

Change the final line to:

```python
return Ok({"message": f"Hey {name}, welcome to N.E.K.O!"})
```

Save and check the project again:

```bash
uv run neko-plugin check hello_world
```

## 8. Run it in N.E.K.O

If the source version of N.E.K.O is not running yet, follow [Development Setup](/guide/dev-setup), then run from the repository root:

```bash
uv run python launcher.py
```

Open the address printed in the terminal. On the **Plugins** page:

1. Click **Refresh** so N.E.K.O scans `plugin/plugins/` again.
2. Find and start **Hello World**.
3. Open its details and switch to **Entries**.
4. Find **Hello**, trigger it, and enter a name.

A successful result confirms that the plugin is running directly from its source directory.

## 9. Edit and reload

Continue editing `plugin/plugins/hello_world/`. After each change:

1. Save the code and run `uv run neko-plugin check hello_world`.
2. Return to the plugin details and click **Reload**.
3. Trigger **Hello** again to verify the change.

Reload stops the current plugin and starts it again from the saved source. Daily development does not require building or repeatedly importing an installation package. If you add or remove a plugin or change `plugin.toml`, refresh the plugin list first.

### Automatic hot reload

Set `NEKO_PLUGIN_HOT_RELOAD=true` before starting N.E.K.O. and the plugin server watches every plugin source directory (built-in roots, installed roots, and workbench project folders). When a `*.py` or `plugin.toml` file stops changing for the debounce window, the running plugin is reloaded automatically — no button click needed:

```bash
# PowerShell
$env:NEKO_PLUGIN_HOT_RELOAD = "true"; uv run python launcher.py
# bash
NEKO_PLUGIN_HOT_RELOAD=true uv run python launcher.py
```

Details worth knowing:

- Only **running** plugins are reloaded; a plugin you stopped stays stopped. The one exception: if an automatic reload stopped the plugin and then failed to start it (for example a bad import), your next save retries the start. Any manual Start/Stop/Reload, uninstall or reinstall cancels that retry.
- The manifest, entry point and source syntax are checked before the reload. A broken edit keeps the current instance running and logs a warning; the next save retries.
- The debounce window (`NEKO_PLUGIN_HOT_RELOAD_DEBOUNCE`, default 1.5s) absorbs multi-file saves; the poll interval is `NEKO_PLUGIN_HOT_RELOAD_INTERVAL` (default 1.0s).
- Auto reloads take the same lock as the manual button. If you are mid-operation, the auto reload waits up to the debounce window for the lock, then defers by one debounce window and retries instead of interrupting.

## 10. Build only when you are ready to deliver

Build a `.neko-plugin` package when other users need to install the plugin:

```bash
uv run neko-plugin build hello_world --out hello_world.neko-plugin
```

The package is for installation and release, not a required development step. See [Publish a plugin to the Market](/plugins/cli) for the complete GitHub review and release flow.

## Next steps

| I want to… | Read |
| --- | --- |
| Upload to GitHub, submit for review, and publish versions | [Publish a plugin to the Market](/plugins/cli) |
| Understand `plugin.toml` | [Plugin Config](/plugins/plugin-toml) |
| Add callable plugin features | [Entries & Parameters](/plugins/entries) |
| Run code during startup or shutdown | [Decorators](/plugins/decorators) |
| Register a conversation-time LLM tool | [LLM Tool Calling](/plugins/tool-calling) |
| Build a UI panel | [Hosted UI](/plugins/hosted-ui) |
| Study real plugin examples | [Examples](/plugins/examples) |
| Handle errors correctly | [Best Practices](/plugins/best-practices) |
| Browse the complete SDK | [SDK Reference](/plugins/sdk-reference) |
