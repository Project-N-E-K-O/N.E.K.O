# VMC モーション出力

**プレフィックス:** `/api/vmc`

N.E.K.O. は、アクティブな VRM アバターの humanoid bone と expression を OSC/UDP で VMC Protocol 対応 receiver へ送信できます。sender は既定で無効、送信先は `127.0.0.1:39539`、送信頻度は 60 Hz です。VRM モデルがアクティブな間だけ frame を生成します。

first-party UI では `window.vrmVmcSender` を使用してください。以下の REST/WebSocket は実装向けで、VRM runtime とともに変更される可能性があります。

browser は最初に軽量 API facade だけを読み込みます。`enable()` や `syncStatusFromBackend()` などの control method を呼ぶまでは、full sender の load、status poll、VMC timer、per-frame sampling、既存 VRM frame limiter の変更を行いません。

## クイックスタート

1. VSeeFace、Warudo、Unity/Unreal の VMC integration などを起動します。
2. receiver の UDP listen port を `39539` に設定します。
3. N.E.K.O. で VRM character を読み込みます。
4. main page で出力を有効にします。

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

追加操作:

```js
await window.vrmVmcSender.requestTPose(2)
await window.vrmVmcSender.disable()
```

送信先と rate は保存されますが、backend 再起動後の出力は意図的に無効から始まります。

## 出力

backend は Three.js の右手座標を Unity/VMC 座標へ変換し、`/VMC/Ext/OK`、`/VMC/Ext/T`、`/VMC/Ext/Root/Pos`、`/VMC/Ext/Bone/Pos`、`/VMC/Ext/Blend/Val`、`/VMC/Ext/Blend/Apply` を送信します。加えて `/VMC/Ext/VRM` を低頻度で送信します（model 変更時に一度だけ。receiver 側で現在の model を識別するため）。

1 frame あたり bone は最大 64、expression は最大 256 です。2 つの上限は独立に適用されます。bone が 65 本の frame は expression の数に関わらず切り詰められ、その逆も同様です。first-party sampler は固定の 55 humanoid bone を走査するため、bone の上限に達し得るのは `/api/vmc/ws` へ直接送る third-party publisher だけです。expression の上限は browser 側でも到達可能で、custom expression が数百ある model が該当します。

超過分は破棄され、上限ごとに warning が一度だけ記録されます。sampler は送信前に切り詰めるため、browser publisher の expression warning は browser console に出ます。backend 側の warning は third-party publisher を対象とします。

画面表示用の位置・scale・rotation は VMC root に使いません。VMC は独立した identity root を持つため、desktop avatar の移動や resize は receiver の world origin に影響しません。

無効化、送信先変更、VRM release の際は active expression を 0 にしてから `/VMC/Ext/OK 0` を送信します。release frame の ACK 後に専用 socket を閉じます。

browser publisher の予期しない切断には 2 秒の grace period があります。その間に replacement publisher が authentication を完了すれば terminal transition なしで継続し、再接続されなければ backend が active expression を 0 にして `/VMC/Ext/OK 0` を送信します。

## REST control plane

mutation route には same-origin CSRF header が必要です。first-party code では security header を手動作成せず `window.vrmVmcSender` を使用してください。

### `GET /api/vmc/status`

`enabled`、`host`、`port`、`send_rate_hz`、T-pose state など、現在の runtime state を返します。

### `POST /api/vmc/enable`

すべての JSON field は省略可能です。

```json
{
  "host": "127.0.0.1",
  "port": 39539,
  "send_rate_hz": 60
}
```

`host` は ASCII hostname または IPv4、`port` は `1..65535` の整数、`send_rate_hz` は `1..120` の整数です。

frame の sampling は browser 側が担当するため、disabled から enabled への遷移時に backend が chat WebSocket 経由で `{"type": "vmc_state_changed", "enabled": true}` を broadcast し、page が完全な sender を読み込んで per-frame sampling を開始します。plugin などの非 browser client は、page の console で `enable()` を手動実行せずにこの endpoint だけで有効化できます。page が未接続の場合、UDP sender は開きますが接続されるまで frame は送出されません。

再度呼び出しても（`port` の変更のみなど）sampling は既に動作しているため broadcast は行われません。

### `POST /api/vmc/disable`

terminal VMC state を送信し、UDP client を閉じて disabled state を返します。

### `POST /api/vmc/t_pose`

```json
{
  "duration_sec": 2
}
```

正の有限値を指定します。最大 10 秒に制限されます。

## WebSocket data plane

`/api/vmc/ws` は chat socket とは分離された first-party data channel です。allowed local Origin から接続し、5 秒以内に CSRF token を含む `auth` を送り、`ready` 後に sequenced `frame` / `release` envelope を送信します。

process-wide publisher は 1 つだけです。server は最新の pending normal frame を 1 件だけ保持し、in-flight frame の後に release を直列化し、10 秒間 valid frame がない publisher を解放します。

browser はこの socket を `window.WebSocket` ではなく、隠し同一 origin iframe から借用した constructor で開きます。desktop build の preload は top-level constructor を差し替え、最後に作られた socket を URL で区別せず chat IPC proxy target として登録するため、そこから作った VMC socket は chat channel を奪ってしまいます。probe iframe を作成できず（CSP `frame-src`、sandbox）**かつ** top-level constructor の source が `[native code]` でない場合、VMC は reconnect を続けずに接続を拒否し、理由を log して sampling を停止します。motion 出力は復旧できますが、奪われた chat channel は復旧できません。

後者は heuristic であり、host についての事実ではありません。通常の browser の constructor は native binding なので iframe が block されても代償はありませんが、WebSocket が engine 内蔵ではなく JavaScript で実装された host では source が読めるため non-native と判定されます（Node の undici `WebSocket` は `class _WebSocket extends EventTarget` と表示されます）。両方の条件が重なった場合の症状は出力が無音になることで、同一 origin frame を許可すれば（`frame-src 'self'`）復旧します。借用が成功すればこの判定自体を通りません。

| Close code | 意味 |
| --- | --- |
| `4403` | Origin または authentication 拒否 |
| `4409` | 256 KiB を超える message |
| `4428` | publisher idle timeout |
| `4429` | 別の publisher が active |

## セキュリティとトラブルシュート

- main server port `48911` を信頼できない LAN や Internet に公開しないでください。
- receiver が同じ PC にある場合は `127.0.0.1` を使用します。
- motion が届かない場合は、VRM が active か、送信先 port と receiver の listen port が一致するか、firewall を確認します。
- full-rate rendering 中は cumulative scheduling により設定 rate に近い平均値になります。active animation や interaction がない VRM は意図的に約 30 Hz へ throttle され、activity の再開後に rate が戻ります。
- 開発環境では `uv sync` で locked `python-osc` dependency を導入します。
- sampling error 時は render を保護するため送信を一時停止し、後続の backend status poll 後に再試行します。
- desktop chat channel を奪う旨の console error が出る場合、同一 origin の probe iframe が block され、**かつ** `window.WebSocket` の source が `[native code]` ではないため native constructor を取得できていません。後者は heuristic なので、preload が全く無い browser でも原理上ここに到達し得ます。いずれの場合も、同一 origin frame を許可する（`frame-src 'self'`）までは frame を送出しません。iframe から借用できればこの判定自体を通りません。
