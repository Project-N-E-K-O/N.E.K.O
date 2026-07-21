# Memory API

**プレフィックス:** `/api/memory`

これは、メモリブラウザーおよび設定画面向けにメインサーバーが提供する API です。最近のメモリの編集、メモリ機能の切り替え、キャラクターメモリの名前変更、ユーザーが明示的に開始する旧ストレージのクリーンアップを扱います。プロセス内部の [Memory Server API](/ja/api/memory-server) を汎用的にプロキシするものではありません。

すべてのルートは末尾のスラッシュなしで定義されています。クラウドストレージがメンテナンス中または読み取り専用の場合、書き込み操作は `409` を返すことがあります。

## エンドポイント一覧

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/memory/recent_files` | 最近のメモリの論理ファイル名を一覧表示 |
| `GET` | `/api/memory/recent_file` | 最近のメモリファイルを 1 件読み取り |
| `POST` | `/api/memory/recent_file/save` | 1 キャラクター分の最近の履歴を置換 |
| `POST` | `/api/memory/update_catgirl_name` | キャラクターのメモリストレージを名前変更 |
| `GET` | `/api/memory/review_config` | 最近のメモリの自動レビュー設定を取得 |
| `POST` | `/api/memory/review_config` | 最近のメモリの自動レビュー設定を更新 |
| `GET` | `/api/memory/powerful_memory_config` | Powerful Memory 設定を取得 |
| `POST` | `/api/memory/powerful_memory_config` | Powerful Memory 設定を更新し、必要な移行を実行 |
| `GET` | `/api/memory/legacy/scan` | ユーザーに提示できる旧メモリルートを読み取り専用で走査 |
| `POST` | `/api/memory/legacy/purge` | 走査済みの旧ルートから明示的に選択された項目を削除 |

## 最近のメモリファイル

ブラウザー API は互換性のため、論理ファイル名 `recent_<character>.json` を維持しています。現在のストレージでは `memory/<character>/recent.json` に解決され、移行中は旧形式のフラットファイルも読み取れます。

### `GET /api/memory/recent_files`

パラメーターはありません。

アクティブなメモリルートとプロジェクトメモリルートを検索し、論理ファイル名を重複排除してソート順で返します。

```json
{
  "files": ["recent_小天.json", "recent_小夜.json"]
}
```

### `GET /api/memory/recent_file`

**クエリパラメーター**

| 名前 | 型 | 必須 | 説明 |
|---|---|---:|---|
| `filename` | 文字列 | はい | `recent_小天.json` などの論理ファイル名。パス区切り文字と `..` は拒否されます |

`content` はファイルの UTF-8 JSON テキストであり、解析済みのメッセージ配列ではありません。

```json
{
  "content": "[{\"type\":\"human\",\"data\":{...}}]"
}
```

エラーは `{"success": false, "error": "..."}` 形式です。無効なファイル名は `400`、論理ファイルを解決できない場合は `404` です。

### `POST /api/memory/recent_file/save`

選択したキャラクターの最近の履歴を置換し、そのキャラクターで実行中のレビューをキャンセルして、手動編集を反映させます。

**リクエストボディ**

```json
{
  "filename": "recent_小天.json",
  "chat": [
    { "role": "human", "text": "こんにちは！" },
    { "role": "ai", "text": "こんばんは！" }
  ]
}
```

| フィールド | 型 | 必須 | 説明 |
|---|---|---:|---|
| `filename` | 文字列 | はい | `recent_<character>.json` と一致する必要があります。キャラクター名はこの値から導出されます |
| `chat` | 配列 | はい | 置換後の履歴。最大 10,000 件 |
| `chat[].role` | 文字列 | はい | 保存するメッセージ種別。通常は `human`、`ai`、`system` |
| `chat[].text` | 文字列 | いいえ | メッセージ本文。既定値は空文字列 |

メッセージ 1 件のテキスト上限は 32,768 文字、リクエスト全体のメッセージテキスト合計は 2,097,152 文字です。チャット項目の未知のフィールドは保存されません。

成功時:

```json
{
  "success": true,
  "need_refresh": true,
  "catgirl_name": "小天"
}
```

検証エラーは `400` と `success: false` を返します。ストレージエラーは `success: false` と `error` を返し、クラウドストレージのメンテナンスは `409` です。

## キャラクターメモリの名前変更

### `POST /api/memory/update_catgirl_name`

共有のキャラクターメモリ移行ヘルパーを使い、最近の履歴ファイルだけでなくキャラクターのメモリストレージ全体を名前変更します。

```json
{
  "old_name": "旧名",
  "new_name": "新名"
}
```

両フィールドとも必須の文字列です。過去の `old_name` ではピリオドを許可します。`new_name` には現在のキャラクター名規則が適用され、予約済みルート名は使用できません。

```json
{
  "success": true,
  "changed": true,
  "exists_after": true
}
```

名前の欠落または不正は `400`。ストレージが書き込み不可の場合は `409` になることがあります。

## メモリ機能の切り替え

### `GET /api/memory/review_config`

最近のメモリの自動レビューと修正が有効かを返します。設定がない場合の既定値は `true` です。

```json
{ "enabled": true }
```

### `POST /api/memory/review_config`

```json
{ "enabled": false }
```

このルートは `core_config.json` の `recent_memory_auto_review` を保存します。

```json
{ "success": true, "enabled": false }
```

失敗時は `{"success": false, "error": "..."}`。ストレージメンテナンス中は `409` になることがあります。

### `GET /api/memory/powerful_memory_config`

`powerful_memory_enabled` 設定を返します。明示的な設定がない既存環境では `true` が既定値です。

```json
{ "enabled": true }
```

### `POST /api/memory/powerful_memory_config`

```json
{ "enabled": false }
```

Powerful Memory は、シグナル分析、昇格時のマージ、反証チェック、ネガティブ対象チェック、事実の重複排除、persona 修正など、証拠駆動の LLM 経路を制御します。Powerful Memory が無効でも、軽量フィードバック経路は利用できます。

`ON` から `OFF` への変更では、まず Memory Server プロセスに confirmed reflection の経過時間アンカーをリセットさせます。これにより、古い confirmed 項目が時間駆動フォールバックで直ちに昇格することを防ぎます。移行に成功した場合のみ設定を保存します。

成功時:

```json
{ "success": true, "enabled": false }
```

移行または保存の失敗:

```json
{ "success": false, "error": "migration HTTP 409" }
```

## 旧メモリのクリーンアップ

旧メモリのクリーンアップは明示的な 2 段階操作です。まず走査し、次に選択した絶対パスを送信します。走査は移行も削除も行いません。

### `GET /api/memory/legacy/scan`

パラメーターはありません。

アクティブなランタイムメモリディレクトリ外にある候補ルートと、その直下の項目を返します。サイズを計算できない、または安全な走査上限を超えた場合、`size_bytes` は `-1` です。

```json
{
  "success": true,
  "runtime_memory_dir": "C:\\...\\memory",
  "legacy_roots": [
    {
      "root": "C:\\...\\old-root\\memory",
      "source": "legacy_app_root",
      "exists": true,
      "entries": [
        {
          "name": "小天",
          "path": "C:\\...\\old-root\\memory\\小天",
          "is_dir": true,
          "size_bytes": 12345,
          "is_unlinked": false,
          "runtime_has_same_name": true
        }
      ]
    }
  ],
  "total_entries": 1,
  "total_size_bytes": 12345
}
```

予期しない走査エラーは `500` と `success: false` を返します。

### `POST /api/memory/legacy/purge`

これは破壊的な操作です。直近の走査で項目として返されたパスだけを送信してください。

```json
{
  "paths": [
    "C:\\...\\old-root\\memory\\小天"
  ]
}
```

`paths` は空でない絶対パスの配列である必要があります。各パスは、現在認識されている旧ルートの厳密な配下へ解決されなければなりません。相対パス、`..` セグメント、ルートディレクトリ自体、アクティブなランタイムメモリディレクトリは拒否されます。存在しない対象は削除済みとして扱われるため、再試行は冪等です。

削除は項目ごとにベストエフォートで実行されるため、成功したリクエストに `removed` と `errors` の両方が含まれることがあります。

```json
{
  "success": true,
  "removed": ["C:\\...\\old-root\\memory\\小天"],
  "errors": [
    { "path": "C:\\...\\not-allowed", "error": "..." }
  ]
}
```

不正なボディは `400`、認識できる旧ルートがない場合は `409`、初期化失敗は `500` です。
