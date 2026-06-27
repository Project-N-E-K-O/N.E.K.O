# 設定ファイル

設定ファイルは、現在のプラットフォームの標準アプリケーションデータディレクトリ内の `N.E.K.O/` サブディレクトリに保存されます。Windows は `%LOCALAPPDATA%\N.E.K.O\`、macOS は `~/Library/Application Support/N.E.K.O/`、Linux は `$XDG_DATA_HOME/N.E.K.O/`（未設定の場合は `~/.local/share/N.E.K.O/`）です。

## ファイルの場所

| ファイル | 用途 |
|----------|------|
| `core_config.json` | API キー、プロバイダー選択、カスタムエンドポイント |
| `characters.json` | キャラクター定義とパーソナリティデータ |
| `user_preferences.json` | UI 設定、モデル選択 |
| `voice_storage.json` | カスタム音声設定 |
| `workshop_config.json` | Steam Workshop 設定 |
| `tutorial_prompt_config.json` | チュートリアル/オンボーディングプロンプトのしきい値とフロー状態 |

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

すべてのキャラクターと所有者プロフィールを定義します。

```json
{
  "主人": {
    "档案名": "哥哥",
    "性别": "男",
    "昵称": "哥哥"
  },
  "猫娘": {
    "小天": {
      "性别": "女",
      "年龄": 15,
      "昵称": "T酱, 小T",
      "live2d": "mao_pro",
      "voice_id": "",
      "system_prompt": "..."
    }
  },
  "当前猫娘": "小天"
}
```

トップレベルのキーは中国語であり、コードはこれらのキー名にそのまま依存しています：`主人`（所有者プロフィール）、`猫娘`（名前をキーとするキャラクターのマップ）、`当前猫娘`（現在アクティブなキャラクターの名前）。`master` や `catgirl` などの英語キーは認識されず、無視されます。

キャラクターフィールドは柔軟で、任意のキーと値のペアを追加でき、キャラクターのコンテキストに含まれます。

## ファイル検出

`ConfigManager` クラス（`utils/config_manager.py`）がファイル検出を処理します：

1. 現在のプラットフォームの標準アプリケーションデータディレクトリ（Windows `%LOCALAPPDATA%`、macOS `~/Library/Application Support`、Linux `$XDG_DATA_HOME` または `~/.local/share`）を優先し、その下に `N.E.K.O/` を作成/読み込みます。
2. 標準ディレクトリが利用できない場合は、レガシーな場所（`~/Documents/N.E.K.O/`、実行ファイル自身のディレクトリ、現在の作業ディレクトリなど）にフォールバックします。これらは古いデータの読み込み/インポートにのみ使用されます。
3. プロジェクト付属の `config/` ディレクトリにフォールバックします。
4. 存在しない場合はデフォルトファイルを作成します。

レガシーな `~/Documents/N.E.K.O/` パス（Windows では Windows API `SHGetFolderPathW` で解決）は、現在ではレガシーデータのインポート候補にすぎず、主要な保存場所ではありません。
