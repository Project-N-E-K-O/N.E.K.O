# MMD モデル

## ランタイムと形式

MMD レンダラーは PMX/PMD モデルと VMD アニメーションに対応します。Three.js がモデルを読み込み、`@moeru/three-mmd-physics-ammo` と Ammo が任意の剛体物理を提供します。

実装は `static/mmd/` の `mmd-core.js`、`mmd-manager.js`、`mmd-init.js`、`mmd-animation.js`、`mmd-expression.js`、`mmd-interaction.js`、`mmd-cursor-follow.js`、UI モジュールに分割されています。選択中のキャラクターが MMD を使う場合だけ、`mmd-init.js` が `window.mmdManager` を作成し `#mmd-canvas` を初期化します。

## モデルとアニメーションの配信元

`GET /api/model/mmd/models` は次を再帰的に統合します。

- `static/mmd/` の同梱 PMX/PMD ファイル
- `/user_mmd` のユーザーモデル
- `/workshop/{item_id}/...` のインストール済み Workshop モデル

VMD アニメーションは `static/mmd/animation/` と `/user_mmd/animation/` から列挙されます。モデルパッケージでは PMX/PMD と参照テクスチャの相対配置を維持してください。

PMX/PMD と VMD は直接アップロードできます。ZIP インポートはモデルとテクスチャを含む完全なパッケージ向けです。最初の PMX/PMD を選択し、既存の単一トップディレクトリを維持し、一般的な日本語/CJK ファイル名エンコーディングを補正し、絶対パスと親ディレクトリ移動を拒否して、ユーザーモデルのサブディレクトリに展開します。

上限はアップロード 1 ファイル 500 MB、ZIP 展開後合計 2 GB、ZIP エントリー 10,000 件です。

## 感情マッピング

MMD の感情は意味名を 1 つ以上のモーフ名に割り当てます。

```json
{
  "neutral": ["default", "ニュートラル"],
  "happy": ["笑い", "smile"]
}
```

マッピングはユーザー MMD ディレクトリの `emotion_config` にモデル別で保存されます。実行時の `mmd-expression.js` は保存値を内蔵候補へ上書きマージし、読み込んだモデルに存在する最初のモーフを選び、設定された遅延後に non-neutral 表情を neutral へ戻します。エディターは `neutral`、`happy`、`relaxed`、`sad`、`angry`、`surprised`、`fear` を公開します。

`/mmd_emotion_manager` で実際のモーフ名を確認して保存します。MMD が有効なとき、`window.LanLan1.setEmotion(name)` は `window.mmdManager.expression.setEmotion(name)` に委譲されます。

## 管理動作

`/model_manager` はモデルとアニメーションをインポートし、選択アバターをプレビューし、ユーザー所有コンテンツを削除します。同梱と Workshop アセットはこれらの API では読み取り専用です。パッケージサブディレクトリのユーザーモデルを削除すると、参照テクスチャを残さないようトップレベルパッケージ全体と感情マッピングを削除します。

## API 概要

| メソッド | エンドポイント | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/mmd/upload` | `.pmx` または `.pmd` を 1 ファイルアップロード |
| `POST` | `/api/model/mmd/upload_animation` | `.vmd` を 1 ファイルアップロード |
| `POST` | `/api/model/mmd/upload_zip` | モデルパッケージをインポート |
| `GET` | `/api/model/mmd/models` | 同梱、ユーザー、Workshop モデルを一覧 |
| `GET` | `/api/model/mmd/animations` | 同梱とユーザー VMD を一覧 |
| `GET` | `/api/model/mmd/config` | 公開 MMD URL プレフィックスを返す |
| `GET`、`POST` | `/api/model/mmd/emotion_mapping` | モデル別モーフマップを読み取りまたは保存 |
| `DELETE` | `/api/model/mmd/model` | 公開 URL でユーザーモデル/パッケージを削除 |
| `GET` | `/api/model/mmd/animations/list` | 削除可能なユーザーアニメーションを一覧 |
| `DELETE` | `/api/model/mmd/animation` | 公開 URL でユーザー VMD を削除 |

## ホスト境界

MMD は `/` を読み込む Electron ペットウィンドウを含む、メインページのアバターサーフェスだけに描画されます。`/chat` と `/subtitle` は独立ウィンドウなので、別の MMD シーンを作らずメインページと通信します。
