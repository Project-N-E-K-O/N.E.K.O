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
  "chat": [
    { "role": "哥哥", "text": "Hello!" },
    { "role": "小天", "text": "Hi there!" }
  ]
}
```

キャラクター名は `filename` から（`extract_catgirl_name_from_recent_filename` を介して）導出され、ボディからは読み取られません。各チャットエントリには `role` 文字列が必要で、メッセージ本文は `text` フィールドから読み取られます。

注意: `role` は `recent.json` に保存される発話者の名前です——人間のターンはマスターに設定された名前、AI のターンはキャラクターの名前であり、`"user"`/`"assistant"` ではありません。ハンドラーは各エントリの `role` をそのまま書き込みます。

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
