
# 開発者向け注意事項

以下は現在のリポジトリ契約です。過去の障害詳細は、陳腐化しやすい model／version 表として複製せず、所有するルール、テスト、設計記録に残します。

## 必須規則

### Python は uv 経由

```bash
uv run python launcher.py
uv run pytest
uv run ruff check .
```

プロジェクト Python を裸の `python`、`pytest`、一時的な `pip` で実行・記述しません。

### 8 locale の i18n

ランタイム locale は次のとおりです。

```text
en, ja, ko, zh-CN, zh-TW, ru, pt, es
```

ユーザー向け i18n の変更では `static/locales/` の全ファイルを更新します。プラグインマネージャーの locale には独自の同期グループがあります。CI が同期差分を検査します。

### プライバシーに関わる出力

生の会話やユーザーの機密テキストには `print` だけを使い、プロジェクト `logger` へ送りません。生の機密内容を含まないシステムイベントは、設定済み logger を利用できます。

### Prompt ウォーターマーク

system prompt の翻訳や並べ替えでも `======以上为` を保持します。

### 構造の対称性

Provider／backend／feature は構造的に対になっています。一つの経路を分割、改名、設定、パッケージ化するときは peer provider を確認します。

## ランタイム境界

- ブラウザー開発は `/`、単一ページ／ウィンドウ、既定 port 48911 です。
- Electron は `/chat`、`/subtitle` などの個別 route／window を読み込みます。
- 静的 path、初期化、IPC、build output は両方で動作する必要があります。
- `frontend/react-neko-chat/` が唯一の chat 実装です。`index.html` と `chat.html` は `#react-chat-window-root` にマウントします。
- 旧 `#chat-container` は非表示・廃止済みで、`app-chat-adapter.js` が旧 `appendMessage()` 呼び出しを bridge します。

## バックエンド境界

- API リソース path は `/` で終えず、proxy 配下の Starlette redirect を避けます。
- async handler でブロッキング処理を直接実行しません。
- 起動時の同期 config write を async 経路でも使う場合は、対応する async `a*` interface を提供します。
- main package の layering と `main_logic/core/` facade 契約は CI が検査します。
- 制限対象の LLM 構築／呼び出しへ `temperature=` を渡さず、output、timeout、input の budget を監視します。

## メモリ

会話イベント永続化、projection、recall candidate、evidence／reflection、persona、maintenance queue は別レイヤーです。変更前に[メモリシステム](/ja/architecture/memory-system)と実装済み設計記録を読んでください。プラグイン内の 1 時間コンテキストを semantic memory recall と説明しないでください。

## フロントエンド境界

このプロジェクトは「vanilla JavaScript のみ」ではありません。静的／Jinja JavaScript、React chat、Vue plugin manager を含みます。所有 subtree とテストを確認します。固定 delay で DOM ready を推測せず、既存の lifecycle／event に従います。

## Steam とパッケージング

achievement と cloud state は外部に不可逆な影響を与えることがあります。既存の test hook と staging flow を使い、実アカウントで破壊的挙動を安易に試しません。パッケージング変更では [Nuitka パッケージング](./nuitka-packaging)と現在の build workflow に従います。

## 検証

最小の関連テストから始め、リスクに応じてテスト／ビルドを広げます。静的ゲートの正本は `.github/workflows/analyze.yml` です。plugin test、docs build、desktop package、Docker には個別 workflow があります。
