# クラウドセーブ API

クラウドセーブルーターは、デスクトップ UI が使うキャラクターユニット同期を提供します。メインサーバーの `/api/cloudsave` 以下で動作し、設定済みの Steam Auto Cloud をストレージバックエンドとして使用します。

> [!CAUTION]
> アップロードとダウンロードはキャラクターデータを変更します。ダウンロード時には、アクティブセッションの終了、メモリサーバーのデータベースハンドル解放、ローカルファイルの置換、キャラクター状態とメモリサーバーの再読み込みが行われる場合があります。バックグラウンドポーリングではなく、ユーザー確認を伴うデータ管理操作として扱ってください。

## ルート一覧

| メソッド | パス | 用途 |
|---|---|---|
| `GET` | `/api/cloudsave/summary` | ローカルとクラウドのキャラクターユニットを比較し、Steam Workshop 素材の状態も返す |
| `GET` | `/api/cloudsave/steam-autocloud-config` | 同期バックエンドと Steam Auto Cloud の可用性を返す |
| `GET` | `/api/cloudsave/character/{name}` | 1 キャラクターの同期詳細を返す |
| `POST` | `/api/cloudsave/character/{name}/upload` | 一貫したキャラクターユニットをクラウドへエクスポートする |
| `POST` | `/api/cloudsave/character/{name}/download` | キャラクターユニットをローカルへインポートし、実行時状態を再読み込みする |

各パスの末尾に `/` はありません。

## 読み取りエンドポイント

`GET /api/cloudsave/summary` は現在のキャラクター設定からクラウドセーブ概要を生成します。レスポンスには `sync_backend` と `steam_autocloud` も含まれ、Steam Workshop 素材を参照する項目には現在の Workshop 状態が追加されます。

`GET /api/cloudsave/steam-autocloud-config` のレスポンス：

```json
{
  "success": true,
  "sync_backend": "steam_auto_cloud",
  "steam_autocloud": {}
}
```

`steam_autocloud` の具体的なフィールドは現在のインストール状態を表し、Steam の可用性によって変わることがあります。

`GET /api/cloudsave/character/{name}` は指定キャラクターのローカル／クラウド比較を返します。クラウド側に存在しない場合は HTTP `404` とコード `CLOUDSAVE_CHARACTER_NOT_FOUND` を返します。

## キャラクターをアップロード

```http
POST /api/cloudsave/character/Lanlan/upload
Content-Type: application/json

{"overwrite": false}
```

`overwrite` は省略可能で、既定値は `false` です。JSON の真偽値でなければなりません。成功時は `character_name`、更新済み `detail`、エクスポートされた `meta`、`sequence_number`、`sync_backend`、`steam_autocloud` を返します。

エクスポート対象はカードだけでなくキャラクターユニットですが、**メモリディレクトリ全体のバックアップではありません**。現在のスナップショットは、存在する場合に限り次の許可済みフラットファイルだけをコピーします。

```text
recent.json
settings.json
facts.json
facts_archive.json
persona.json
persona_corrections.json
reflections.json
reflections_archive.json
surfaced.json
time_indexed.db
```

現在の分割アーカイブである `reflection_archive/` と `persona_archive/`、直近サマリーのメタデータ `recent_meta.json`、復旧状態の `cursors.json`、`outbox.ndjson`、`events.ndjson`、`events_applied.json`、および `facts_pending_dedup.json` などの worker sidecar は含まれません。これらはアップロードされず、別デバイスでも復元されません。上書き前の操作バックアップはローカルロールバック用であり、クラウドの対象範囲を広げるものではありません。クラウド側に既に存在し、`overwrite` が false の場合は HTTP `409` です。

## キャラクターをダウンロード

```http
POST /api/cloudsave/character/Lanlan/download
Content-Type: application/json

{
  "overwrite": true,
  "backup_before_overwrite": true,
  "force": false
}
```

| フィールド | 型 | 既定値 | 意味 |
|---|---|---:|---|
| `overwrite` | boolean | `false` | 既存のローカルキャラクターの置換を許可する |
| `backup_before_overwrite` | boolean | `true` | 置換前に操作バックアップを作成する |
| `force` | boolean | `false` | インポート前にアクティブセッションを終了する |

キャラクターにアクティブセッションがあり、`force` が true でない場合、HTTP `409`、コード `ACTIVE_SESSION_BLOCKED`、`can_force: true` が返ります。`force: true` では、サーバーがセッションを終了し、メモリサーバーハンドルを解放してからインポートします。

インポート後はキャラクター設定を再読み込みし、メモリサーバーにも再読み込みを要求します。失敗時は操作バックアップからの復元を試み、HTTP `500`、コード `LOCAL_RELOAD_FAILED_ROLLED_BACK`、ロールバック結果を返します。成功時は `detail`、`backup_path`、`sync_backend`、`steam_autocloud` が含まれます。

## エラー

クラウドセーブのエラーは FastAPI の `detail` だけではなく、次の形式です：

```json
{
  "success": false,
  "error": "LOCAL_CHARACTER_EXISTS",
  "code": "LOCAL_CHARACTER_EXISTS",
  "message": "local character already exists: Lanlan",
  "message_key": "cloudsave.error.localCharacterExists",
  "message_params": {},
  "character_name": "Lanlan"
}
```

主なステータスコード：

| ステータス | 意味 |
|---:|---|
| `400` | 不正な JSON、真偽値オプションの型違い、名前監査失敗、または拒否された操作 |
| `404` | 指定したローカル／クラウドキャラクターが存在しない |
| `409` | 保存先が既存、アクティブセッションあり、または書き込みフェンスが有効 |
| `500` | 予期しないアップロード／ダウンロード失敗、または再読み込み／ロールバック失敗 |
| `503` | クラウドプロバイダー利用不可、セッション終了失敗、メモリハンドル解放失敗 |

英語の `message` だけに依存せず、`code` で分岐し、ローカライズ UI には `message_key` を使ってください。
