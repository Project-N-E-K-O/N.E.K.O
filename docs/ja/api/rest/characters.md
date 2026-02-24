# Characters API

**プレフィックス:** `/api/characters`

AI キャラクター（内部では「catgirl」または「lanlan」と呼ばれます）を管理します。CRUD 操作、音声設定、マイク設定を含みます。

## キャラクター管理

### `GET /api/characters/`

オプションの言語ローカライズ付きで全キャラクターを一覧表示します。

**クエリ:** `language`（オプション） — 翻訳されたフィールド名のロケールコード。

---

### `POST /api/characters/catgirl`

新しいキャラクターを作成します。

**ボディ:** パーソナリティフィールドを含むキャラクターデータオブジェクト。

---

### `PUT /api/characters/catgirl/{name}`

既存のキャラクター設定を更新します。

**パス:** `name` — キャラクター識別子。

**ボディ:** 更新されたキャラクターデータ。

---

### `DELETE /api/characters/catgirl/{name}`

キャラクターを削除します。

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

## Live2D モデルバインディング

### `GET /api/characters/current_live2d_model`

現在のキャラクターの Live2D モデル情報を取得します。

**クエリ:** `catgirl_name`（オプション）、`item_id`（オプション）

### `PUT /api/characters/catgirl/l2d/{name}`

キャラクターの Live2D モデルバインディングを更新します。

**ボディ:**

```json
{
  "live2d": "model_directory_name",
  "live2d_item_id": "workshop_item_id"
}
```

### `PUT /api/characters/catgirl/{name}/lighting`

キャラクターの VRM ライティング設定を更新します。

**ボディ:**

```json
{ "brightness": 0.8 }
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

利用可能な TTS 音声を一覧表示します。

**クエリ:** `voice_provider`（オプション） — プロバイダーでフィルタリング。

### `GET /api/characters/voice_preview`

音声をプレビューします（オーディオストリームを返します）。

**クエリ:** `voice_id`、`text`、`provider`

### `POST /api/characters/voices`

カスタム音声設定を追加します。

### `DELETE /api/characters/voices/{voice_id}`

カスタム音声を削除します。

### `POST /api/characters/voice_clone`

オーディオサンプルから音声をクローンします。

**ボディ:** オーディオファイルを含む `multipart/form-data`。

## マイク

### `POST /api/characters/set_microphone`

入力マイクデバイスを設定します。

**ボディ:**

```json
{
  "device_name": "Built-in Microphone",
  "device_id": "default"
}
```

### `GET /api/characters/get_microphone`

現在のマイク設定を取得します。

## キャラクターカード

### `GET /api/characters/character-card/list`

キャラクターカードファイルを一覧表示します。

### `POST /api/characters/character-card/save`

キャラクターカードを保存します。

### `POST /api/characters/catgirl/save-to-model-folder`

Workshop パブリッシュ用にキャラクターデータをモデルフォルダに保存します。
