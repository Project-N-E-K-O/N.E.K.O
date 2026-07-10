# ジュークボックス API

**プレフィックス:** `/api/jukebox`

ジュークボックスは、キャラクターに紐づく歌 / アクションのライブラリで、歌唱やプリセットアクションに使われます。アップロードされた**楽曲**（音声ファイル）、**アクション**（VMD/VRMA などのアニメーションファイル）、両者の**バインディング**、および設定の**インポート / エクスポート**を管理します。

楽曲とアクションはそれぞれ重複排除用の MD5 インデックスを持ちます。アプリ同梱のリソースには `isBuiltin` が付与されており、組み込みリソースを削除してもファイルそのものは消えず、非表示（`visible: false`）になるだけです。

::: info
これらのエンドポイントが返す設定は、同梱（組み込み）ライブラリとユーザーのライブラリをマージしたもので、ユーザーのエントリが優先されます。ディスクへ永続化されるのはユーザーリソースとオーバーライド設定のみです。
:::

## 設定

### `GET /config`

ジュークボックスの完全な設定を返します: `songs`、`actions`、`bindings`、`md5Index`、および後述のサマリーフィールド。

**レスポンス:**

```json
{
  "version": "1.0",
  "songs": { "song_001": { "id": "song_001", "name": "...", "artist": "...", "audio": "songs/...", "audioMd5": "...", "audioFormat": "mp3", "visible": true, "uploadDate": "...", "defaultAction": "" } },
  "actions": { "action_001": { "id": "action_001", "name": "...", "file": "actions/...", "fileMd5": "...", "format": "vmd", "uploadDate": "...", "visible": true, "missing": false } },
  "bindings": { "song_001": { "action_001": { "offset": 0 } } },
  "md5Index": { "songs": {}, "actions": {} },
  "configRevision": "...",
  "songCount": 0,
  "visibleSongCount": 0,
  "actionCount": 0
}
```

### `GET /config/summary`

軽量なサマリーを返します。プレイリスト全体の再取得が必要かどうかをポーリングで判定するのに適しています。

**レスポンス:**

```json
{
  "configRevision": "...",
  "songCount": 0,
  "visibleSongCount": 0,
  "actionCount": 0
}
```

::: info
`configRevision` は `version` + `songs` + `actions` + `bindings` の短く安定したハッシュです。`/config/summary` をポーリングし、`configRevision` が変化したときだけ `/config` を再取得してください。
:::

## 楽曲

### `POST /songs`

1 つ以上の楽曲をアップロードします。`multipart/form-data`。

**ボディ:**

- `files` — 1 つ以上の音声ファイル。許可される拡張子: `.mp3`、`.wav`、`.ogg`、`.flac`。1 ファイルあたり最大 1 GB。
- `metadata` — JSON 文字列の配列で、楽曲ごとに `[{ "name": "...", "artist": "..." }, ...]`。任意。欠落したエントリは音声の埋め込みタグ、次にファイル名へフォールバックします。

**レスポンス:** 単一ファイルの場合は結果オブジェクトを直接返します（`{ "success": true, "song": { ... } }` または `{ "success": false, "error": "..." }`）。複数ファイルの場合は `{ "success": true, "results": [ ... ] }`。重複した音声（MD5 一致）は項目ごとに拒否されます。

### `POST /songs/batch-delete`

検証済みの一括処理で、アップロード済みの楽曲を削除し、組み込み楽曲を非表示にします。

**ボディ:**

```json
{ "songIds": ["song_001", "song_002"] }
```

**レスポンス:** 件数と項目ごとの結果。

```json
{
  "success": true,
  "partial": false,
  "requestedCount": 2,
  "deletedCount": 1,
  "hiddenCount": 1,
  "failedCount": 0,
  "deleted": [{ "songId": "song_001", "name": "..." }],
  "hidden": [{ "songId": "song_002", "name": "..." }],
  "failed": []
}
```

### `DELETE /songs/{song_id}`

アップロード済みの楽曲を削除するか、組み込み楽曲を非表示にします。ユーザー楽曲ではファイル・バインディング・MD5 インデックスのエントリを削除します。組み込み楽曲では `{ "success": true, "message": "...", "hidden": true }` を返します。

**パスパラメータ:** `song_id` — 楽曲 ID。

### `PUT /songs/{song_id}/visibility`

楽曲の表示 / 非表示を設定します。`multipart/form-data`。

**パスパラメータ:** `song_id` — 楽曲 ID。

**ボディ:** `visible` — 真偽値（フォームフィールド）。

### `PUT /songs/{song_id}/metadata`

楽曲の表示名やアーティストを更新します。`multipart/form-data`。

**パスパラメータ:** `song_id` — 楽曲 ID。

**ボディ:** `name`、`artist` — 任意のフォームフィールド。指定されたフィールドのみ更新されます。

### `PUT /songs/{song_id}/default-action`

楽曲のデフォルトアクションを設定します。そのアクションは楽曲に既にバインドされている必要があります。空文字を渡すとデフォルトを解除します。

**パスパラメータ:** `song_id` — 楽曲 ID。

**ボディ:** `action_id` — フォームフィールド。アクション ID、または解除する場合は空。

**レスポンス:** `{ "success": true, "defaultAction": "action_001" }`

## アクション

### `POST /actions`

