# Live2D Auto Layer

Describe what this plugin does and how to configure it.

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/live2d_auto_layer
```

When publishing to the plugin market, use this GitHub repository name:

```text
n.e.k.o_plugin_live2d_auto_layer
```

From the N.E.K.O repository root:

```bash
uv run python -m plugin.neko_plugin_cli.cli check live2d_auto_layer
uv run python -m plugin.neko_plugin_cli.cli check -r live2d_auto_layer
```

## Market release

Push a tag matching `plugin.toml` version to create a GitHub Release asset:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The generated `.github/workflows/release.yml` uploads `live2d_auto_layer.neko-plugin`.
Use that GitHub Release URL when publishing a version in the plugin market.

## Entry

```toml
entry = "plugins.live2d_auto_layer:Live2dAutoLayerPlugin"
```
