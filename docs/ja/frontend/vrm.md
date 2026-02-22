# VRM モデル

## 概要

N.E.K.O. は Three.js と `@pixiv/three-vrm` を使用した 3D キャラクターレンダリングのために VRM（Virtual Reality Model）形式をサポートしています。

## モデル管理

- `/api/model/vrm/upload` から VRM ファイルをアップロード（最大 200MB）
- `/api/model/vrm/animation/upload` からアニメーションを個別にアップロード
- `/vrm_emotion_manager` から感情マッピングを設定

## ライティング設定

VRM モデルは設定可能なライティングシステムを使用します：

| ライト | デフォルト | 範囲 | 説明 |
|-------|---------|-------|-------------|
| Ambient | 0.4 | 0 - 1.0 | HemisphereLight の強度 |
| Main | 1.2 | 0 - 2.5 | メインのディレクショナルライト |
| Fill | 0.5 | 0 - 1.0 | セカンダリフィルライト |
| Rim | 0.8 | 0 - 1.5 | エッジ/リムライティング |
| Top | 0.3 | 0 - 1.0 | トップダウンライト |
| Bottom | 0.15 | 0 - 0.5 | ボトムアップライト |

設定は `PUT /api/characters/catgirl/{name}/lighting` で行います。

## UI コンポーネント

| モジュール | 用途 |
|--------|---------|
| `vrm-ui-buttons.js` | VRM 固有のコントロールボタン |
| `vrm-ui-popup.js` | VRM ポップアップダイアログ |

## 既知の問題と修正

### SpringBone 物理演算の暴走

VRM の `update(delta)` は delta を **秒** 単位で期待しています。ミリ秒やクランプされていない値を渡すと、髪が上方向に飛び散ります：

```javascript
let delta = clock.getDelta();
delta = Math.min(delta, 0.05); // タブ切り替え時の物理演算暴走を防止
vrm.update(delta);
```

### コライダーのサイズ過大（ほぼすべての VRM モデルに影響）

VRoid Studio からエクスポートされた VRM モデルには、コライダーの半径が約 2 倍大きくなる既知の UniVRM バグ（[#673](https://github.com/vrm-c/UniVRM/issues/673)）があります。これにより髪が水平に固定されたように見えます。**修正方法**：読み込み後にすべてのコライダー半径を 50% 縮小します：

```javascript
springBoneManager.colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        collider._originalRadius = collider.shape.radius;
        collider.shape.radius *= 0.5;
    }
});
```

### MToon アウトラインの太さ

モデルをスケーリングすると、MToon のアウトラインが不均衡に太くなります。スクリーンスペースモードに切り替えてください：

```javascript
material.outlineWidthMode = 'screenCoordinates';
material.outlineWidthFactor = 0.005; // 1-2 ピクセルの細いアウトライン
material.needsUpdate = true;
```

| 係数 | 効果 |
|--------|--------|
| 0.002 - 0.003 | 非常に細い（約 1px） |
| 0.005 | 細い（1-2px） |
| 0.01 | 中程度（2-3px） |
| 0.02+ | 太い |

### カメラドラッグの不一致

ドラッグに固定の `panSpeed` を使用しないでください。ピクセルからワールドへのマッピングを動的に計算します：

```javascript
const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
const pixelToWorld = worldHeight / screenHeight;
```

完全なリファレンスは [開発者ノート](/ja/contributing/developer-notes#vrm-model-gotchas) を参照してください。

## API エンドポイント

完全な REST エンドポイントリファレンスは [VRM API](/ja/api/rest/vrm) を参照してください。