1 つ以上のアクション（アニメーション）をアップロードします。`multipart/form-data`。

**ボディ:**

- `files` — 1 つ以上のアニメーションファイル。許可される拡張子: `.vmd`、`.bvh`、`.fbx`、`.vrma`。1 ファイルあたり最大 1 GB。
- `metadata` — JSON 文字列の配列で、アクションごとに `[{ "name": "..." }, ...]`。任意。欠落した名前はファイル名へフォールバックします。

**レスポンス:** `POST /songs` と同じ形式で、単一ファイルでは結果オブジェクト、複数ファイルでは `{ "success": true, "results": [ ... ] }`。重複ファイル（MD5 一致）は項目ごとに拒否されます。

### `POST /actions/batch-delete`

検証済みの一括処理で、アップロード済みのアクションを削除し、組み込みアクションを非表示にします。

**ボディ:**

```json
{ "actionIds": ["action_001", "action_002"] }
```

**レスポンス:** `POST /songs/batch-delete` と同じ件数 / 項目ごとの形式で、キーは `actionId`。

### `DELETE /actions/{action_id}`

アップロード済みのアクションを削除するか、組み込みアクションを非表示にします。ユーザーアクションではファイルを削除し、バインディングおよび各楽曲の `defaultAction` 内の参照をクリアし、MD5 インデックスのエントリを削除します。組み込みアクションでは `{ "success": true, "message": "...", "hidden": true }` を返します。

**パスパラメータ:** `action_id` — アクション ID。

### `PUT /actions/{action_id}/visibility`

アクションの表示 / 非表示を設定します。`multipart/form-data`。

**パスパラメータ:** `action_id` — アクション ID。

**ボディ:** `visible` — 真偽値（フォームフィールド）。

### `PUT /actions/{action_id}/metadata`

アクションの表示名を更新します。`multipart/form-data`。

**パスパラメータ:** `action_id` — アクション ID。

**ボディ:** `name` — フォームフィールド（必須）。

## バインド

### `POST /bind`

アクションを楽曲にバインドします。`multipart/form-data`。同じアニメーション種別のデフォルトアクションがその楽曲にまだ無い場合、新しくバインドされたアクションがデフォルトになります。

**ボディ:**

- `songId` — 楽曲 ID。
- `actionId` — アクション ID。
- `offset` — 整数のオフセット。デフォルトは `0`。

**レスポンス:** `{ "success": true, "defaultAction": "action_001" }`

### `DELETE /bind`

楽曲とアクションのバインディングを解除します。`multipart/form-data`。解除したアクションがその楽曲のデフォルトだった場合、デフォルトはクリアされます。

**ボディ:** `songId`、`actionId` — 楽曲とアクションの ID。

**レスポンス:** `{ "success": true, "defaultAction": "..." }`。バインディングが存在しない場合は `404` を返します。

## インポート / エクスポート

### `POST /export`

選択した（またはすべての）楽曲とアクションを ZIP アーカイブとしてエクスポートします。`multipart/form-data`。組み込み楽曲はスキップされ、組み込みアクションは ID/MD5 のみエクスポートされます（ファイルは同梱しません）。バインディングは別マシンでも正しく再リンクできるよう MD5 レベルでエクスポートされます。

**ボディ:**

- `songIds` — 楽曲 ID の任意の JSON 文字列配列。省略時は（`includeHidden` に従い）すべての楽曲が対象になります。
- `actionIds` — アクション ID の任意の JSON 文字列配列。省略時（全エクスポート）はすべてのアクションがエクスポートされます。
- `includeHidden` — 真偽値、デフォルトは `true`。`false` の場合、非表示の楽曲 / アクションおよびそのバインディングは除外されます。

**レスポンス:** `config.json` と楽曲 / アクションファイルを含む、ストリーミングの `application/zip` ダウンロード（`jukebox_export.zip`）。

### `POST /import`

以前にエクスポートした ZIP アーカイブをインポートします。`multipart/form-data`。MD5 レベルのバインディングはローカルの ID レベルのバインディングへ変換され、一致したリソースは重複させず統合されます。

**ボディ:** `file` — ZIP アーカイブ（最大 10 GB）。

**レスポンス:** インポート統計。

```json
{
  "success": true,
  "stats": {
    "songsAdded": 0,
    "songsMerged": 0,
    "actionsAdded": 0,
    "actionsMerged": 0,
    "bindingsAdded": 0
  }
}
```

### `GET /file/{file_path:path}`

楽曲またはアクションのファイルを配信します。ユーザードキュメントディレクトリを優先し、無い場合は同梱ディレクトリへフォールバックします。ディレクトリトラバーサルに対して保護されています。

**パスパラメータ:** `file_path` — 相対パス。例: `songs/song_001.mp3` または `actions/action_001.vmd`。

**レスポンス:** ファイル本体。メディアタイプは拡張子から推定されます（例: `.mp3` は `audio/mpeg`）。

### `POST /pack-folder`

任意の一連のアップロードファイルを（相対パスを保ったまま）1 つの ZIP アーカイブにまとめます。`multipart/form-data`。ジュークボックスのインポート / エクスポート UI が使用する汎用ユーティリティです。

**ボディ:** `files` — 1 つ以上のファイル。各ファイルは相対パスをファイル名として持ちます。

**レスポンス:** ストリーミングの `application/zip` ダウンロード（`packed.zip`）。
