# System API

**プレフィックス:** `/api`

この router は、より具体的な resource router に属さない同梱アプリサービスをまとめます。readiness、notice、activity、prompt flow、screenshot、Steam、survey、翻訳、proactive delivery、ページ handoff です。

::: warning ローカルアプリ API
一部 endpoint はローカル file 読取、desktop capture、Steam 状態、onboarding 状態を扱います。mutation route は local-request/CSRF 検証を使い、screenshot は loopback client も必須です。信頼できない network へ直接公開しないでください。
:::

## Readiness、usage、notice

| メソッドとパス | 用途 |
|---|---|
| `GET /api/system/status` | `no-store` bootstrap snapshot。`status` は `starting`、`migration_required`、`ready` で storage migration flag を含みます。 |
| `GET /api/token-usage` | query `days`（既定 7、最大 90）の token 統計。 |
| `GET /api/pending-notices` | notice queue を削除せず `{ notices, cursor }` で peek。 |
| `POST /api/pending-notices/ack` | body `cursor` 以前のみ drain し、read 後に追加された notice を保持。 |
| `POST /api/activity_signal` | frontend の bounded OS/activity heartbeat を activity tracker へ渡します。 |

起動情報がまだ取得できない間、status probe は意図的に HTTP 200 と `ready: false` を返します。deep health check ではなく bootstrap sentinel です。

## Changelog と survey

| メソッドとパス | 用途 |
|---|---|
| `GET /api/changelog` | query `since` より新しい entry を返し、`lang` は検証済み locale fallback を選択。 |
| `GET /api/survey` | 対象 Steam user に現 version の localized survey、その他は `has_survey: false`。DNT/reporting opt-out では配信しません。 |
| `POST /api/survey/submit` | 現 version survey を submit/skip。回答を型・サイズ制限し、remote upload 結果を `uploaded` で返します。 |

submit は local mutation validation 必須。server 自身の app version を使い、client の survey version は信用しません。

## Emotion と翻訳

### `POST /api/emotion/analysis`

指定キャラクターの text を project の emotion label へ正規化します。拡張可能 JSON body は `text`、`lanlan_name`、応答は emotion と confidence。設定済み model を使い、必要時は bounded heuristic へ degrade します。

### `POST /api/translate`

同梱 subtitle 用 translation endpoint:

```json
{
  "text": "Hello",
  "target_lang": "ja",
  "source_lang": "en",
  "skip_google": false
}
```

`source_lang` は省略時 auto-detect。応答は `success`、`translated_text`、正規化した source/target、必要に応じ `google_failed`。失敗時は application envelope 内で原文を返します。

## ローカル file・image helper

| メソッドとパス | 用途と境界 |
|---|---|
| `GET /api/file-exists` | 必須 query `path`、`{ exists }`。明示 traversal を拒否しつつ通常の user/Workshop absolute path は意図的に対応。 |
| `GET /api/find-first-image` | 必須 query **`folder`**。許可された app/assets/user-data root で固定 preview 名と 1 MiB 未満画像のみ検索。 |
| `GET /api/meme/proxy-image` | 必須 remote `url`。SSRF 検証、content limit、cache 付き HTTP(S) proxy。 |
| `GET /api/steam/proxy-image` | 必須 local `image_path`。containment/type 検証後に local/Workshop image を配信。 |

入力不足は通常 `400`、禁止 path/target は `403`、未存在 local file は `404`、upstream image failure は段階に応じた `4xx`/`5xx` です。

## Screenshot と active window

| メソッドとパス | 用途 |
|---|---|
| `GET /api/get_window_title` | platform integration がある場合に active window title を返します（主に Windows）。 |
| `POST /api/screenshot` | loopback 限定 pyautogui fallback capture。JPEG data URL と byte size。 |
| `POST /api/screenshot/interactive` | macOS は native region selection、他 platform は frontend capture を指示。loopback 限定。 |

成功は `{ "success": true, "data": "data:image/jpeg;base64,...", "size": 123 }`、選択取消は `success: false, canceled: true`。remote 設定または非 loopback は host desktop を capture せず拒否します。

## Proactive と mini-game event

