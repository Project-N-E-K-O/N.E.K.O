# 一言 · Hitokoto

一个内置于 N.E.K.O 的轻量 Hitokoto 插件。它通过公开 HTTPS API
`https://v1.hitokoto.cn/` 获取随机一言和每日一句，无需 API Key。

## 能力

- `random_quote`：随机获取一言，可按官方 `a` 至 `l` 类型筛选。
- `daily_quote`：缓存启用时，同一个本地日历日内始终返回同一句。
- 每日首次聊天分享：每天观察到第一条聊天消息时，把今日一言注入对话，
  由当前角色用自己的口吻自然分享；插件原文不会直接显示为聊天气泡。
- 管理面板：调整默认类型、请求超时、最大句长、每日缓存和首次聊天分享，
  并可立即试用、测试 API、恢复默认值或清除每日缓存。

随机一言和今日一言同时注册为普通插件入口与对话 LLM 工具。管理操作只注册为
插件入口，不会占用角色的工具列表。

## 类型码

| 代码 | 类型 | 代码 | 类型 |
| --- | --- | --- | --- |
| `a` | 动画 | `g` | 其他 |
| `b` | 漫画 | `h` | 影视 |
| `c` | 游戏 | `i` | 诗词 |
| `d` | 文学 | `j` | 网易云 |
| `e` | 原创 | `k` | 哲学 |
| `f` | 来自网络 | `l` | 抖机灵 |

空类型表示全类型随机。省略 `random_quote` 的类型参数时，会使用管理面板保存的
默认类型。

## 数据与隐私

插件仅向 Hitokoto API 发送固定的编码、字符集、类型和最大句长参数。它不上传
聊天内容，不需要账号或密钥。日志不会记录一言正文或用户输入，只记录 quote
ID、UUID、类型、长度、缓存命中状态与安全的失败类型。

设置、每日缓存和问候尝试日期保存在 N.E.K.O `PluginStore` 中。存储损坏或不可用
时插件会安全降级；进程内仍会避免同一天重复问候。关闭每日缓存后不会读取或写入
每日一句缓存。

## 本地验证

```bash
uv run python -m pytest plugin/tests/unit/plugins/test_hitokoto.py -x -q
uv run python -m pytest plugin/tests/integration/test_neko_plugin_cli_repo_plugins.py -x -q
uv run python -m plugin.neko_plugin_cli check plugin/plugins/hitokoto
```
