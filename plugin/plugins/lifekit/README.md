# 生活助手

基于地理位置的多功能生活服务。天气查询、逐小时预报、出行建议、路线规划、常用地点管理、附近 POI 搜索。

## Development

This repository is meant to live at:

```text
N.E.K.O/plugin/plugins/lifekit
```

From the N.E.K.O `plugin/` directory:

```bash
uv run python neko_plugin_cli/cli.py doctor lifekit
uv run python neko_plugin_cli/cli.py release-check lifekit
```

## Entry

```toml
entry = "plugin.plugins.lifekit:LifeKitPlugin"
```
