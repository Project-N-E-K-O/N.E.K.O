# AGENTS.md — {plugin_id}

This directory is a N.E.K.O plugin: its own Git repository and its editable
source. `plugin.toml` declares the plugin identity; `[plugin].entry` points at
the Python class the host loads.

## Agent skill

The `neko-plugin` skill in `.agents/skills/neko-plugin/` is the entry point for
creating, modifying, debugging, and reviewing this plugin. Read its `SKILL.md`
before changing plugin code, and follow the references it points to. The skill
is a snapshot taken when this plugin was scaffolded; the N.E.K.O repository's
`.agent/skills/neko-plugin/` is the maintained source.

## Boundaries

- Edit only inside this directory. Treat the N.E.K.O checkout (when present)
  as read context; platform changes need an explicit request.
- Runtime-triggered `@plugin_entry` handlers must be `async def` and return
  `Ok(...)` / `Err(...)`.
- Keep `plugin.toml`'s `[plugin].id` and `[plugin].entry` stable; they are the
  plugin's identity.
- Declare Python dependencies in `pyproject.toml`, then materialize them with
  `neko-plugin sync {plugin_id} --clean`; never add `requirements.txt`.

## Commands

In a N.E.K.O source checkout, run from the repository root:

```bash
uv run neko-plugin check {plugin_id}
uv run neko-plugin build {plugin_id}
```

In the packaged app, use the plugin workbench (插件制作工作台) in the plugin
manager: register this directory, then check, reload, and build from its
detail page. Run the plugin-local tests with:

```bash
uv run python -m pytest tests -q
```