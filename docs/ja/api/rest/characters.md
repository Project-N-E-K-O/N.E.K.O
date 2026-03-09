# Characters API

**プレフィックス:** `/api/characters`

AI キャラクター（内部では「catgirl」または「lanlan」と呼ばれます）を管理します。CRUD 操作、音声設定、マイク設定を含みます。

## キャラクター管理

### `GET /api/characters/`

オプションの言語ローカライズ付きで全キャラクターを一覧表示します。

**クエリ:** `language`（オプション） — 翻訳されたフィールド名のロケールコード。`Accept-Language` ヘッダーも参照されます。

---

### `POST /api/characters/catgirl`

新しいキャラクターを作成します。

**ボディ:** パーソナリティフィールドを含むキャラクターデータオブジェクト（档案名、昵称、性格、system_prompt、voice_id 等）。

---

### `PUT /api/characters/catgirl/{name}`

既存のキャラクター設定を更新します。

**パス:** `name` — キャラクター識別子。

**ボディ:** 更新されたキャラクターデータ（昵称、性别、voice_id 等）。

---

### `DELETE /api/characters/catgirl/{name}`

キャラクターを削除します。現在アクティブなキャラクターは削除できません。

---

### `POST /api/characters/catgirl/{old_name}/rename`

キャラクターの名前を変更します。メモリファイルを含むすべての参照を更新します。

**ボディ:**

```json
{ "new_name": "new_character_name" }
```

---

### `GET /api/characters/current_catgirl`

現在アクティブなキャラクターを取得します。

### `POST /api/characters/current_catgirl`

アクティブなキャラクターを切り替えます。

**ボディ:**

```json
{ "catgirl_name": "character_name" }
```

---

### `POST /api/characters/reload`

ディスクからキャラクター設定をリロードします。

### `POST /api/characters/master`

マスター（オーナー/プレイヤー）情報を更新します。

## モデルバインディング

### `GET /api/characters/current_live2d_model`

現在のキャラクターのモデル情報を取得します。

**クエリ:** `catgirl_name`（オプション）、`item_id`（オプション Workshop アイテム ID）

### `PUT /api/characters/catgirl/l2d/{name}`

キャラクターのモデルバインディング（Live2D または VRM）を更新します。

**ボディ:**

```json
{
  "live2d": "model_directory_name",
  "vrm": "vrm_model_name",
  "model_type": "live2d",
  "item_id": "workshop_item_id",
  "vrm_animation": "animation_name"
}
```

### `PUT /api/characters/catgirl/{name}/lighting`

キャラクターの VRM ライティング設定を更新します。

**ボディ:**

```json
{
  "lighting": { ... },
  "apply_runtime": true
}
```

## 音声設定

### `PUT /api/characters/catgirl/voice_id/{name}`

キャラクターの TTS 音声 ID を設定します。

**ボディ:**

```json
{ "voice_id": "voice-tone-xxxxx" }
```

### `GET /api/characters/catgirl/{name}/voice_mode_status`

キャラクターの音声モードの利用可否を確認します。

### `POST /api/characters/catgirl/{name}/unregister_voice`

キャラクターからカスタム音声を削除します。

### `GET /api/characters/voices`

現在の API キーに登録されたすべてのボイストーンを一覧表示します。

### `GET /api/characters/voice_preview`

音声をプレビューします（オーディオストリームを返します）。

**クエリ:** `voice_id`

### `POST /api/characters/clear_voice_ids`

全キャラクターに保存されたすべてのボイス ID レコードをクリアします。

## マイク

### `POST /api/characters/set_microphone`

入力マイクデバイスを設定します。

**ボディ:**

```json
{ "microphone_id": "device_id" }
```

### `GET /api/characters/get_microphone`

現在のマイク設定を取得します。
