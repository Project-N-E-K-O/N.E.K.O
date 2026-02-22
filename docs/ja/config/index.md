# 設定の概要

N.E.K.O. は複数のソースによるレイヤード設定システムを採用しています。設定値は優先度の高い順に解決されます。

## 優先順位チェーン

1. **環境変数**（最高優先） — `NEKO_*` プレフィックス
2. **ユーザー設定ファイル** — `core_config.json`、`user_preferences.json`
3. **API プロバイダー設定** — `api_providers.json`
4. **コードのデフォルト値**（最低優先） — `config/__init__.py`

## クイックリファレンス

| 設定項目 | 設定場所 |
|----------|----------|
| API キーとプロバイダー | [環境変数](./environment-vars) または Web UI（`/api_key`） |
| 設定ファイルの場所 | [設定ファイル](./config-files) |
| 利用可能な AI プロバイダー | [API プロバイダー](./api-providers) |
| タスクごとのモデル選択 | [モデル設定](./model-config) |
| オーバーライドの仕組み | [設定の優先順位](./config-priority) |

## Web UI による設定

N.E.K.O. を設定する最も簡単な方法は Web UI を使用することです：

- **API キー：** `http://localhost:48911/api_key`
- **キャラクター設定：** `http://localhost:48911/chara_manager`
- **モデル管理：** `http://localhost:48911/model_manager`

Web UI で行った変更は、適切な設定ファイルに自動的に保存されます。
