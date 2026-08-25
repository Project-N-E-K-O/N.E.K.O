# 生活助手

基于地理位置的多功能生活服务。天气查询、逐小时预报、出行建议、路线规划、常用地点管理、附近 POI 搜索。

## Location risk policy

- Read-only entries choose one deterministic primary location when several usable candidates remain, execute the query, and disclose the assumption and alternatives in the summary.
- Nearby results always come from one search center; candidates from different places are never mixed.
- If no usable location exists, read-only entries return `status=unavailable` instead of a blocking clarification.
- Location and configuration writes require an explicit confirmation flag; read-only requests never do.

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/lifekit
```

From the N.E.K.O `plugin/` directory:

```bash
uv run python neko_plugin_cli/cli.py check lifekit --strict
uv run python neko_plugin_cli/cli.py check lifekit --release
```

## Entry

```toml
entry = "plugin.plugins.lifekit:LifeKitPlugin"
```
