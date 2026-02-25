# コードスタイル

## Python

- **Python 3.11** -- 必須。3.12 以降の機能は使用しないこと
- **型ヒント** -- 特にパブリック API では実用的な範囲で使用する
- **Async** -- FastAPI ハンドラーの I/O 操作には `async/await` を使用する
- **インポート** -- 標準ライブラリ、サードパーティ、ローカルの順
- **行の長さ** -- 厳密な制限なし、ただし妥当な範囲で（約 120 文字）

## JavaScript

- **ES6+** -- モダンな構文を使用する（アロー関数、const/let、テンプレートリテラル）
- **フレームワーク不使用** -- フロントエンドは設計上 vanilla JS を使用
- **i18n** -- すべてのユーザー向け文字列はロケールシステムを使用する

## コミットメッセージ

可能な限り Conventional Commits に従ってください：

```
feat: add voice preview for custom voices
fix: resolve WebSocket reconnection on character switch
docs: update API reference for memory endpoints
refactor: extract TTS queue logic into separate module
```

## Pull Request

- PR は単一の関心事に集中させる
- 何を変更したか、なぜ変更したかの説明を含める
- 該当する場合は関連する Issue を参照する
- `uv run pytest` が通ることを確認する
