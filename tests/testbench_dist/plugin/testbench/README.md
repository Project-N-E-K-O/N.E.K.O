# N.E.K.O. Testbench driver plugin

N.E.K.O. plugin that starts Testbench FastAPI and prefers an independent
pywebview window (system browser as fallback). Mode A = single shell process;
Mode B = embedded uvicorn when no script Python is available.

## Market repository

When publishing to the plugin market, use this GitHub repository name:

```text
n.e.k.o_plugin_testbench
```

Remote: https://github.com/TL0SR2/n.e.k.o_plugin_testbench

## Development

From the N.E.K.O repository root:

```bash
uv run neko-plugin check -r tests/testbench_dist/plugin/testbench
uv run python tests/testbench_dist/scripts/build_plugin.py
```

`bundled/` is generated at release time from `tests/testbench/` and is not committed.

## Market release

After first-time market approval:

```bash
uv run neko-plugin publish tests/testbench_dist/plugin/testbench
```

See [Plugin CLI — 把插件发布到 N.E.K.O 插件市场](https://project-neko.online/zh-CN/plugins/cli).

维护者详见 N.E.K.O 主仓：[docs/PLUGIN_MARKET_REPO.md](../../docs/PLUGIN_MARKET_REPO.md)（独立仓库、双端同步、发布检查清单）。
