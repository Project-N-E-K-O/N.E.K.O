# Memory API

**プレフィックス:** `/api/memory`

会話メモリファイルとレビュー設定を管理します。

## 最近のメモリファイル

### `GET /api/memory/recent_files`

メモリストア内のすべての `recent_*.json` ファイルを一覧表示します。

### `GET /api/memory/recent_file`

特定のメモリファイルの内容を取得します。

**クエリ:** `filename` — メモリファイルの名前。

### `POST /api/memory/recent_file/save`

更新されたメモリファイルを保存します。

**ボディ:**

```json
{
  "filename": "recent_character_name.json",
  "catgirl_name": "character_name",
  "chat_history": [
    { "role": "user", "content": "Hello!" },
    { "role": "assistant", "content": "Hi there!" }
  ]
}
```

::: info
キャラクター名は CJK 文字をサポートする正規表現で検証されます。チャット履歴エントリは必須フィールドが検証されます。
:::

## 名前管理

### `POST /api/memory/update_catgirl_name`

すべてのメモリファイルにわたってキャラクター名を更新します。

**ボディ:**

```json
{
  "old_name": "old_character_name",
  "new_name": "new_character_name"
}
```

## レビュー設定

### `GET /api/memory/review_config`

メモリレビュー設定（圧縮スケジュール、保持設定）を取得します。

### `POST /api/memory/review_config`

メモリレビュー設定を更新します。
