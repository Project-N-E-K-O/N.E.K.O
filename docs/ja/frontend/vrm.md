# VRM モデル

## ランタイムと形式

VRM レンダラーは Three.js と `@pixiv/three-vrm` を使います。モデルは `.vrm`、アニメーションは通常 `@pixiv/three-vrm-animation` で読み込む `.vrma` です。

現在の実装は `static/vrm/` の core、manager、init、animation、expression、interaction、cursor-follow、orientation、UI モジュールにあります。VRM キャラクターが有効な場合だけ、`vrm-init.js` が `window.vrmManager` を作成し `#vrm-canvas` を初期化します。

## モデルとアニメーション

`GET /api/model/vrm/models` は `static/vrm/` 直下の同梱ファイル、`/user_vrm` のユーザーファイル、`/workshop/{item_id}/...` のインストール済み Workshop ファイルを統合します。API は公開 URL を返し、絶対ファイルシステムパスを公開しません。

アニメーションは `static/vrm/animation/` と `/user_vrm/animation/` から列挙されます。アップロードはモデル `.vrm` とアニメーション `.vrma` を受け付け、1 ファイルの上限は 200 MB です。ユーザーモデル削除は設定済み VRM ディレクトリ直下の `.vrm` だけに制限されます。

## ライティング

`config/character_defaults.py` のバックエンド既定値は、レンダラースクリプトより先に `window.VRM_DEFAULT_LIGHTING` としてテンプレートへ注入されます。現在のキーは次のとおりです。

```json
{
  "ambient": 0.83,
  "main": 1.91,
  "fill": 0.0,
  "rim": 0.0,
  "top": 0.0,
  "bottom": 0.0,
  "exposure": 1.1,
  "toneMapping": 7,
  "outlineWidthScale": 1.0
}
```

キャラクター別ライティングはこれを上書きできます。バックエンド既定値、テンプレートコンテキスト、`vrm-core.js` の防御的フォールバックを一致させてください。

## 感情マッピング

VRM の感情は意味名を順序付きの表情名候補へ割り当てます。

```json
{
  "neutral": ["neutral"],
  "happy": ["happy", "joy", "fun", "smile"],
  "surprised": ["surprised", "surprise", "shock", "e", "o"]
}
```

サーバーはモデル別マップを `static/vrm/configs/` に保存します。`vrm-expression.js` は保存値を既定値へ上書きマージし、表情名を大文字小文字を無視した完全一致で検索します。管理ページは保存前に `/api/model/vrm/expressions/{model_name}` から実際の表情名を取得できます。

VRM が有効なとき、`window.LanLan1.setEmotion(name)` は `window.vrmManager.expression.setMood(name)` に委譲されます。non-neutral の mood は実行時遅延後に neutral へ戻ります。

## VMC モーション出力

`vrm-vmc-sender.js` は animation、look-at、spring-bone、expression 更新後の active VRM を sample し、専用 `/api/vmc/ws` channel へ pose を送信します。backend が座標を変換し、VMC Protocol 対応 receiver へ OSC/UDP で出力します。

既定では無効です。無効時は軽量 facade のみを保持し、status poll、VMC timer、per-frame sampling は行いません。main page から既定の local destination を有効にします。

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

VMC root は `vrm.scene` から独立しているため、desktop layout transform は出力されません。model switch/dispose 時は ACK 付き zero-value frame で古い expression を解除し、disable/release の最後に `/VMC/Ext/OK 0` を送信します。

receiver 設定、control API、security boundary、close code、troubleshooting は [VMC モーション出力](/ja/api/rest/vmc) を参照してください。

## ランタイム保護

現在のレンダラーは長い停止後のフレーム delta をクランプし、読み込んだ spring-bone collider 半径を縮小し、ライティング設定から MToon アウトライン幅を調整します。これらは内部互換対策で、モデル形式の要件ではありません。再現のためにアップロード VRM を事前編集しないでください。

collider 半径は既定ですべて 50% に縮小されます（`VrmPhysicsProfile.DEFAULT_REDUCTION`）。これは経験値で、UniVRM のエクスポート時に collider 半径がスケールに合わせて正規化されない問題を打ち消すためのものです。特定のモデルで別の値が必要な場合は、全体の係数を変えずに、下記のモデル別物理設定で上書きしてください。

