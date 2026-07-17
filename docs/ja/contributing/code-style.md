
# コードスタイル

強制規則の正本は `pyproject.toml`、`.agent/rules/neko-guide.md`、CI スクリプトです。

## Python

- 対象は Python 3.11 です。
- Python コマンドには `uv run` を使います。
- 非同期リクエスト経路をブロックせず、必要に応じてファイルシステム／CPU のブロッキング処理をイベントループ外へ移します。
- `scripts/check_module_layering.py` が検査するモジュール階層を維持します。
- 重い SDK を起動時の import chain に入れません。
- `loguru`、`structlog`、`logbook`、`tkinter` は追加禁止で、CI が拒否します。
- 生の会話やプライバシーに関わるテキストは `print` のみを使い、永続的なプロジェクト logger へ送りません。

`uv run ruff check .` と関連するリポジトリ検査を実行します。

## フロントエンド

フロントエンドは、静的／Jinja JavaScript、単一の React チャットアプリ、Vue プラグインマネージャーからなる混合構成です。振る舞いを複製せず、所有実装を編集します。

- チャット UI／ロジックは `frontend/react-neko-chat/` にあります。
- `index.html` と Electron の `chat.html` は同じ React コンポーネントをマウントします。
- 廃止済みの `#chat-container` に新しい挙動を追加しません。
- ブラウザーの `/` と Electron の `/chat`、`/subtitle` などを両方考慮します。
- i18n を使用し、8 locale を同期更新します。

## Provider の対称性

Provider／backend／feature の経路は構造的に対称でなければなりません。同種 provider の一つを分割したり lifecycle／設定経路を追加したりする場合は、対応する peer も確認し、理由のない例外経路を残しません。

## API パス

バックエンドの decorator とフロントエンド呼び出しでは、API リソース末尾にスラッシュを付けません。

- 正：`/api/characters`
- 誤：`/api/characters/`

prefix 付き `APIRouter` では、文字どおりのサイトルートを除き `@router.get("")` を使います。CI は前後両方を検査します。

## Prompt と i18n

多言語 prompt table は所有する `config/prompts_*.py` に置き、prompt budget／temperature の検査に従います。system prompt の翻訳では `======以上为` をそのまま保持します。

## Commit と PR

一つの commit／PR は一つのまとまった関心事に絞ります。振る舞いと検証を説明し、実行していないテストや platform を主張しません。
