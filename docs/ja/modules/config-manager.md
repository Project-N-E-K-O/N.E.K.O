# Config Manager

**ファイル:** `utils/config_manager.py`（約1490行）

`ConfigManager` はすべての設定の読み込み、バリデーション、永続化を集約するシングルトンです。

## アクセス

```python
from utils.config_manager import get_config_manager

config = get_config_manager()
```

## 主要メソッド

### キャラクターデータ

```python
config.get_character_data()      # 全キャラクター（master_name、her_name、stores などを返す）
config.load_characters()          # ディスクから再読み込み
config.save_characters()          # ディスクに永続化
```

### API 設定

```python
config.get_core_config()                     # API キー、プロバイダー、エンドポイント
config.get_model_api_config(model_type)      # 特定の役割の設定（realtime/tts_custom/agent/etc.）
config.is_agent_api_ready()                  # エージェント API が設定済みか確認
```

### ボイス管理

```python
config.load_voice_storage()                  # 全ボイスデータの読み込み
config.save_voice_storage()                  # ボイスデータの永続化
config.get_voices_for_current_api()          # 現在の API プロバイダーのボイス
config.save_voice_for_current_api(voice)     # 現在のプロバイダーにボイスを保存
config.delete_voice_for_current_api(voice_id) # ボイスの削除
```

### ファイルシステム

```python
config.get_workshop_path()        # Steam Workshop ディレクトリ
config.ensure_live2d_directory()  # Live2D モデルディレクトリの作成
config.ensure_vrm_directory()     # VRM モデルディレクトリの作成
```

### マイグレーション

```python
config.migrate_config_files()     # 旧パスからの設定ファイルの移行
config.migrate_memory_files()     # 旧パスからのメモリファイルの移行
```

## ディレクトリ構造

Config Manager は以下のディレクトリを解決・管理します：

| プロパティ | 用途 |
|-----------|------|
| `docs_dir` | ユーザーのドキュメントディレクトリ |
| `app_docs_dir` | `~/Documents/N.E.K.O/` |
| `config_dir` | 設定ファイル |
| `memory_dir` | メモリストア |
| `live2d_dir` | Live2D モデル |
| `vrm_dir` | VRM モデル |
| `vrm_animation_dir` | VRM アニメーションファイル |
| `workshop_dir` | Steam Workshop コンテンツ |
| `chara_dir` | キャラクターデータ |
| `project_config_dir` | プロジェクトレベルの設定 |
| `project_memory_dir` | プロジェクトレベルのメモリ |

## 設定の解決

Config Manager は[優先順位チェーン](/ja/config/config-priority)を実装しています：

1. 環境変数を確認（`NEKO_*`）
2. ユーザー設定ファイルを確認（`core_config.json`）
3. API プロバイダー定義を確認（`api_providers.json`）
4. コードのデフォルト値にフォールバック（`config/__init__.py`）

## ファイル検出

マネージャーはユーザードキュメントディレクトリを検索します：

- **Windows**: `SHGetFolderPathW`（CSIDL_PERSONAL）→ レジストリフォールバック → `USERPROFILE` フォールバック。同一ドライブ上の「文档」/「Documents」/「My Documents」候補も検索。
- **macOS/Linux**: `~/Documents/`

`_get_project_root()` メソッドは、バンドルされたリソースにアクセスするためにプロジェクトルートディレクトリを検出します。
