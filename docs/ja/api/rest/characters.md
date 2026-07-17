# Characters API

**プレフィックス:** `/api/characters`

この router はキャラクタープロファイル、モデル割り当て、ペルソナ導入、キャラクターカード、マイク選択、音声ライフサイクルを管理します。コレクション route は末尾スラッシュなしの `GET /api/characters` です。

## キャラクターとモデル状態

| メソッドとパス | 用途 |
|---|---|
| `GET /api/characters` | キャラクタープロファイルを列挙。リクエスト言語に応じて編集フィールド名をローカライズできます。 |
| `POST /api/characters/catgirl` | JSON プロファイルからキャラクターを作成。 |
| `PUT /api/characters/catgirl/{name}` | 既存キャラクターの変更可能フィールドを更新。 |
| `DELETE /api/characters/catgirl/{name}` | 安全なパス名でキャラクターを削除。 |
| `POST /api/characters/catgirl/delete` | JSON body で削除。URL に安全に置けない過去の名前向けです。 |
| `POST /api/characters/catgirl/{old_name}/rename` | キャラクター名と関連状態を移行。body: `{ "new_name": "..." }`。 |
| `GET`、`POST /api/characters/current_catgirl` | 現在のキャラクターを取得または選択。POST body: `{ "catgirl_name": "..." }`。 |
| `POST /api/characters/reload` | ストレージから設定を再読み込み。 |
| `POST /api/characters/master` | マスタープロファイルを更新。 |
| `POST /api/characters/master/{old_name}/rename` | マスタープロファイルを改名。 |
| `POST`、`GET /api/characters/set_microphone`、`/get_microphone` | マイクを保存または取得。POST は `microphone_id`、任意で `microphone_name`。 |

名前にはパス安全性と長さの検証があります。多くの書き込み API は `{ "success": true }` 形式のアプリケーション envelope を返し、不正 JSON、名前、未存在、競合、書き込みフェンスは操作に応じて `400`、`404`、`409`、`503` になります。

### モデル割り当て

| メソッドとパス | 用途 |
|---|---|
| `GET /api/characters/current_live2d_model` | 現在または指定キャラクターの Live2D、VRM、MMD、PNGTuber 割り当てを解決。任意 query: `catgirl_name`、`item_id`。 |
| `PUT /api/characters/catgirl/l2d/{name}` | モデル割り当てを更新。歴史的な `l2d` 名ですが、現行モデル種別を扱います。 |
| `PATCH /api/characters/catgirl/{name}/touch_set` | 現在モデルのタッチ動作セットを置換。 |
| `PUT /api/characters/catgirl/{name}/lighting` | VRM ライティングを更新。 |
| `GET`、`PUT /api/characters/catgirl/{name}/mmd_settings` | MMD の描画、物理、照明、ポインター追従設定を取得・更新。 |

## ペルソナ選択

| メソッドとパス | 用途 |
|---|---|
| `GET /api/characters/persona-presets` | ローカライズ済み内蔵プリセットを列挙。 |
| `GET`、`POST /api/characters/persona-onboarding-state` | 初回ペルソナ導入状態を取得・更新。 |
| `POST`、`DELETE /api/characters/persona-reselect-current` | 現在キャラクターの再選択要求を設定・解除。 |
| `GET`、`PUT`、`DELETE /api/characters/character/{name}/persona-selection` | 指定キャラクターの選択を取得・設定・解除。 |

## キャラクターカードとポートレート

| メソッドとパス | 用途 |
|---|---|
| `GET /api/characters/character-card/list` | 保存済みカードを列挙。 |
| `POST /api/characters/character-card/save` | インポートしたカードデータを設定へ保存。 |
| `POST /api/characters/catgirl/save-to-model-folder` | パッケージ用にカードをモデルフォルダーへ保存。 |
| `GET /api/characters/catgirl/{name}/export` | プロファイルとモデル archive を埋め込んだ PNG カードを export。 |
| `GET /api/characters/catgirl/{name}/export-settings` | モデル素材なしで設定だけを export。 |
| `POST /api/characters/import-card` | multipart `zip_file` を import。`card_image` は任意。 |
| `GET /api/characters/card-faces` | カード面データを列挙。 |
| `GET /api/characters/card-metas` | カードの由来・メタデータを列挙。 |
| `GET`、`PUT /api/characters/catgirl/{name}/card-meta` | 1 枚のメタデータ JSON を取得・更新。 |
| `GET`、`PUT /api/characters/catgirl/{name}/card-face` | カード面画像を取得・アップロード（`image`）。 |
| `POST /api/characters/catgirl/{name}/export-with-portrait` | multipart `portrait` で export。`include_model` の既定値は true。 |

export は JSON ではなくファイルを返します。upload/import はファイル名、サイズ、archive entry、パス境界を検証し、通常 `400` または `413` を返します。

## 音声操作

