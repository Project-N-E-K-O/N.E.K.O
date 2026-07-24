# VMC output

**Prefix:** `/api/vmc`

N.E.K.O. can publish the active VRM avatar's humanoid bones and expressions to a VMC-compatible receiver over OSC/UDP. The sender starts disabled, defaults to `127.0.0.1:39539` at 60 Hz, and only produces frames while a VRM model is active.

The preferred first-party integration is `window.vrmVmcSender`. The raw REST and WebSocket contracts below are implementation-facing and may evolve with the VRM runtime.

## Quick start

1. Start a VMC receiver such as VSeeFace, Warudo, or a Unity/Unreal VMC integration.
2. Configure the receiver to listen on UDP port `39539`.
3. Load a VRM character in N.E.K.O.
4. Enable output from the main page:

```js
await window.vrmVmcSender.enable('127.0.0.1', 39539, 60)
```

Optional controls:

```js
await window.vrmVmcSender.requestTPose(2)
await window.vrmVmcSender.disable()
```

The endpoint and send rate are persisted, but output intentionally starts disabled after a backend restart.

## Output contract

The backend converts Three.js right-handed transforms to Unity/VMC coordinates and emits:

- `/VMC/Ext/OK`
- `/VMC/Ext/T`
- `/VMC/Ext/Root/Pos`
- `/VMC/Ext/Bone/Pos`
- `/VMC/Ext/Blend/Val`
- `/VMC/Ext/Blend/Apply`

The webpage's display position, scale, and rotation are not used as the VMC root. VMC owns an independent identity root so dragging or resizing the desktop avatar does not move the receiver's world origin.

When output is disabled, the destination changes, or the active VRM is released, N.E.K.O. sends zero values for active expressions followed by `/VMC/Ext/OK 0`. Model-release frames are acknowledged before the browser closes its dedicated socket.

## REST control plane

Mutation routes require N.E.K.O.'s same-origin CSRF headers. First-party code should call `window.vrmVmcSender` instead of constructing those headers manually.

### `GET /api/vmc/status`

Returns the effective runtime state:

```json
{
  "success": true,
  "enabled": false,
  "host": "127.0.0.1",
  "port": 39539,
  "send_rate_hz": 60,
  "config_path": ".../vmc_config.json",
  "t_pose_requested": false,
  "t_pose_duration_sec": 2.0,
  "t_pose_generation": 0
}
```

### `POST /api/vmc/enable`

All JSON fields are optional:

```json
{
  "host": "127.0.0.1",
  "port": 39539,
  "send_rate_hz": 60
}
```

`host` accepts an ASCII hostname or IPv4 address, `port` must be `1..65535`, and `send_rate_hz` must be `1..120`.

### `POST /api/vmc/disable`

Sends the terminal VMC state, closes the UDP client, and reports the disabled runtime status.

### `POST /api/vmc/t_pose`

Requests raw-rest-pose output for the active VRM:

```json
{
  "duration_sec": 2
}
```

The duration must be positive and finite and is capped at 10 seconds.

## WebSocket data plane

`/api/vmc/ws` is a dedicated first-party data channel; it is not the main chat WebSocket.

The browser must:

1. Connect from an allowed local HTTP(S) origin.
2. Send an `auth` message containing the current CSRF token within 5 seconds.
3. Wait for `{"type":"ready"}`.
4. Send sequenced `frame` or `release` envelopes.

Only one publisher may hold the process-wide lease. The server keeps one newest pending normal frame, serializes release after any in-flight frame, and expires a publisher after 10 seconds without a valid frame. Release and expression-retirement frames use `frame_ack` messages so state is not discarded before OSC transmission succeeds.

Close codes:

| Code | Meaning |
| --- | --- |
| `4403` | Origin or authentication rejected |
| `4409` | Message exceeds 256 KiB |
| `4428` | Publisher idle timeout |
| `4429` | Another VMC publisher is active |

## Security and deployment

- Do not expose the main server port `48911` to an untrusted LAN or the public Internet.
- Use `127.0.0.1` unless the receiver intentionally runs on another trusted machine.
- OSC uses UDP and has no transport-level acknowledgement; the browser acknowledgements only confirm that N.E.K.O. handed the frame to its UDP sender.
- Development installs need the locked `python-osc` dependency (`uv sync`).

## Troubleshooting

- **No motion:** confirm that VMC is enabled and a VRM, not Live2D/MMD/PNGTuber, is active.
- **No receiver data:** verify the destination host/port, receiver listen port, and local firewall.
- **About 48 Hz on a 144 Hz display:** update both `vrm-manager.js` and `vrm-vmc-sender.js`; current builds use cumulative scheduling and average approximately the configured rate.
- **Publisher busy:** close the other N.E.K.O. page or wait for its 10-second lease timeout.
- **Sampling error:** sampling is suspended to protect rendering and retried after a later backend status poll.

