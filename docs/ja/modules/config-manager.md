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
config.save_character(name, data) # 変更を永続化
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

マネージャーは以下の順序で設定ファイルを検索します：

1. ユーザードキュメントディレクトリ（`~/Documents/N.E.K.O/`）
2. プロジェクトの `config/` ディレクトリ
3. 何も見つからない場合はデフォルトを作成

Windows ではドキュメントパスは Windows API（`SHGetFolderPath`）で解決されます。macOS/Linux では `~/Documents/` を使用します。
