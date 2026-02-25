# 設定ファイル

設定ファイルはユーザーのドキュメントディレクトリ内の `N.E.K.O/` に保存されます。

## ファイルの場所

| ファイル | 用途 |
|----------|------|
| `core_config.json` | API キー、プロバイダー選択、カスタムエンドポイント |
| `characters.json` | キャラクター定義とパーソナリティデータ |
| `user_preferences.json` | UI 設定、モデル選択 |
| `voice_storage.json` | カスタム音声設定 |
| `workshop_config.json` | Steam Workshop 設定 |

## `core_config.json`

主要なランタイム設定ファイルです。

```json
{
  "coreApiKey": "",
  "coreApi": "qwen",
  "assistApi": "qwen",
  "assistApiKeyQwen": "",
  "assistApiKeyOpenai": "",
  "assistApiKeyGlm": "",
  "assistApiKeyStep": "",
  "assistApiKeySilicon": "",
  "assistApiKeyGemini": "",
  "mcpToken": "",
  "agentModelUrl": "",
  "agentModelId": "",
  "agentModelApiKey": ""
}
```

## `characters.json`

すべてのキャラクターとマスター（オーナー）プロフィールを定義します。

```json
{
  "master": {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥"
  },
  "catgirl": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "T酱, 小T",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  }
}
```

キャラクターフィールドは柔軟で、任意のキーと値のペアを追加でき、キャラクターのコンテキストに含まれます。

## ファイル検出

`ConfigManager` クラス（`utils/config_manager.py`）がファイル検出を処理します：

1. ユーザーのドキュメントディレクトリを確認（`~/Documents/N.E.K.O/`）
2. プロジェクトの `config/` ディレクトリにフォールバック
3. 存在しない場合はデフォルトファイルを作成

Windows ではドキュメントディレクトリは Windows API で解決されます。macOS/Linux では `~/Documents/` を使用します。
