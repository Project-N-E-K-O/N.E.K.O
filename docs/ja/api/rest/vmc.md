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

backend は Three.js の右手座標を Unity/VMC 座標へ変換し、`/VMC/Ext/OK`、`/VMC/Ext/T`、`/VMC/Ext/Root/Pos`、`/VMC/Ext/Bone/Pos`、`/VMC/Ext/Blend/Val`、`/VMC/Ext/Blend/Apply` を送信します。

画面表示用の位置・scale・rotation は VMC root に使いません。VMC は独立した identity root を持つため、desktop avatar の移動や resize は receiver の world origin に影響しません。

無効化、送信先変更、VRM release の際は active expression を 0 にしてから `/VMC/Ext/OK 0` を送信します。release frame の ACK 後に専用 socket を閉じます。

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

`host` は ASCII hostname または IPv4、`port` は `1..65535`、`send_rate_hz` は `1..120` です。

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
- 開発環境では `uv sync` で locked `python-osc` dependency を導入します。
- sampling error 時は render を保護するため送信を一時停止し、後続の backend status poll 後に再試行します。
