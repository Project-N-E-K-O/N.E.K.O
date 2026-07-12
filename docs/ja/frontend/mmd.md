# MMD モデル

## 概要

N.E.K.O. は Three.js とその MMD ローダーで MMD（MikuMikuDance）モデルを描画します。組み込みモデルは `static/mmd/Miku/Miku.pmx` にあり、デフォルトで読み込まれます。モデルは会話から検出された感情に反応し、Live2D のようなモーションファイルではなく **モーフターゲット（ブレンドシェイプ / blendshape）** のウェイトで駆動されます。

## フォーマット

| 種類 | 拡張子 | 備考 |
|------|--------|------|
| モデル | `.pmx`、`.pmd` | Three.js の MMD ローダーで読み込み |
| アニメーション | `.vmd` | ポーズ / モーショントラック（待機、リップシンクなど） |

::: info
アップロード上限は **500 MB** です（テクスチャを含む MMD モデルは大きくなることがあります）。`main_routers/mmd_router.py` の `MAX_FILE_SIZE`（57〜64 行）を参照してください。ZIP パッケージにはさらに 2 GB の展開後上限と 10000 エントリの上限があり、zip bomb を防ぎます。
:::

## モデルのソース

| ソース | 場所 |
|--------|------|
| 組み込み | `/static/mmd`（例: `static/mmd/Miku/Miku.pmx`） |
| ユーザーがインポートしたモデル | `/user_mmd` |
| ユーザーがインポートしたアニメーション | `/user_mmd/animation` |
| Steam ワークショップ | `/workshop/<item_id>/...`（自動マウント） |

モデルごとの感情マッピングの上書きは `/user_mmd/emotion_config` 配下に `<model>.json` として保存されます。

## 描画と静的モジュール

ビューアは `static/mmd/` 配下の `mmd-*.js` モジュールで構成されます。

| モジュール | 役割 |
|------------|------|
| `mmd-core.js` | Three.js のシーン、レンダラー、MMD モデルの読み込み |
| `mmd-manager.js` | 各サブモジュールを統括する最上位マネージャ（`window.mmdManager`） |
| `mmd-init.js` | ブートストラップ / 初期化 |
| `mmd-animation.js` | VMD アニメーション再生とリップシンク値 |
| `mmd-expression.js` | モーフターゲット制御と感情システム（`mmd-expression.js`） |
| `mmd-interaction.js` | ポインタ / インタラクション処理 |
| `mmd-cursor-follow.js` | カーソル追従の挙動 |
| `mmd-ui-buttons.js` | MMD 専用のコントロールボタン |
| `mmd-ui-debug.js` | デバッグ用オーバーレイ |

## 感情マッピング

Live2D（感情を表情 + モーションファイルにマッピング）とは異なり、MMD の感情は **モーフターゲット / ブレンドシェイプ** のウェイトとして適用されます。`mmd-expression.js` には、感情ラベルから候補となるモーフ名（日本語 / 英語）へのデフォルト `moodMap` が同梱されています。例:

```javascript
{
  "happy":     ["笑い", "にやり", "にこり", "smile", "happy", "joy", "ワ"],
  "sad":       ["悲しい", "泣き", "sad", "sorrow", "しょんぼり"],
  "angry":     ["怒り", "angry", "anger", "むっ"],
  "surprised": ["驚き", "びっくり", "surprised", "shock", "おっ"],
  "relaxed":   ["穏やか", "relaxed", "calm", "微笑み"],
  "fear":      ["恐怖", "fear", "scared", "おびえ"]
}
```

感情が設定されると、`MMDExpression.setEmotion(emotion)` は候補のモーフ名を検索し、現在のモデルに存在する最初の名前を選び、そのウェイトを `1.0` に駆動します（一定の遅延後に自動で neutral に戻ります）。適用方法は次のとおりです。

```javascript
window.mmdManager.expression.setEmotion('happy');
```

各モデルはデフォルトのマップを上書きできます。フロントエンドは `GET /api/model/mmd/emotion_mapping?model=<name>`（`loadMoodMap()` 経由）を呼び出し、返ってきたマッピングをデフォルトに重ね合わせます。エディタは `POST /api/model/mmd/emotion_mapping` で上書きを保存します。

## モデル管理ページ

- `/model_manager` — MMD モデルとアニメーションの閲覧、アップロード、削除
- `/mmd_emotion_manager` — モデルごとの感情からモーフへのマッピングを設定

