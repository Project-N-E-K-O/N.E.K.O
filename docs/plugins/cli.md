# Create and publish plugins from the command line

You do not need to prepare every directory, test file, and GitHub configuration by hand when starting a N.E.K.O plugin. Plugin CLI creates a complete plugin project, checks it while you work, and publishes new versions when they are ready.

This guide follows one plugin from an empty directory to its first release in the N.E.K.O Plugin Market.

::: info Current invocation
Plugin CLI currently ships with the N.E.K.O source tree and is not yet available as a standalone installation. Run the commands below from a N.E.K.O source checkout.
:::

## Create a plugin project

```bash
uv run neko-plugin init weather_helper \
  --type plugin \
  --name "Weather Helper" \
  --output ../n.e.k.o_plugin_weather_helper
```

`--output` is the final project directory. The CLI creates the plugin code, manifest, tests, editor tasks, Ruff configuration, and both GitHub workflows in that directory. It also initializes a Git repository on the `main` branch.

Use `--type adapter` instead when the project bridges an external protocol into N.E.K.O.

The files you will edit most often are:

| File | Purpose |
| --- | --- |
| `__init__.py` | Plugin Python code |
| `plugin.toml` | Identity, version, entry point, and runtime settings |
| `pyproject.toml` | Third-party Python libraries used by the plugin |

See [Plugin Config](./plugin-toml) for the full manifest reference.

## Install third-party libraries into the plugin

After changing `[project].dependencies` in `pyproject.toml`, update the plugin's `vendor/` directory:

```bash
uv run neko-plugin sync ../n.e.k.o_plugin_weather_helper --clean
```

The command succeeds without writing extra files when the plugin has no third-party dependencies. Do not use `requirements.txt` for plugin packages.

## Check the plugin

Run the quick check while developing:

```bash
uv run neko-plugin check ../n.e.k.o_plugin_weather_helper
```

Before publishing, run the complete check:

```bash
uv run neko-plugin check -r ../n.e.k.o_plugin_weather_helper
```

The complete check runs plugin tests, builds the installable package, and verifies that the package is intact.

To build a local package without publishing it:

```bash
uv run neko-plugin build ../n.e.k.o_plugin_weather_helper \
  --target-dir ../plugin-builds
```

## Push the project to GitHub

The CLI does not create a GitHub repository, commit your changes, or push the current branch. Create a repository named after the plugin:

```text
n.e.k.o_plugin_weather_helper
```

Then commit and push the project:

```bash
cd ../n.e.k.o_plugin_weather_helper
git add .
git commit -m "feat: first release"
git remote add origin \
  https://github.com/your-name/n.e.k.o_plugin_weather_helper
git push -u origin main
```

## Submit the plugin for review

Before the first release, open the [N.E.K.O Plugin Market](https://market.project-neko.cn), sign in, submit the GitHub repository, and wait for the plugin to be approved.

You only do this once for a plugin. The CLI does not register the plugin or submit it for review. Approval lets the Market associate later releases with this repository, so `publish` can add them automatically.

## Publish a version

Set the version in `plugin.toml`, make sure the working tree is clean, and push the current commit. Then run from the N.E.K.O source directory:

```bash
uv run neko-plugin publish ../n.e.k.o_plugin_weather_helper
```

For version `0.1.0`, the CLI checks the project, pushes tag `v0.1.0`, waits for GitHub to create the Release and package, then asks the N.E.K.O Plugin Market to read that Release.

The Market notification does not require Market credentials. Git operations still use your own GitHub credentials.

The command is safe to retry when the tag already points to the same commit. It stops instead of overwriting a tag that points to different code.

## Update an existing plugin project

Preview the standard GitHub configuration update:

```bash
uv run neko-plugin setup-repo /path/to/existing-plugin \
  --upgrade-github-actions \
  --dry-run
```

Apply it after reviewing the plan:

```bash
uv run neko-plugin setup-repo /path/to/existing-plugin \
  --upgrade-github-actions
```

This operation manages only `ruff.toml`, `.github/workflows/verify.yml`, and `.github/workflows/release.yml`. It stops without overwriting unrecognized custom content.

## Resume one publication step

Normally, rerun `publish`. Use the split modes only to recover an interrupted publication or diagnose one step:

```bash
uv run neko-plugin publish github /path/to/plugin

uv run neko-plugin publish market \
  https://github.com/owner/repository/releases/tag/v0.1.0
```

## Command summary

| Command | Use it to |
| --- | --- |
| `init` | Create a complete plugin project |
| `setup-repo` | Update standard GitHub files in an existing project |
| `sync` | Refresh third-party libraries in `vendor/` |
| `check` | Check a plugin while developing |
| `check -r` | Run tests, build, and verify before publishing |
| `build` | Build a local package without publishing |
| `publish` | Create the GitHub Release and notify the Market |
| `install` | Install a local package into explicit directories for debugging |
| `analyze` | Compare SDK and dependency compatibility for bundle candidates |

Use `uv run neko-plugin <command> --help` to see every option.

## Common failures

- **Uncommitted changes:** commit or stash them before publishing.
- **HEAD has not been pushed:** push the current branch first.
- **The standard release configuration is outdated:** run `setup-repo --upgrade-github-actions`.
- **Dependencies are missing from `vendor/`:** run `sync --clean`.
- **GitHub did not create a Release:** open the repository's Actions page and inspect the Release workflow.