## モデル別物理設定

VRM の読み込み中に、`vrm-physics-profile.js` が `GET /api/model/vrm/physics_profile/{model_name}` を呼び、`static/vrm/configs/{model_name}_physics.json` を読みます。使える設定があれば collider 半径と spring joint のパラメータを設定どおりに書き込みます。ファイルがない、壊れている、リクエストが失敗した、4 秒を超えた場合は、上記の 50% 縮小に戻ります。リポジトリには `_physics.json` が 1 つも同梱されていないため、既定ではすべてのモデルが 50% 縮小になります。

`model_name` はモデル URL のファイル名から `.vrm` を除いたものです（先に URL デコードし、失敗した場合はそのまま使います）。感情マッピングと同じ名前です。リクエストは `cache: 'no-store'` なので、設定を編集したらモデルを読み込み直せば反映されます。コンソールに `[VRM] Physics profile: source=file|default, colliders=N, joints=N, reset=true|false` が 1 行出力され、実際に何件一致したかを確認できます。

設定は外部の Unity ツール [vrm-physics-tuner](https://github.com/ylh440104/vrm-physics-tuner) で書き出せます。このリポジトリはツールに依存しておらず、手書きでもかまいません。

### ファイル形式

トップレベルは JSON オブジェクトで、`colliders` と `chains` の少なくとも一方が配列である必要があります。そうでなければ設定なしとして扱います。ファイルは UTF-8 で、BOM（C# が既定で書き込むもの）も受け付けます。`NaN` / `Infinity` はエンドポイントで `null` に置き換えられ、その項目はスキップされます。

```json
{
  "colliders": [
    { "bone": "J_Bip_C_Head", "shape": "sphere", "offset": [0, 0.05, 0], "radius": 0.06 },
    { "bone": "J_Bip_C_Chest", "shape": "capsule", "offset": [0, 0, 0], "tail": [0, 0.1, 0], "radius": 0.08 }
  ],
  "chains": [
    {
      "bones": ["J_Sec_Hair1_01", "J_Sec_Hair2_01"],
      "stiffness": 1.0,
      "drag_force": 0.4,
      "gravity_power": 0.0,
      "gravity_dir": [0, -1, 0],
      "hit_radius": 0.02
    }
  ]
}
```

| フィールド | 説明 |
| --- | --- |
| `colliders[].bone` | collider が付いているボーンの glTF ノード元名 |
| `colliders[].shape` | `sphere` または `capsule`。three-vrm の collider の `shape.type` と同じ値です。省略すると形状を確認しません |
| `colliders[].offset` | ボーンのローカル空間でのオフセット。省略または `[0,0,0]` は「位置情報なし」を意味します（tuner は常に `[0,0,0]` を書き出します） |
| `colliders[].tail` | カプセルの末端。同じボーンにある複数のカプセルを優先的に区別するためだけに使います。tuner は Unity 座標で書き出すため、一致しなくてもエラーではありません |
| `colliders[].radius` | モデルのスケールが 1 のときの半径で、実行時に現在のスケールを掛けます。有限の非負数でなければ、その collider は 50% 縮小のままです |
| `chains[].bones` | このパラメータを適用するボーンの元名の一覧 |
| `chains[].stiffness`、`gravity_power` | 非負数 |
| `chains[].drag_force` | 範囲 [0, 1]（ソルバーは前フレームの変位を `1 - dragForce` で減衰させます） |
| `chains[].hit_radius` | 非負数。スケール 1 のときの値で、モデルのスケールに追従します |
| `chains[].gravity_dir` | 省略可能な 3 要素の配列 |

joint パラメータが範囲外の場合はその項目だけをスキップし、同じ chain の他のパラメータは通常どおり書き込みます。

### マッチング規則

- **ボーン名**: まず GLTFLoader が `userData.name` に残す glTF の元名で照合します。ノードに元名が記録されていない場合だけ、整形後の名前を使います（GLTFLoader は空白をアンダースコアに置き換え、`[ ] . : /` を削除します）。元名を持つノードが設定に見つからない場合は整形後の名前にフォールバックしないため、`hair.001` と `hair001` のように整形後に同名になるボーン同士が設定を借りることはありません。
- **collider**: モデルが元から持つ collider の半径だけを変更します。collider の追加・削除はせず、形状と位置も変えません。照合は、ボーン + 形状 + offset + tail、ボーン + 形状 + offset の順に試し、最後に設定側に位置情報がない場合だけ、同じボーン・同じ形状のものを出現順に対応させます。形状が違うもの、または offset が明確に一致しないものは未一致のまま 50% 縮小になります。
- **joint**: ボーンから chain を探します。同じボーンが複数の chain に含まれる場合は出現順に割り当てます。実行時の joint が設定の chain より多い場合、余った joint はモデル自身のパラメータを保ちます。
- 適用後に `springBoneManager.reset()` を呼び、spring を新しいパラメータからやり直させます。モデルをスケールすると、collider 半径は従来どおりスケールに追従します。joint の `hitRadius` は設定で書き込まれたものだけが追従し、設定のないモデルは従来どおり変わりません。

### 既知の制限

- **ファイル名で検索します。** 感情マッピングと同じく、設定はモデルのファイル名で探します。Workshop の別々のアイテムに含まれる同名の `model.vrm` は 1 つの設定を共有します。
- **ボーンパスではなくボーン名で照合します。** 同じボーンがパラメータの異なる複数の chain に含まれる場合は、出現順に割り当てます。
- **tuner の現在の書き出しでは、collider はほぼ一致しません。** 同梱の `sister1.0.vrm` では、tuner が書き出すのは 6 個のカプセル collider で、モデルの実行時 collider は 28 個の球です。その結果 collider は 0/28、joint は 71/71 が一致し、実際に効くのは spring joint のパラメータだけです。collider も効かせるには、tuner がモデルの既存 collider ごとに半径を書き出すよう変更する必要があります。

### ユーザー向け機能にする前に解決すること

現在この仕組みは開発者と調整作業向けで、インポート UI はなく、同梱や Workshop で配布される設定もありません。ユーザー向け機能にするには、少なくとも次の 3 点を先に解決する必要があります。

1. **保存場所。** 設定は `project_root/static/vrm/configs` から読みます。パッケージ版の `project_root` はインストールディレクトリで、ユーザーデータディレクトリではないため、ユーザーが置いたファイルを長く保管する場所に向きません。ユーザーが書き込めるディレクトリに移し、旧い場所も読み続ける必要があります。感情マッピングも同じディレクトリにあるので、一緒に移すのが望ましいです。
2. **検索に使う名前。** 設定を配布し始めると、ファイル名での検索では Workshop の同名モデルが設定を共有してしまいます。出どころを区別できる名前に切り替える必要があります。感情マッピングも同じ規則なので、両方を一緒に変えてください。
3. **形式のバージョン。** ファイルにはバージョン項目がなく、コードは `colliders` / `chains` の有無しか見ていません。tuner が形式を変える前に（たとえば collider ごとの半径を書き出す、項目の意味を変えるなど）、バージョン項目を追加し、旧いバージョンの扱いを決めてください。

## API 概要

| メソッド | エンドポイント | 用途 |
| --- | --- | --- |
| `POST` | `/api/model/vrm/upload` | `.vrm` モデルを 1 ファイルアップロード |
| `POST` | `/api/model/vrm/upload_animation` | `.vrma` アニメーションを 1 ファイルアップロード |
| `GET` | `/api/model/vrm/models` | 同梱、ユーザー、Workshop モデルを一覧 |
| `GET` | `/api/model/vrm/animations` | 同梱とユーザーアニメーションを一覧 |
| `GET` | `/api/model/vrm/config` | 公開 VRM URL プレフィックスを返す |
| `GET`、`POST` | `/api/model/vrm/emotion_mapping/{model_name}` | 表情マップを読み取りまたは保存 |
| `GET` | `/api/model/vrm/expressions/{model_name}` | モデル内の表情名を確認 |
| `GET` | `/api/model/vrm/physics_profile/{model_name}` | モデル別物理設定を読み取る |
| `DELETE` | `/api/model/vrm/model` | 公開 URL でユーザーモデルを削除 |

## ホスト境界

VRM は Electron ペットウィンドウを含む `index.html` に描画されます。独立したチャットと字幕テンプレートは第二の VRM シーンを作らず、ネイティブウィンドウは共通のクロスウィンドウブリッジでメインページと連携します。