## REST API

**プレフィックス:** `/api/model/mmd`

`main_routers/mmd_router.py` で定義されています。すべての成功レスポンスは `success` ブール値を持つ JSON です。エラーレスポンスは適切なステータスコードとともに `{ "success": false, "error": "..." }` を使用します。

### `POST /api/model/mmd/upload`

MMD モデルファイル（`.pmx` / `.pmd`）をアップロードします。

**Body:** `multipart/form-data`（モデル `file`）。

**Response:** `{ success, message, model_name, model_url, file_size }`。ファイルは 1 MB チャンクでストリーミングされ `/user_mmd` に書き込まれます。同名のファイルが既に存在する場合は拒否されます。

### `POST /api/model/mmd/upload_animation`

`.vmd` アニメーションファイルをアップロードします。

**Body:** `multipart/form-data`（`.vmd` の `file`）。

**Response:** `{ success, message, filename, file_path }`。`/user_mmd/animation` に保存されます。

### `POST /api/model/mmd/upload_zip`

`.zip` パッケージ（モデル `.pmx`/`.pmd` とテクスチャ）をアップロードし、サブディレクトリへ自動展開します。

**Body:** `multipart/form-data`（`.zip` の `file`）。

**Response:** `{ success, message, model_name, model_url, file_count, file_size }`。

::: info
MMD パッケージは非 UTF-8 のファイル名をよく使うため、ZIP のファイル名は CJK 対応の判定（Shift-JIS / CP932、GBK、Big5、EUC-KR）でデコードされます。パストラバーサル、zip bomb、予約ディレクトリ名（`animation`、`emotion_config`）は拒否されます。
:::

### `GET /api/model/mmd/models`

MMD モデルを一覧します。組み込みの `static/mmd`、ユーザーの `/user_mmd`（再帰的に、予約ディレクトリはスキップ）、購読済みの Steam ワークショップアイテムを検索します。

**Response:** `{ success, models: [...] }`。各エントリには `name`、`filename`、`url`、`rel_path`、`type`、`size`、`location`（`project`、`user`、`steam_workshop`）が含まれます。有効なモデルファイルがない残骸ディレクトリは `broken: true` で返されます。

### `GET /api/model/mmd/animations`

組み込みの `static/mmd/animation` とユーザーの `/user_mmd/animation` 配下の `.vmd` アニメーションを一覧します。

**Response:** `{ success, animations: [...] }`（`name`、`filename`、`url`、`type`、`size`）。

### `GET /api/model/mmd/config`

MMD のパス設定を返します。

**Response:** `{ success, paths: { user_mmd: "/user_mmd", static_mmd: "/static/mmd" } }`。

### `GET /api/model/mmd/emotion_mapping`

モデルの感情マッピングを取得します。

**Query:** `model=<name>`。

**Response:** `{ success, mapping }`。上書きが保存されていない場合は空のマッピングを返します。パス区切り文字を含むモデル名は拒否されます。

### `POST /api/model/mmd/emotion_mapping`

モデルの感情マッピングを更新します。

**Body:** JSON `{ "model": "<name>", "mapping": { ... } }`。

**Response:** `{ success, message }`。マッピングは `/user_mmd/emotion_config/<model>.json` にアトミックに書き込まれます。

### `DELETE /api/model/mmd/model`

ユーザーがインポートしたモデル（およびそのディレクトリ内の関連リソース）を削除します。

**Body:** JSON `{ "url": "/user_mmd/<...>" }`。

**Response:** `{ success, message, deleted_files }`。サブディレクトリ内のモデルはサブディレクトリ全体を削除します。対応する `emotion_config/<model>.json` も削除されます。組み込みの `/static/mmd/` モデルは削除できません。

### `GET /api/model/mmd/animations/list`

削除可能なユーザーの `.vmd` アニメーション（`/user_mmd/animation` から）を一覧します。

**Response:** `{ success, animations: [...] }`（`name`、`filename`、`url`、`path`）。

### `DELETE /api/model/mmd/animation`

ユーザーがインポートした `.vmd` アニメーションを削除します。

**Body:** JSON `{ "url": "/user_mmd/animation/<file>.vmd" }`。

**Response:** `{ success, message }`。削除できるのは `/user_mmd/animation` 配下の `.vmd` ファイルのみです。
