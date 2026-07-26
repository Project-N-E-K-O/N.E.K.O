# Steam Workshop API

**プレフィックス:** `/api/steam/workshop`

同梱 Workshop UI の integration surface です。local staging、Steam UGC discovery/download/publish、character sync、unsubscribe cleanup、任意の reference voice packaging を扱います。

::: warning First-party・local-only
多くの request/response field は UI workflow state で、versioned third-party schema ではありません。一部 route は local path、Steam subscription、character data を変更します。loopback のみで運用してください。Steamworks 未初期化時、Steam 依存操作は `503` です。
:::

## Config と sandbox file helper

| メソッドとパス | 用途 |
|---|---|
| `GET /api/steam/workshop/config` | `default_workshop_folder`、`user_mod_folder`、auto-create 設定を取得。 |
| `POST /api/steam/workshop/config` | 対応 field を merge し、有効なら folder を作成。 |
| `GET /api/steam/workshop/read-file` | Workshop root 内の必須 query `path` を read。text は直接、既知 binary は base64。上限 5 MiB。 |
| `GET /api/steam/workshop/list-chara-files` | 必須 query `directory` 内の top-level `*.chara.json` を列挙。 |
| `GET /api/steam/workshop/list-audio-files` | 必須 `directory` 内の top-level `.mp3`/`.wav` を列挙。 |

Path containment が traversal を拒否します。missing path は `404`、oversize read は `413`、その他 read failure は `500`。

## Steam item discovery・download

| メソッドとパス | 用途 |
|---|---|
| `GET /api/steam/workshop/status` | Steamworks 初期化状態。 |
| `GET /api/steam/workshop/subscribed-items` | subscribed UGC metadata を cache/refresh して返します。 |
| `GET /api/steam/workshop/item/{item_id}` | 1 item の metadata。 |
| `GET /api/steam/workshop/item/{item_id}/path` | installed item の local path。 |
| `POST /api/steam/workshop/item/{item_id}/download` | download trigger。任意 body: `high_priority`、`wait`、`timeout`（1–600 秒）。 |
| `GET /api/steam/workshop/item/{item_id}/download-status` | state、byte progress、installed path を poll。 |

非数値 ID は `400`、未 subscribe download は `409`、Steam reject は `502` の場合があります。`wait: true` timeout は HTTP `202` と現在進捗を返し、poll 継続可能。既に install 済みなら即時成功です。

## Staging と publish

### `POST /api/steam/workshop/prepare-upload`

一時 `WorkshopExport/item_*` を作り、character card と Live2D/VRM/MMD model を copy。UI 必須は `charaData`、`modelName`、`modelType` の既定は `live2d`。任意 `fileName`、`character_card_name`。既 upload metadata、未対応 type、危険 path、missing asset は拒否します。

### Upload・cleanup helper

| メソッドとパス | 用途 |
|---|---|
| `POST /api/steam/workshop/upload-preview-image` | multipart JPEG/PNG `file` を upload。任意 `content_folder`。`file_path` を返します。 |
| `GET /api/steam/workshop/check-upload-status` | query `item_path` の staging/upload 状態を確認。 |
| `POST /api/steam/workshop/cleanup-temp-folder` | body `temp_folder` が `WorkshopExport` 内に解決される場合のみ削除。 |

### `POST /api/steam/workshop/publish`

prepared folder を publish。JSON 必須 `title`、`content_folder`、整数 `visibility`。任意 `description`、`preview_image`、`tags`、`change_note`、`character_card_name`。`content_folder` は Workshop root 内必須。Steam callback は非同期 native integration で、進捗/失敗は `success` envelope と HTTP status で返します。

::: info Platform boundary
macOS arm64 は現 Steamworks binding に callback crash risk があるため native publish を明示的に拒否します。
:::

## Character metadata・sync

| メソッドとパス | 用途 |
|---|---|
| `GET /api/steam/workshop/meta/{character_name}` | card の local `.workshop_meta.json` snapshot と upload 状態。 |
| `POST /api/steam/workshop/sync-characters` | subscribed/installed item を scan して card を sync。 |
| `POST /api/steam/workshop/sync-character/{item_id}` | 1 item の card を sync。 |
| `POST /api/steam/workshop/unsubscribe` | body `item_id` を unsubscribe し、関連 character/asset を guarded cleanup。 |

Sync は skip/conflict、missing install、storage write fence を JSON で報告する場合があります。Unsubscribe は origin metadata と保守的 disk check を使い、Workshop folder に同名 card があるだけで local character を削除しません。

## Reference voice packaging

| メソッドとパス | 用途 |
|---|---|
| `POST /api/steam/workshop/upload-reference-audio` | multipart `file` と `WorkshopExport` 内 `content_folder`。MP3/WAV を受け `voice_manifest.json` を作成。任意 `prefix`、`display_name`、`ref_language`、`provider_hint`。 |
| `POST /api/steam/workshop/remove-reference-audio` | body `content_folder` から sample と manifest を削除。 |
| `GET /api/steam/workshop/voice-reference/{item_id}` | installed subscribed item の normalized manifest。ない場合 `available: false`。 |
| `GET /api/steam/workshop/voice-reference/{item_id}/audio` | 検証済み reference audio を stream。 |

Reference material を package するだけで、local TTS voice の clone/register は行いません。

## 実装で確認した route 一覧

```text
GET  /api/steam/workshop/config
POST /api/steam/workshop/config
GET  /api/steam/workshop/read-file
GET  /api/steam/workshop/list-chara-files
GET  /api/steam/workshop/list-audio-files
GET  /api/steam/workshop/status
POST /api/steam/workshop/item/{item_id}/download
GET  /api/steam/workshop/item/{item_id}/download-status
GET  /api/steam/workshop/item/{item_id}/path
GET  /api/steam/workshop/item/{item_id}
GET  /api/steam/workshop/meta/{character_name}
POST /api/steam/workshop/upload-preview-image
GET  /api/steam/workshop/check-upload-status
POST /api/steam/workshop/prepare-upload
POST /api/steam/workshop/cleanup-temp-folder
POST /api/steam/workshop/publish
POST /api/steam/workshop/sync-characters
POST /api/steam/workshop/sync-character/{item_id}
GET  /api/steam/workshop/subscribed-items
POST /api/steam/workshop/unsubscribe
POST /api/steam/workshop/upload-reference-audio
POST /api/steam/workshop/remove-reference-audio
GET  /api/steam/workshop/voice-reference/{item_id}
GET  /api/steam/workshop/voice-reference/{item_id}/audio
```
