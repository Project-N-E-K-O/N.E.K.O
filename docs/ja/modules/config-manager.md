# Config Manager

**ファイル:** `utils/config_manager.py`（約1500行）

`ConfigManager` はすべての設定の読み込み、バリデーション、永続化を集約するシングルトンです。

## アクセス

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

## 主要メソッド

### キャラクターデータ

```python
config.get_character_data()      # 全キャラクター
config.load_characters()          # ディスクから再読み込み
config.save_characters(data)      # キャラクター辞書全体を永続化（同期、イベントループをブロック）
config.asave_characters(data)     # 非同期版（async パスではこちらを使用）
```

### API 設定

```python
config.get_core_config()              # API キー、プロバイダー、エンドポイント
config.get_model_api_config(model_type)  # 特定のモデル役割の設定
```

### ファイルシステム

```python
config.get_workshop_path()        # Steam Workshop ディレクトリ
config.ensure_live2d_directory()  # Live2D モデルディレクトリの作成
config.ensure_vrm_directory()     # VRM モデルディレクトリの作成
```

## 設定の解決

Config Manager は[優先順位チェーン](/ja/config/config-priority)を実装しています：

1. 環境変数を確認（`NEKO_*`）
2. ユーザー設定ファイルを確認（`core_config.json`）
3. API プロバイダー定義を確認（`api_providers.json`）
4. コードのデフォルト値にフォールバック（`config/__init__.py`）

## ファイル検出

マネージャーは以下の順序でランタイムデータのルートディレクトリ（`config/`、`memory/` などを格納）を検索します：

1. 各プラットフォームの標準アプリデータディレクトリ：
   - Windows：`%LOCALAPPDATA%\N.E.K.O\`（例：`C:\Users\<あなた>\AppData\Local\N.E.K.O\`）
   - macOS：`~/Library/Application Support/N.E.K.O/`
   - Linux：`$XDG_DATA_HOME/N.E.K.O/`（未設定時は `~/.local/share/N.E.K.O/`）
2. レガシーな場所（一度きりのデータインポート / 最終フォールバックにのみ使用）：ユーザードキュメントディレクトリ（`~/Documents/N.E.K.O/`、Windows では Windows API `SHGetFolderPath` で解決）、実行ファイルのあるディレクトリ（凍結ビルド）、およびカレントワーキングディレクトリ。
3. 使用可能なものが見つからない場合は、アプリデータディレクトリ配下にデフォルトを作成します。

ソースモードでは、ソースツリーに同梱されるプロジェクトの `config/` ファイルは引き続きリポジトリルートを基準に解決されます。
