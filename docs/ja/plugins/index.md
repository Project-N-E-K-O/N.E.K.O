# プラグインシステムの概要

N.E.K.O. のプラグインシステムは、**プロセス隔離**と**非同期 IPC** に基づく Python プラグインフレームワークです。package type は、製品機能向けの **Plugin** と外部プロトコルブリッジ向けの **Adapter** の 2 種類です。旧 **Extension** package type は削除され、`PluginRouter` は通常 Plugin 内部でのみ利用できます。

## アーキテクチャ

```
┌────────────────────────────────────────────────────┐
│              Main Process (Host)                   │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Host (core/)                        │  │
│  │   - プラグインライフサイクル管理              │  │
│  │   - バスシステム（メモリ、イベント、メッセージ）│  │
│  │   - ZMQ IPC トランスポート                   │  │
│  └──────────────────────────────────────────────┘  │
│  ┌──────────────────────────────────────────────┐  │
│  │   Plugin Server (server/)                    │  │
│  │   - HTTP API エンドポイント (FastAPI)         │  │
│  │   - プラグインレジストリ                      │  │
│  │   - メッセージキュー                          │  │
│  └──────────────────────────────────────────────┘  │
└────────────────────┬───────────────────────────────┘
                     │ ZMQ IPC
      ┌──────────────┼──────────────┐
      ▼              ▼              ▼
  Plugin A       Plugin B       Adapter D
  (プロセス)     (プロセス)      (プロセス)
```

## パッケージ種別

| パラダイム | インポート元 | ユースケース | 実行方法 |
|----------|------------|----------|-------------|
| **Plugin** | `plugin.sdk.plugin` | 独立した機能（検索、リマインダーなど） | 別プロセス |
| **Adapter** | `plugin.sdk.adapter` | 外部プロトコル（MCP、NoneBot）を内部プラグイン呼び出しにブリッジ | ゲートウェイパイプライン付き別プロセス |

### どれを使うべきか？

- **「新しいスタンドアロン機能を追加したい」** → **Plugin** を使用
- **「既存機能の周辺にコマンドを追加したい」** → 通常の **Plugin** を使う。host を所有しコードが大きい場合は host 内で `PluginRouter` を使う
- **「MCP/NoneBot/外部プロトコルの呼び出しを受け付けてプラグインにルーティングしたい」** → **Adapter** を使用

> **Plugin** から始めてください。旧 Extension は Router を所有する Plugin に統合するか、独立した Plugin に変換します。

## 主な機能

- **プロセス隔離** — Plugin と Adapter は別プロセスで実行されます
- **非同期サポート** — 同期・非同期の両方のエントリーポイントに対応
- **Result 型** — 型安全なエラーハンドリングのための `Ok`/`Err`（通常フローで例外を使用しない）
- **フックシステム** — AOP のための `@before_entry`、`@after_entry`、`@around_entry`、`@replace_entry`
- **プラグイン間呼び出し** — プラグイン間通信のための `self.plugins.call_entry("other_plugin:entry_id")`
- **システム情報** — ホストシステムのメタデータを照会するための `self.system_info`
- **プラグインストア** — 永続的なキーバリューストレージのための `PluginStore`
- **バスシステム** — `self.bus` は `messages`、`events`、`lifecycle`、`conversations`、`memory` から host state を読みます。`watch()` を使えるのは最初の 3 namespace だけで、`conversations` と `memory` は read-only snapshot です。replayable watcher chain は `get()` → structured `filter(field=value, ...)` → `sort(by=...)` → `limit()` → `watch()` を使い、delta は `add`、`del`、`change` だけを購読します。publish/emit API はありません。最近の memory record は `self.bus.memory.get(...)`、semantic query は `await self.ctx.query_memory(...)` を使います。
- **動的エントリー** — 実行時にエントリーポイントを登録/解除
- **静的 UI** — プラグインディレクトリから Web UI を配信
- **ライフサイクルフック** — `startup`、`shutdown`、`reload`、`freeze`、`unfreeze`、`config_change`
- **タイマータスク** — `@timer_interval` による定期実行
- **メッセージハンドラー** — ホストシステムからのメッセージに反応

## プラグインディレクトリ構造

```
plugin/plugins/
└── my_plugin/
    ├── __init__.py      # プラグインコード（エントリーポイント）
    ├── plugin.toml      # プラグイン設定
    ├── config.json      # オプション：カスタム設定
    ├── data/            # オプション：ランタイムデータディレクトリ
    └── static/          # オプション：Web UI ファイル
```

## クイックリンク

- [クイックスタート](./quick-start) — 5 分で最初のプラグインを作成
- [v0.9 移行](./migration-v0.9) — 削除済み API と正確な移行先
- [SDK リファレンス](./sdk-reference) — ベースクラス、コンテキスト API、Result 型
- [デコレーター](./decorators) — 利用可能なすべてのデコレーター
- [サンプル](./examples) — 完全に動作するサンプル
- [応用トピック](./advanced) — Router 構成、Adapter、プラグイン間呼び出し、フック
- [LLM ツール呼び出し](./tool-calling) — LLM が会話中に呼び出せるツールをプラグインから登録する
- [ベストプラクティス](./best-practices) — エラーハンドリング、テスト、コード構成
