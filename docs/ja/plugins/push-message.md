# メッセージ配信

このページでは、プラグインが会話へ内容を届ける方法を説明します。`push_message()` の 2 つの配信軸、`parts` の種類とサイズ制限、画像 upload、そして `finish()` の配信モード・結果セマンティクス・ロールプレースホルダーです。メソッド一覧は [プラグイン機能と SDK リファレンス](./sdk-reference) を参照してください。

## `push_message(**kwargs) -> PushMessageResult`

`push_message` はプラグイン → ホストの**唯一**のメッセージ送信入口です。2 つの独立した軸が下流の挙動を決め、順序付きの `parts` リストが OpenAI 風のマルチモーダル内容を運びます。完全な schema は `plugin/sdk/shared/core/push_message_schema.py` にあります。

```python
self.push_message(
    visibility=[],                  # ["chat"] / ["hud"] / ["chat","hud"] / []
    ai_behavior="respond",          # "respond" / "read" / "blind"
    parts=[                         # 順序付き parts（chat は順序を保持。モデル側の制限は下記）
        {"type": "text",  "text": "これを見て"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
        {"type": "ui_action",
         "action": "media_play_url",
         "url": "https://example.com/song.mp3",
         "media_type": "audio"},
    ],
    source="my_feature",
    target_lanlan="灵",             # 任意：特定 session へルーティング
    metadata={...},
    priority=0,                     # 数値が大きいほど優先度が高い
)
```

### 送信結果

戻り値はローカル送信の結果です：

```python
result = self.push_message(
    visibility=[],
    ai_behavior="respond",
    parts=[{"type": "text", "text": "このイベントに応答してください"}],
)
if not result["submitted"]:
    # ローカルの作業は保持し、再試行するかはプラグイン自身の方針で決めます。
    # メッセージ本文はログに書かないでください。
    self.logger.warning("message submission failed: %s", result.get("reason"))
```

`submitted=True` は、SDK が payload を正規のローカル送信経路に渡し、以後の送信責任を引き受けたことだけを示します。ホストでの消費、AI の返信生成、音声の再生完了を意味しません。

