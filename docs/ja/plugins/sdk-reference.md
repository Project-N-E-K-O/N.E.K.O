# SDK リファレンス

## NekoPluginBase

すべてのプラグインは `NekoPluginBase` を継承する必要があります。

```python
from plugin.sdk.base import NekoPluginBase

class MyPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
```

### メソッド

#### `get_input_schema() → dict`

プラグインの入力 JSON Schema を返します。デフォルトではクラス属性から読み取ります。動的なスキーマにはオーバーライドしてください。

#### `report_status(status: dict) → None`

プラグインのステータスをメインプロセスに報告します。

```python
self.report_status({
    "status": "running",
    "progress": 50,
    "message": "Processing..."
})
```

#### `collect_entries() → dict`

すべてのエントリーポイント（`@plugin_entry` で装飾されたメソッド）を収集します。システムにより自動的に呼び出されます。

## PluginContext

プラグインのコンストラクタに渡される `ctx` オブジェクトです。

### プロパティ

| プロパティ | 型 | 説明 |
|-----------|------|------|
| `ctx.plugin_id` | `str` | プラグイン識別子 |
| `ctx.config_path` | `Path` | `plugin.toml` へのパス |
| `ctx.logger` | `Logger` | ロガーインスタンス |

### メソッド

#### `ctx.update_status(status: dict) → None`

メインプロセスのプラグインステータスを更新します。

#### `ctx.push_message(...) → None`

メインシステムにメッセージをプッシュします。

```python
ctx.push_message(
    source="my_feature",          # メッセージソース識別子
    message_type="text",          # "text" | "url" | "binary" | "binary_url"
    description="Task complete",  # 人間が読める説明
    priority=5,                   # 0-10（0=低、10=緊急）
    content="Result text",        # text/url タイプ用
    binary_data=b"...",           # binary タイプ用
    binary_url="https://...",     # binary_url タイプ用
    metadata={"key": "value"}    # 追加メタデータ
)
```

### メッセージタイプ

| タイプ | 用途 |
|--------|------|
| `text` | プレーンテキストメッセージ |
| `url` | URL リンク |
| `binary` | 小さなバイナリデータ（直接送信） |
| `binary_url` | 大きなファイル（URL で参照） |

### 優先度レベル

| 範囲 | レベル | 用途 |
|------|--------|------|
| 0-2 | 低 | 情報メッセージ |
| 3-5 | 中 | 一般的な通知 |
| 6-8 | 高 | 重要な通知 |
| 9-10 | 緊急 | 即座の対応が必要 |