| メソッドとパス | 用途 |
|---|---|
| `POST /api/proactive_chat` | `lanlan_name` の source selection、generation、delivery pipeline を実行。 |
| `POST /api/proactive/music_played_through` | 推薦曲の完走を source weighting の positive feedback として記録。 |
| `POST /api/mini_game/invite/respond` | active mini-game invitation state machine に user response を適用。 |

Proactive 応答は `action: chat` または `action: pass` と、busy、empty source、duplicate、delivery preemption、timeout、delivered などを示す安定した `reason_code`/`stage` を使います。別途呼べる「phase 1 screening」route はありません。

## Tutorial・autostart prompt state

Homepage state machine 用の内部 endpoint です。

| メソッドとパス | 用途 |
|---|---|
| `GET /api/tutorial-prompt/state` | tutorial prompt state を取得。 |
| `POST /api/tutorial-prompt/heartbeat` | idle/interaction を記録し prompt 要否を判定。 |
| `POST /api/tutorial-prompt/shown` | 表示を記録。 |
| `POST /api/tutorial-prompt/decision` | user decision を記録。 |
| `POST /api/tutorial-prompt/reset` | state を reset。 |
| `POST /api/tutorial-prompt/tutorial-started` | tutorial start を記録。 |
| `POST /api/tutorial-prompt/tutorial-completed` | tutorial completion を記録。 |
| `GET /api/autostart-prompt/state` | autostart prompt state を取得。 |
| `POST /api/autostart-prompt/heartbeat` | homepage state を記録し prompt 要否を判定。 |
| `POST /api/autostart-prompt/shown` | 表示を記録。 |
| `POST /api/autostart-prompt/decision` | autostart decision を記録。 |

この group の POST はすべて local mutation validation 必須。body は同梱 UI state で、stable third-party schema ではありません。

## Steam state

| メソッドとパス | 用途 |
|---|---|
| `POST /api/steam/set-achievement-status/{name}` | 指定した設定済み achievement を unlock/set。 |
| `POST /api/steam/update-playtime` | bounded playtime delta を加算して Steam stats を保存。 |
| `GET /api/steam/list-achievements` | achievement state を列挙する診断 endpoint。 |

Steamworks unavailable は failure envelope。無効な local mutation request は Steam 状態変更前に拒否します。

## Yui guide handoff

| メソッドとパス | 用途 |
|---|---|
| `POST /api/yui-guide/handoff/create` | 短命、署名付き、memory 上の one-time token を作成。`target_page` 必須、source/target path と resume metadata は任意。 |
| `POST /api/yui-guide/handoff/consume` | 必須 `token`、`signature`、`expected_page` で consume。`consumer_id` は任意。 |

応答は `no-store`。無効入力 `400`、signature/origin/page mismatch `403`、missing/expired `404`、replay/conflict `409`。

## 実装で確認した route 一覧

```text
POST /api/activity_signal
GET  /api/changelog
GET  /api/survey
POST /api/survey/submit
POST /api/emotion/analysis
GET  /api/file-exists
GET  /api/find-first-image
GET  /api/meme/proxy-image
POST /api/mini_game/invite/respond
POST /api/proactive_chat
POST /api/proactive/music_played_through
GET  /api/tutorial-prompt/state
POST /api/tutorial-prompt/heartbeat
POST /api/tutorial-prompt/shown
POST /api/tutorial-prompt/decision
POST /api/tutorial-prompt/reset
GET  /api/autostart-prompt/state
POST /api/autostart-prompt/heartbeat
POST /api/autostart-prompt/shown
POST /api/autostart-prompt/decision
POST /api/tutorial-prompt/tutorial-started
POST /api/tutorial-prompt/tutorial-completed
GET  /api/get_window_title
POST /api/screenshot
POST /api/screenshot/interactive
GET  /api/system/status
GET  /api/token-usage
GET  /api/pending-notices
POST /api/pending-notices/ack
POST /api/steam/set-achievement-status/{name}
POST /api/steam/update-playtime
GET  /api/steam/list-achievements
GET  /api/steam/proxy-image
POST /api/translate
POST /api/yui-guide/handoff/create
POST /api/yui-guide/handoff/consume
```