| メソッドとパス | 用途 |
|---|---|
| `GET /api/characters/voices` | 現在の provider 設定で利用可能な音声を列挙。 |
| `GET /api/characters/voice_preview` | 必須 `voice_id` のローカライズ済み試聴を生成。`language`/`i18n_language` は任意、JSON に base64 音声を返します。 |
| `PUT /api/characters/catgirl/voice_id/{name}` | キャラクターへ voice ID を割り当て。 |
| `GET /api/characters/catgirl/{name}/voice_mode_status` | 音声モード状態を取得。 |
| `POST /api/characters/catgirl/{name}/unregister_voice` | カスタム音声割り当て・登録を解除。 |
| `POST /api/characters/clear_voice_ids` | 全キャラクターのローカル voice ID を消去。 |
| `GET /api/characters/custom_tts_voices` | カスタム TTS 音声を列挙。任意 `provider` filter。 |
| `POST /api/characters/voices` | JSON からカスタム音声を登録。 |
| `DELETE /api/characters/voices/{voice_id}` | 登録済み音声を削除。 |
| `POST /api/characters/voice_clone` | multipart 音声から clone。`file`、`prefix` 必須、provider 固有フィールドも受付。 |
| `POST /api/characters/voice_clone_direct` | 検証済み直リンク音声から登録/clone。プライベートネットワークや危険な redirect は拒否。 |
| `POST /api/characters/voice_design` | `provider`、`prefix`、`voice_prompt` から再利用可能な音声を作成して保存。対応 provider と制約は TTS provider registry に従います。 |
| `POST /api/characters/voice_design_preview` | ElevenLabs voice design の候補を生成。 |
| `POST /api/characters/voice_design_create` | 選択候補を再利用可能な音声として保存。 |
| `POST /api/characters/audio/analyze_silence` | multipart `file` の無音区間を解析。 |
| `POST /api/characters/audio/trim_silence` | multipart `file` を trim。`task_id` は任意。 |
| `GET /api/characters/audio/trim_progress/{task_id}` | trim 進捗を取得。 |
| `POST /api/characters/audio/trim_cancel/{task_id}` | trim 中止を要求。 |

Provider エラーは JSON envelope に入り、分類可能な場合は HTTP `4xx`/`5xx` も使います。Provider 固有フィールドと音声 catalog は実行時データであり、provider 間の固定 schema ではありません。

## 実装で確認した route 一覧

```text
GET    /api/characters
GET    /api/characters/character-card/list
POST   /api/characters/catgirl/save-to-model-folder
POST   /api/characters/character-card/save
GET    /api/characters/catgirl/{name}/export
GET    /api/characters/catgirl/{name}/export-settings
POST   /api/characters/import-card
GET    /api/characters/card-faces
GET    /api/characters/card-metas
GET    /api/characters/catgirl/{name}/card-meta
PUT    /api/characters/catgirl/{name}/card-meta
GET    /api/characters/catgirl/{name}/card-face
PUT    /api/characters/catgirl/{name}/card-face
POST   /api/characters/catgirl/{name}/export-with-portrait
POST   /api/characters/catgirl/{old_name}/rename
GET    /api/characters/current_catgirl
POST   /api/characters/current_catgirl
POST   /api/characters/reload
POST   /api/characters/master
POST   /api/characters/master/{old_name}/rename
POST   /api/characters/catgirl
PUT    /api/characters/catgirl/{name}
POST   /api/characters/catgirl/delete
DELETE /api/characters/catgirl/{name}
POST   /api/characters/set_microphone
GET    /api/characters/get_microphone
GET    /api/characters/current_live2d_model
PUT    /api/characters/catgirl/l2d/{name}
PATCH  /api/characters/catgirl/{name}/touch_set
PUT    /api/characters/catgirl/{name}/lighting
PUT    /api/characters/catgirl/{name}/mmd_settings
GET    /api/characters/catgirl/{name}/mmd_settings
GET    /api/characters/persona-presets
GET    /api/characters/persona-onboarding-state
POST   /api/characters/persona-onboarding-state
POST   /api/characters/persona-reselect-current
DELETE /api/characters/persona-reselect-current
GET    /api/characters/character/{name}/persona-selection
PUT    /api/characters/character/{name}/persona-selection
DELETE /api/characters/character/{name}/persona-selection
POST   /api/characters/audio/analyze_silence
POST   /api/characters/audio/trim_silence
GET    /api/characters/audio/trim_progress/{task_id}
POST   /api/characters/audio/trim_cancel/{task_id}
POST   /api/characters/voice_clone
POST   /api/characters/voice_design
POST   /api/characters/voice_design_preview
POST   /api/characters/voice_design_create
POST   /api/characters/voice_clone_direct
GET    /api/characters/voices
GET    /api/characters/voice_preview
PUT    /api/characters/catgirl/voice_id/{name}
GET    /api/characters/catgirl/{name}/voice_mode_status
POST   /api/characters/catgirl/{name}/unregister_voice
POST   /api/characters/clear_voice_ids
GET    /api/characters/custom_tts_voices
POST   /api/characters/voices
DELETE /api/characters/voices/{voice_id}
```
