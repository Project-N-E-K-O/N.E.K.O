# テスト

N.E.K.O. はユニットテスト、フロントエンド統合テスト、エンドツーエンドフローをカバーする包括的なテストスイートを備えています。

## セットアップ

```bash
# 依存関係をインストール
uv sync

# Playwright ブラウザをインストール（フロントエンド & e2e テスト用）
uv run playwright install
```

### テスト用 API キー

```bash
cp tests/api_keys.json.template tests/api_keys.json
# tests/api_keys.json を編集して API キーを入力
```

このファイルは gitignore されており、コミットされません。

## テストの実行

::: warning
すべてのテストコマンドは正しい Python 環境を使用するために `uv run` を使用する必要があります。
:::

```bash
# すべてのテスト（e2e を除く）
uv run pytest tests/ -s

# ユニットテストのみ
uv run pytest tests/unit -s

# フロントエンド統合テスト
uv run pytest tests/frontend -s

# エンドツーエンドテスト（明示的なフラグが必要）
uv run pytest tests/e2e --run-e2e -s
```

## テスト構成

```
tests/
├── conftest.py                # 共有フィクスチャ（サーバーライフサイクル、ページ、データディレクトリ）
├── api_keys.json              # API キー（gitignore 対象）
├── unit/
│   ├── test_providers.py      # マルチプロバイダー API 接続
│   ├── test_text_chat.py      # OmniOfflineClient テキスト + ビジョンチャット
│   ├── test_voice_session.py  # OmniRealtimeClient WebSocket セッション
│   └── test_video_session.py  # OmniRealtimeClient ビデオ/画面ストリーミング
├── frontend/
│   ├── test_api_settings.py   # API キー設定ページ
│   ├── test_chara_settings.py # キャラクター管理ページ
│   ├── test_memory_browser.py # メモリブラウザページ
│   ├── test_voice_clone.py    # ボイスクローンページ
│   └── test_emotion.py        # Live2D + VRM 感情マネージャーページ
├── e2e/
│   └── test_e2e_full_flow.py  # 完全なアプリジャーニー（8 ステージ）
├── utils/
│   ├── llm_judger.py          # LLM ベースのレスポンス品質評価器
│   └── audio_streamer.py      # オーディオストリーミングテストユーティリティ
└── test_inputs/
    ├── script.md              # オーディオテスト用の録音スクリプト
    └── screenshot.png         # ビジョンテスト用のテストスクリーンショット
```

## テストカテゴリ

### ユニットテスト (`tests/unit/`)

コアバックエンドコンポーネントを分離してテストします：

- **プロバイダー接続**: サポートされているすべてのプロバイダーへの API 接続を検証
- **テキストチャット**: テキストとビジョン入力で `OmniOfflineClient` をテスト
- **ボイスセッション**: `OmniRealtimeClient` の WebSocket 接続をテスト
- **ビデオセッション**: 画面共有とビデオストリーミングをテスト

### フロントエンドテスト (`tests/frontend/`)

Playwright を使用して Web UI ページをテストします：

- **API 設定**: キー入力、プロバイダー切り替え、保存/読み込み
- **キャラクター設定**: CRUD 操作、性格編集
- **メモリブラウザ**: メモリファイルの一覧、編集、保存
- **ボイスクローン**: アップロードインターフェース、ボイスプレビュー
- **感情マネージャー**: Live2D と VRM の感情マッピング

### E2E テスト (`tests/e2e/`)

完全なシステムを実行する完全なユーザージャーニーテストです。以下の理由により `--run-e2e` フラグが必要です：

- 実際のサーバープロセスを起動する
- 実際の API 呼び出しを行う
- 実行に長い時間がかかる

## テストユーティリティ

### LLM Judger (`tests/utils/llm_judger.py`)

レスポンスの品質を評価する LLM ベースの評価器です。e2e テストでキャラクターの応答が文脈的に適切で、キャラクターに沿っており、事実として妥当であることを検証するために使用されます。

### Playwright パターン

フロントエンドテストは **偵察後アクション** パターンに従います：

1. ページに移動する
2. `networkidle` を待つ（JS レンダリングコンテンツには重要）
3. レンダリングされた DOM を検査する
4. 検出したセレクターを使用してアクションを実行する

```python
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.goto('http://localhost:48911')
    page.wait_for_load_state('networkidle')
    # ページとの安全なインタラクションが可能
```

::: tip
CI 互換性のために常に Chromium をヘッドレスモードで起動してください。動的コンテンツを検査する前に `networkidle` を待ってください。
:::

## 新しいテストの作成

1. テストファイルを適切なサブディレクトリに配置します（`unit/`、`frontend/`、`e2e/`）
2. pytest マーカーを使用します: `@pytest.mark.unit`、`@pytest.mark.frontend`、`@pytest.mark.e2e`
3. サーバーライフサイクルとページセットアップには `conftest.py` の共有フィクスチャを使用します
4. 既存の命名規則に従います: `test_<module>_<feature>.py`