`submitted=False` には安定した `reason` が付きます：`backpressure`、`transport_error`、`transport_unavailable`、`payload_too_large`。最初の 3 つはその時点の転送状況（混雑 / 送信失敗 / 利用可能な経路なし）を表すため、時間をおいて同じ内容を再送する意味があります。`payload_too_large` は別種で、SDK が**送信前に**ローカルで計測します。パック後の payload 全体が `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` からホスト用予備 227 バイトを引いた値を超えた（判定は下の[サイズ制限](#サイズ制限)）ので、同じ内容の再試行は必ず同じ結果になります。push を小さくするしかありません。inline 画像は `ctx.images.upload()` で URL part に置き換えてください。`audio` / `video` には今のところ対応するアップロード API がないため、自分で小さくする（短いクリップ、低いビットレートや解像度）か、自前でホストして `data=` の代わりに `url=` を使ってください。

結果は内部 transport 名を公開せず、メッセージ本文や例外内容も返しません。拒否結果には従来の呼び出し元向けに `ok=False` も含まれますが、新しいコードでは `submitted` を正式な判定基準にしてください。再試行と重複排除はプラグイン側の方針です。

### 2 つの軸

**`visibility`** — プラグインの parts をそのままユーザーに表示する先（**AI とは無関係**）。`["chat", "hud"]` のように複数指定できます：

| 値 | 意味 |
|---|---|
| `"chat"` | parts をチャット欄に**そのまま**吹き出しとして表示 |
| `"hud"`  | parts を agent UI / HUD 通知パネルに表示 |
| `[]`（既定） | ユーザーは parts を**直接は**見ません。`ai_behavior="respond"` なら AI の返信が見えます |

**`ai_behavior`** — LLM が parts をどう扱うか：

| 値 | LLM コンテキスト | 返信 turn を開始 | 言及されるタイミング |
|---|---|---|---|
| `"respond"`（既定） | ✅ | ✅ 即座に | AI がすぐに応答 |
| `"read"`  | ✅ | ❌ 割り込まない | 次にユーザーが話したとき自然に言及 |
| `"blind"` | ❌ | ❌ | AI には見えない |

### `parts` の種類

各 part は `type` discriminator を持つ dict です：

| `type` | フィールド | 用途 |
|---|---|---|
| `text` | `text: str` | プレーンテキスト |
| `image` | `data: bytes` + `mime`、または `url` + `mime` | 画像（inline またはリモート） |
| `audio` | image と同じ | 音声（schema 上の予約。**現在 AI 注入経路では未処理**で warn-drop） |
| `video` | image と同じ | 動画（schema 上の予約。**現在 AI 注入経路では未処理**で warn-drop） |
| `ui_action` | `action: str` + action ごとのフィールド | フロントエンド UI の副作用 |

inline の `data: bytes` は SDK が base64 エンコードして送ります。push_message を拡張するときは新しい part `type` を追加し、`message_type` の値は増やさないでください。

### 現在の実装上の制限

- `ai_behavior in ("respond","read")` のとき、inline の `image` parts と `ctx.images.upload()` が返すローカル一時 URL はどちらも LLM コンテキストに入ります（最終的に `session.stream_image(base64)`）。任意の外部 URL は拒否され、リモート取得を agent event 配信経路に持ち込みません。1 メッセージでモデルに注入されるのは最大 8 枚・合計 8 MiB までです。超えた image parts は `visibility` に従って表示はされますが、モデルコンテキストには入りません。
- 8 枚 / 8 MiB は **1 回の push** の上限です。text mode の `ai_behavior="read"` の画像はすぐには送られず、ユーザーの次の発話まで保留されるため、**1 つの turn** に複数回の push 分が溜まることがあります。保留は送信元ごとに別枠で、互いの枠を侵食しません：

  | 送信元 | 枚数 | バイト |
  | --- | --- | --- |
  | ユーザー自身のスクリーンショット / カメラフレーム | 5 | 16 MiB |
  | プラグインの `read` 画像 | 3 | 8 MiB |
  | 能動的な話しかけで残ったスクリーンショット | 1（専用スロット、TTL 付き） | — |

  枠を超えたときに削られるのは**常に同じ送信元の最も古い 1 枚**です。プラグインがどれだけ送ってもユーザーのフレームは奪われず、その逆も同様です。そのため 1 turn は最大 9 枚になり、1 回の push の 8 枚より 1 枚多くなります。これは意図的で、合計枠を共有すると 2 つの送信元のどちらかを犠牲にする選択が必要になり、正解がないためです。
- `parts` の順序は**チャット表示**では保持されます。しかし**モデル経路では順序は保持されません**。画像は抜き出されて先に注入され、テキストは 1 つにまとめて後から渡されます。「説明 A、画像 A、説明 B、画像 B」の交互配置でモデルに対応付けさせることはできないので、対応関係は 1 つのテキストに書いてください（「1 枚目は…、2 枚目は…」）。
- `visibility=["chat"]` は image parts を表示できますが、HUD 通知は現在 text part のみ表示します。
- `audio` / `video` には現在 realtime 注入経路がなく（`stream_audio` はライブ PCM マイク専用、video の API は存在しない）、どちらも warn-drop されます。当面は `ai_behavior="blind"` + `ui_action` でフロントエンドだけで表示する用途に限ってください。Hosted UI パネル内で再生することもできます。

### サイズ制限

inline part は message plane 経由で ZMQ を通り、payload 全体の上限は `MESSAGE_PLANE_PAYLOAD_MAX_BYTES` = 524288（512 **KiB** = 512×1024。十進の 512 KB ではありません）です。これは出荷時の既定値で、環境変数 `NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES` で上書きできます。以下の数値はすべて既定値を前提にしています。計測対象は**パック後**の envelope で、元画像のバイト数ではありません。inline 画像は `parts[].binary_base64` に base64 で入り、元のバイト数の 4/3（+33%）になります。したがって inline 画像 1 枚の元バイト上限は 512 KiB × 3/4 ≈ **384 KiB** で、そこから数百バイトの envelope と同じメッセージ内の text part を引いた値です。実測では、256 KiB の画像と短い文をパックすると 341.8 KiB で、上限まで約 170 KiB の余裕があります。524288 バイトの上限ちょうどに収まる元画像サイズは約 383.6 KiB です。ローカルチェック（下記）はさらに 227 バイトの余裕を確保するため、同じ形のメッセージが実際に止められる位置は約 0.2 KiB 低くなります。小数点以下まで詰めず、およそ 383 KiB を目安にしてください。

この実効上限はかつてずっと低いものでした。v2 へ未移行の下流コンシューマーのため、wire envelope は以前 inline 画像を**2 回**運んでいました。`parts[].binary_base64` の base64 と、legacy の `binary_data` フィールドの生バイトで、合計は元画像の約 2.34 倍です。そのため当時の 256 KiB 上限に実際に収まるのは約 110 KiB の画像まででした。現在 `_build_wire_payload`（`plugin/core/context.py`）が legacy フィールドを埋めるのは、呼び出し側が `parts=` と `binary_data=` を両方渡したときだけで、通常の inline 画像は base64 で 1 回だけ運ばれます。重複の除去と上限の 512 KiB への引き上げが揃って、256 KiB の画像が通るようになりました。

上限超過は**ローカルで止められます**。`push_message()` は message plane に渡す前にパックして計測し、パック後のサイズが「上限 − 227 バイトの予備」を超えていれば `submitted=False` と `reason="payload_too_large"` を返します。そのメッセージは 1 バイトも送信されず、どの part（`image` / `audio` / `video`）が予算を使ったかがログに出ます。227 バイトはホスト用の予備です。ホストは計測前にレコードへ `plugin_id` / `message_id` / `time` を補うため、その最悪ケース分を差し引いておき、SDK が通した push がサイズを理由に ingest で捨てられないようにしています。プラグイン側とホスト側（`plugin/message_plane/ingest_server.py`）は同じ定数を読み、ホスト側のチェックは最後の防御として残ります。

- このチェックは**すべての**送信出口にあります。バッチの高速経路、同期経路、ZMQ が使えないときの legacy 制御キューのいずれも、先に payload 全体を計測します。
- 計測対象は inline 画像だけでなく payload 全体です。巨大なテキストや metadata も拒否されます。唯一の例外は `NEKO_MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES`（既定で有効）を明示的に無効にした環境で、その場合 SDK もホストも計測しません。
- 拒否は **push 全体**に及びます。同じメッセージ内の text part や `ui_action` も一緒に拒否されます。
- `submitted=True` の意味は変わりません。ホストの backpressure やプロセス再起動で、メッセージが黙って失われる可能性は残ります。

1080p のスクリーンショットを inline で送ろうとしないでください。JPEG q70 や 256×256 PNG に縮小するか、下の upload API を使ってください。

### 画像 upload：`ctx.images.upload()`

大きな画像には別の一時 upload API を使います。スレッドプールで最長辺 2048 以下の JPEG に正規化し、専用の media transport で upload するため、`push_message` の 512 KiB payload を消費しません：

```python
image_part = await self.ctx.images.upload(image_bytes, mime="image/png")
self.push_message(
    visibility=["chat"],
    ai_behavior="respond",
    parts=[{"type": "text", "text": "この画像を見て"}, image_part],
)
```

`images.upload()` は現在のランタイムで使える一時リソースを用意するだけで、画像の表示、モデルコンテキストへの書き込み、返信の開始は行いません。配信は引き続き `push_message()` が決めます。plugin entry、timer、message、custom event handler から呼び出してください。lifecycle handler（`startup` / `freeze` / `unfreeze` / `shutdown` / `config_change`）の実行中は、timeout を待たずに即座に `RuntimeError` を送出します。

**`upload()` のハードエラー** — 以下はすべて**例外を送出**し、小さな画像に黙って縮退することはありません。ユーザー提供の画像を渡すときは自分で `try` してください：

| チェック | 定数 / 判定 | 超過時 |
|---|---|---|
| 元画像のバイト数 | `MAX_SOURCE_IMAGE_BYTES` = 33554432（32 MiB） | `ValueError` |
| 元画像のピクセル数 | `MAX_SOURCE_IMAGE_PIXELS` = 16777216（16 MP、幅 × 高さ） | `ValueError` |
| 正規化後のバイト数 | `MAX_UPLOADED_IMAGE_BYTES` = 8388608（8 MiB） | `ValueError` |
| デコードスロット待ち | プロセス全体で 2 スロットのみ。待ち時間は指定した `timeout`（既定 3s、上限 30s）に含まれる | `TimeoutError` |
| 出力の最長辺 | `MAX_IMAGE_EDGE` = 2048 | エラーなし。等比縮小 |

正規化後のバイト数は見落としがちです。32 MiB / 16 MP を通過した元画像でも、再エンコード後の JPEG が 8 MiB を超えることがあり（ノイズの多い大きな画像は圧縮しにくい）、その例外は**デコード後**に出るため CPU はすでに消費されています。downlink が未準備、transport が応答しない場合も `TimeoutError` です。空の bytes は `ValueError`、bytes 以外は `TypeError` です。

**アニメーション画像は平坦化されます**：フレーム 0 だけが使われ、出力は常に単一フレームの JPEG で、エラーもログも出ません。`mime=` は呼び出し側のラベルにすぎず、実際の形式は Pillow が判定し、出力は常に JPEG です。アニメーションが必要なら `ui_action` でフロントエンドに再生させてください。

**高解像度で得られるのはチャット表示の画質で、モデルの精度ではありません**：モデルに送る画像はすべて**無条件に**まずモデル用プロファイルへ再エンコードされます。幅は最大 `MODEL_IMAGE_MAX_WIDTH`（1280）、高さは最大 `COMPRESS_TARGET_HEIGHT`（720）、JPEG q80 です（`utils/screenshot_utils.py` の `normalize_image_for_model`）。すでにプロファイル内の画像はそのまま通り、繰り返し再エンコードされることはありません。最長辺 2048 で upload した画像も、モデルに届く時点では 720p クラスです。その後も 1 turn の画像合計が `TURN_ATTACHED_IMAGE_MAX_TOTAL_BYTES`（8 MiB）を超える場合に限り、段階的な縮退が始まります。まず先頭・中央・末尾の 3 枚に間引き、残りをもう一度圧縮し、それでも足りなければ最も古いものから落とします。ユーザーに通知されるのは画像が丸ごと落ちたときだけです。チャット表示用のコピーはこれらを一切通らず、upload した解像度を保ちます。

### よくある組み合わせ

```python
# 1. AI に伝えてもらう（最も一般的）：プラグインのテキスト → LLM コンテキスト → AI が即座に返信
self.push_message(parts=[{"type": "text", "text": "ユーザーが 100 元の投げ銭をしました"}])

# 2. AI に静かに知らせる：次にユーザーが話したとき自然に言及
self.push_message(
    visibility=[],
    ai_behavior="read",
    parts=[{"type": "text", "text": "配信ルームに新しいコメントが届きました"}],
)

# 3. HUD でユーザーにだけ通知し、AI には関与させない
self.push_message(
    visibility=["hud"],
    ai_behavior="blind",
    parts=[{"type": "text", "text": "プラグイン X が起動しました"}],
)

# 4. チャットに再生カードを直接表示し、AI には知らせない
self.push_message(
    visibility=["chat"],
    ai_behavior="blind",
    parts=[{
        "type": "ui_action",
        "action": "media_play_url",
        "url": "https://music.example.com/song.mp3",
        "media_type": "audio",
        "name": "Test Song",
        "artist": "Bot",
    }],
)

# 5. テキストと画像を同時に AI へ
self.push_message(
    parts=[
        {"type": "text",  "text": "この画像を見て"},
        {"type": "image", "data": img_bytes, "mime": "image/png"},
    ],
)
```

`message_type`、`content`、`delivery`、`reply` などの v1 フィールドは非推奨ですが、現在も変換され `DeprecationWarning` を出します。フィールドごとの置き換えは [`push_message` v2 のメモ](/changelog/plugin-push-message-v2) と [移行ガイド](./migration-v0.9#push-message-v2) を参照してください。

## 音楽再生ドメインの許可リスト

N.E.K.O は既定で `music.163.com` などの既知の安全なドメインからの音声だけを再生します。新しい音声ソースを使うプラグインは、再生前にそのドメインを許可リストに追加する必要があります。

### バックエンドプラグイン（Python SDK）

`ui_action: media_allowlist_add` を push してから再生を開始します：

```python
from urllib.parse import urlparse

MUSIC_HOST = "music.my-cdn.com"

@plugin_entry(id="play_external")
async def play_external(self, url: str, **_):
    # 許可リストには自分の固定ソースだけを入れ、呼び出し側が渡した url は入れないこと。
    # url は LLM が選んだものかもしれず、そのまま入れると許可リスト自体を迂回できてしまう。
    if urlparse(url).hostname != MUSIC_HOST:
        return Err(SdkError("unsupported music source"))
    result = self.push_message(
        ai_behavior="blind",
        parts=[{"type": "ui_action", "action": "media_allowlist_add", "domains": [MUSIC_HOST]}],
    )
    if not result["submitted"]:
        # 許可リストに入らなければ、フロントエンドは結局再生を止める
        return Err(SdkError(f"allowlist push failed: {result.get('reason')}"))
    self.push_message(
        visibility=["chat"],
        ai_behavior="blind",
        parts=[{"type": "ui_action", "action": "media_play_url", "url": url, "media_type": "audio"}],
    )
    return Ok({"status": "playing", "url": url})
```

旧 SDK helper の `self.register_music_domains(...)` は削除されました。

### プラグインページからは `window.MusicPluginAPI` を使えません

`window.MusicPluginAPI`（`getAllowlist()` / `addAllowlist()`）と `music-ui-ready` イベントは、`music_ui.js` を読み込むメインページ（`index.html` / `chat.html`）にしか存在しません。`register_static_ui` で提供するページはプラグインマネージャー内の `<iframe>` で開かれます。プラグインマネージャーは別ポート（別オリジン）で動き、`music_ui.js` も読み込まないため、ページの `window` にも `window.parent` にもこの API はありません。上の節のとおり、バックエンドから `media_allowlist_add` を push して許可リストに追加してください。

## `finish()` の配信モード

`finish()` は `delivery` でタスク結果をメイン AI にどう届けるかを決めます（既定 `"proactive"`）：

| `delivery` | LLM コンテキスト | turn を開始して報告 | フロントエンド HUD / 通知 |
|---|---|---|---|
| `"proactive"`（既定） | ✅ | ✅ 即座に | ✅ |
| `"passive"` | ✅ コンテキストに書き込む | ❌ 割り込まない。次のユーザー発話で AI が自然に言及 | ✅ |
| `"silent"` | ❌ AI は知らない | ❌ | ✅（task_update のみ） |

```python
# 通常：キャラクターがすぐに結果を報告
return await self.finish(data={"summary": "今日は晴れ"})

# 静かな通知：コンテキストに入るが割り込まない。次にユーザーが話したときに触れる
return await self.finish(data={"summary": "ポモドーロが終わりました"}, delivery="passive")

# 完全にサイレント：AI は知らず、フロントエンドは task_update でのみ終了状態を見る
return await self.finish(data={"summary": "..."}, delivery="silent")
```

`reply=True` / `reply=False` は非推奨のブール別名で、それぞれ `"proactive"` / `"silent"` と同じです。両方指定した場合は `delivery` が優先されます。`push_message()` は `delivery` を使わず、`visibility` + `ai_behavior` の 2 軸を使います。

## 結果のセマンティクス：タスク報告とイベント応答

メイン AI が通知を受け取ると、ホストは外側のプロンプトで包みます。その文面は 2 種類です：

| 呼び出し | ホストの分類 | 外側プロンプトの趣旨 |
|---|---|---|
| `await self.finish(...)`（既定） | `task_result` | 「{あなたのプラグイン}のタスクが完了しました。ご主人に**報告**してください…」 |
| `result_kind="event"` を宣言した照会/即時応答 entry | `event` | 「{あなたのプラグイン}からの**新しいメッセージ**です。内容に沿ってご主人に**応答**してください…」 |
| `self.push_message(...)` | `event` | 「{あなたのプラグイン}からの**新しいメッセージ**です。内容に沿ってご主人に**応答**してください…」 |

- タスク報告：プラグインが呼び出されて完了した。AI は「何をして、どうなったか」を伝えるべきです。
- イベントの流れ：プラグインが外部イベント（コメント、IM メッセージ、タイマー）を監視している。AI は「さっき処理しました…」と語るのではなく、**イベントそのものに応答**するべきです。

照会、状態の読み取り、「開始しました」のような即時応答は、明示的にイベントのセマンティクスに格下げしてください。静的に宣言できます：

```python
@plugin_entry(
    id="service_status",
    metadata={"result_kind": "event", "expires_in_s": 30},
)
```

1 回の実行結果で静的宣言を上書きすることもできます：

```python
return await self.finish(
    data={"status": "running"},
    meta={"agent": {"result_kind": "event", "expires_in_s": 10}},
)
```

優先順位は「実行時 `meta.agent` > entry の静的 `metadata` > 既定の `task_result`」です。`expires_in_s` は `event` 結果にだけ効き、期限切れの応答はメイン AI に届きません。ホストが `event` への格下げを許すのは成功した `user_plugin task_result` だけで、`proactive_message` をタスク完了に見せかけることはできません。`result_kind` は `visibility` / `ai_behavior` / `delivery` と直交し、外側プロンプトの文面だけを決めます。

## ロールを意識したテキスト：`{MASTER_NAME}` / `{LANLAN_NAME}`

プラグインは `finish()` の `data.summary` / `data.detail`、`push_message()` の `parts[*].text` などを通じて文字列を会話の LLM コンテキストに入れます（`direct_reply` では TTS / チャット吹き出しに直接入ります）。これらの文字列に `"用户"` / `"user"` / `"master"` / `"主人"` を**ハードコードしないでください**：

- **口調が不自然**：AI は「ユーザーに報告…」をそのまま読み上げ、実際の `master_name` を使いません。
- **複数キャラクターでの破綻**：各 `LLMSessionManager` は自分の `lanlan_name` を持ちます。プラグインが複数のキャラクターに送ると、ハードコードした文面では全員が同じ言い回しになります。

| プレースホルダー | 置換先 |
|---|---|
| `{MASTER_NAME}` | 現在の session の `master_name`（ユーザーが付けた名前） |
| `{LANLAN_NAME}` | 現在の session のキャラクター名 |

ホストは LLM 注入点（`main_logic.core.apply_role_placeholders`）で対象 session ごとに展開します。**プラグイン側で置換しないでください**。visibility によるフィルタリングはホスト側で行われ、プラグインはメッセージがどの session に届くかを知りません。

```python
# ✅ 推奨：ホストに session ごとに展開させる
await self.finish(data={
    "summary": "最新の画面をもとに、さっき起きたことを {MASTER_NAME} にすぐ説明する",
})

self.push_message(parts=[{
    "type": "text",
    "text": "{MASTER_NAME} がコメントを送りました：『...』",
}], ai_behavior="respond")

# ❌ 非推奨：ハードコード。口調が一般化し、複数キャラクターで破綻する
await self.finish(data={"summary": "すぐにユーザーに説明する..."})
```

実装の詳細：

- **置換は `str.format` ではなく `str.replace`**：`detail` に JSON 断片、コード、`{` を含むユーザーの原文があっても `KeyError` は起きません。
- **名前が空ならプレースホルダーはそのまま残る**：ホストが名前を取得できない場合（初期化中のまれなケース）、`{MASTER_NAME}` は空文字にならず原文に残り、文が壊れません。
- **綴りは固定**：`{MASTER_NAME}` / `{LANLAN_NAME}`（大文字、アンダースコア、単一の波括弧）だけを使ってください。`{master_name}` / `{master}` / `{MASTER}` はホスト内部テンプレートの `.format(...)` 用で、プラグイン境界を越えません。

適用範囲：

| 経路 | 展開される |
|---|---|
| `finish(data={"summary": ..., "detail": ...})` | ✅ |
| `push_message(parts=[{"type": "text", ...}])` の LLM コンテキスト入り | ✅ |
| `task_result` + `direct_reply=True`（LLM を経由せず直接 TTS） | ✅ |
| `push_message(visibility=["chat"], ai_behavior="blind")` で直接チャット吹き出しへ | ✅ |
| `push_message(visibility=["hud"])` の HUD トースト | ✅ |
| 静的な説明フィールド（`plugin.toml` の `description`、entry の `name` など） | ❌ 展開されない（開発者 / UI 向けで、会話経路に入らない） |
