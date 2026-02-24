# ベストプラクティス

## コード構成

初期化、ヘルパー、パブリックエントリーポイントを分離してください：

```python
@neko_plugin
class WellOrganizedPlugin(NekoPluginBase):
    def __init__(self, ctx):
        super().__init__(ctx)
        self._initialize()

    # プライベートヘルパー
    def _initialize(self):
        """リソースのセットアップ"""
        pass

    def _helper(self, data):
        """内部ロジック"""
        pass

    # パブリックエントリーポイント
    @plugin_entry(id="process")
    def process(self, data: str, **_):
        result = self._helper(data)
        return {"result": result}
```

## エラーハンドリング

常にエラーを適切に処理し、構造化されたレスポンスを返してください：

```python
@plugin_entry(id="task")
def task(self, param: str, **_):
    try:
        if not param:
            raise ValueError("param is required")
        result = self._do_work(param)
        return {"success": True, "result": result}
    except ValueError as e:
        self.logger.warning(f"Validation error: {e}")
        return {"success": False, "error": str(e)}
    except Exception as e:
        self.logger.exception(f"Unexpected error: {e}")
        return {"success": False, "error": "Internal error"}
```

## ロギング

適切なログレベルを使用してください：

| レベル | 使用場面 |
|--------|---------|
| `debug` | 詳細な診断情報 |
| `info` | 通常動作のマイルストーン |
| `warning` | 予期しないが処理された状況 |
| `error` | 注意が必要なエラー |
| `exception` | スタックトレース付きエラー |

## ステータス更新

長時間実行されるオペレーション中は進捗を報告してください：

```python
@plugin_entry(id="batch_job")
def batch_job(self, items: list, **_):
    total = len(items)
    for i, item in enumerate(items):
        self._process(item)
        self.report_status({
            "status": "processing",
            "progress": (i + 1) / total * 100,
            "message": f"Processing {i+1}/{total}"
        })

    self.report_status({"status": "completed", "progress": 100})
    return {"processed": total}
```

## 入力バリデーション

自動バリデーションのために `input_schema` を JSON Schema で定義してください：

```python
@plugin_entry(
    id="validated",
    input_schema={
        "type": "object",
        "properties": {
            "email": {"type": "string", "format": "email"},
            "age": {"type": "integer", "minimum": 0, "maximum": 150}
        },
        "required": ["email", "age"]
    }
)
def validated(self, email: str, age: int, **_):
    # 入力はフレームワークにより既にバリデーション済み
    return {"email": email, "age": age}
```

## 作業ディレクトリ

プラグイン固有のファイルのベースとして `ctx.config_path.parent` を使用してください：

```python
work_dir = self.ctx.config_path.parent / "data"
work_dir.mkdir(exist_ok=True)
```
