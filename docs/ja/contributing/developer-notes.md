# 開発者ノート

すべての N.E.K.O. コントリビューターが知っておくべき重要なルールと注意点です。これらはプロジェクト経験から得られた貴重な知見です。

## コアルール

::: danger 必ず従うこと
これらのルールはコードベース全体で適用されます。
:::

### 1. 必ず `uv` を使用して実行する

すべての Python コマンドは `uv` を通す必要があります：

```bash
# ✅ 正しい
uv run python main_server.py
uv run pytest tests/

# ❌ 間違い
python main_server.py
pytest tests/
```

### 2. ユーザー向けテキストには i18n が必須

プロジェクトは 5 言語（`en`、`zh-CN`、`zh-TW`、`ja`、`ko`）をサポートしています。すべてのユーザーに表示される文字列は i18n システムを通す必要があります。

- **HTML**: `data-i18n` 属性を使用
- **JS**: 中国語フォールバック付きの `window.t('key')` を使用
- ロケールファイルは `static/locales/` にあります

完全なガイドは [国際化](/ja/frontend/i18n) を参照してください。

### 3. プライバシーに関わるログ: `print()` のみ

**生のユーザー会話データ** を含む可能性のあるログは `print()` を使用し、`logger` は使用しないでください。これにより機密データが永続的なログファイルに残らないようにします。

```python
# ✅ ユーザー会話データ
print(f"User said: {user_message}")

# ✅ システムイベントには logger を使用
logger.info("Session started for character: %s", lanlan_name)

# ❌ ユーザー会話を logger で記録しないこと
logger.info(f"User said: {user_message}")  # ダメ！
```

### 4. 翻訳時にシステムプロンプトのウォーターマークを保持する

システムプロンプトを（いかなる理由でも）翻訳する際は、必ずマーカー `======以上為` を保持してください。これはプロンプト境界検出に使用される内部ウォーターマークです。

### 5. Steam 実績は不可逆

Steam 実績は一度アンロックすると、コードで **取り消すことができません**。デプロイ前に必ずコンソールコマンドで実績ロジックを十分にテストしてください：

```javascript
// ブラウザコンソールでテスト
await window.unlockAchievement('ACH_NAME');
window.getAchievementStats();
```

## フロントエンドの注意点

### i18n が HTML アイコンを破壊する

i18next が `textContent` 経由で要素テキストを更新すると、要素内の `<img>` や `<span>` タグが破壊されます。翻訳文字列に HTML が含まれている場合、i18n システムはこれを検出して代わりに `innerHTML` を使用します。翻訳可能な要素にアイコンを追加する場合は、ロケール JSON に HTML を含めてください：

```json
{
  "button.save": "<img src='icon.svg'> Save"
}
```

### `overflow: hidden` が `<select>` ドロップダウンを壊す

カプセル UI システムは大きな border-radius を使用しており、開発者がコンテナに `overflow: hidden` を追加しがちです。これによりネイティブの `<select>` ドロップダウンがクリップされます。修正方法：

```css
/* <select> を含むコンテナ */
.field-row-with-select {
  overflow: visible !important;
}
```

### ボタンインタラクションの公式

すべてのボタンは一貫した操作感のために以下のインタラクションパターンに従う必要があります：

```css
.button:hover {
  transform: translateY(-1px);
  /* 強調されたシャドウ */
}
.button:active {
  transform: translateY(1px) scale(0.98);
}
```

### Vanilla JS の競合状態（DOM の遅延読み込み）

N.E.K.O. はリアクティブフレームワークなしの vanilla JavaScript を使用しているため、コード実行時に DOM 要素が存在しない場合があります -- 特に最初のクリック時に遅延作成されるポップアップや HUD コンポーネントで顕著です。

::: warning DOM バインディングに固定の `setTimeout` を使用しないこと
ハードコードされた `setTimeout(..., 100)` はまだ作成されていない要素を見逃します。代わりに自己終了型の再帰ポーリングを使用してください：
:::

```javascript
const bindEvents = () => {
    const getEl = (ids) => {
        for (let id of ids) {
            const el = document.getElementById(id);
            if (el) return el;
        }
        return null;
    };

    const targetEl = getEl(['live2d-agent-keyboard', 'vrm-agent-keyboard']);

    if (!targetEl) {
        setTimeout(bindEvents, 500); // DOM が存在するまでリトライ
        return;
    }

    // 見つかった -- バインドしてポーリングを停止
    targetEl.addEventListener('change', myLogic);
    myLogic(); // 最初のチェックをトリガー
};

setTimeout(bindEvents, 100); // ポーリング開始
```

**楽観的 UI の競合**: トグルボタンがクリックされると、バックエンドリクエストの送信中に UI は楽観的に「オン」に切り替わります。別のコンポーネント（例えばポーリングループ）がこの間に DOM を読み取ると、古い状態を見る可能性があります。要素の値を信頼する前に、要素がローディング/無効状態にあるかどうかを確認して防御してください。

### UI デザインシステム: カプセル UI + Neko Blue

プロジェクトには厳格なビジュアルシステムがあります：

| トークン | 値 | 用途 |
|-------|-------|-------|
| `--color-n-main` | `#40C5F1` | ブランドブルー: タイトル、プライマリボタン、アクティブ状態 |
| `--color-n-deep` | `#22b3ff` | ストローク/ディープブルー: テキストアウトライン、フォーカスグロー |
| `--color-n-light` | `#e3f4ff` | ライトバックグラウンドブルー |
| `--color-n-border` | `#b3e5fc` | ボーダーブルー: カプセルボーダー、仕切り |
| `--radius-capsule` | `50px` | すべてのインタラクティブ要素 |
| `--radius-card` | `20px` | カードとコンテナ |

フォント：
- **ラテン文字**: `'Comic Neue'`, `'Segoe UI'`, `Arial`
- **CJK**: `'Source Han Sans CN'`, `'Noto Sans SC'`
- **等幅フォント**（API キー、ID）: `'Courier New', monospace`

完全なデザインシステムは `.agent/skills/ui-system-refactor/references/design-system.md` を参照してください。

## バックエンドの注意点

### Gemini API レスポンス形式

Gemini は JSON レスポンスをマークダウンコードブロックでラップすることがあります：

````
```json
{"emotion": "happy"}
```
````

パース前に必ずマークダウンラッピングを除去してください：

```python
if result_text.startswith("```"):
    lines = result_text.split("\n")
    if lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    result_text = "\n".join(lines).strip()
```

### Gemini `extra_body` は二重ネストが必要

OpenAI 互換 API 経由で Gemini のシンキングモードを制御する場合、`extra_body` は二重ネストにする必要があります：

```python
# ✅ 正しい: 二重ネスト
extra_body = {
    "extra_body": {
        "google": {
            "thinking_config": {
                "thinking_budget": 0  # 2.5 のシンキングを無効化
            }
        }
    }
}

# ❌ 間違い: 単一ネスト（"Unknown name 'google'" エラーの原因）
extra_body = {
    "google": {
        "thinking_config": {"thinking_budget": 0}
    }
}
```

### シンキングモードはプロバイダーごとに異なる

各 LLM プロバイダーは拡張推論を無効化するためのフォーマットが異なります：

| プロバイダー | フォーマット |
|----------|--------|
| Qwen, Step, DeepSeek | `{"enable_thinking": false}` |
| GLM | `{"thinking": {"type": "disabled"}}` |
| Gemini 2.x | `{"thinking_config": {"thinking_budget": 0}}` |
| Gemini 3.x | `{"thinking_config": {"thinking_level": "low"}}` |

`config/__init__.py` モジュールがこのマッピングを自動的に処理します -- `MODELS_EXTRA_BODY_MAP` を確認してください。

## VRM モデルの注意点

### SpringBone 物理演算の暴走

VRM の物理演算は `vrm.update(delta)` を使用し、`delta` はミリ秒ではなく **秒** 単位である必要があります。読み込み時に髪/衣服が上方に飛ぶ場合：

```javascript
let delta = clock.getDelta();
delta = Math.min(delta, 0.05); // タブ切り替え時の暴走を防止するためクランプ
vrm.update(delta);
```

### コライダーのサイズ過大（VRM モデルの約 100% に影響）

VRoid Studio/UniVRM からエクスポートされた VRM モデルには、コライダーの半径が約 2 倍大きくなる既知のバグ（[UniVRM #673](https://github.com/vrm-c/UniVRM/issues/673)）があります。これにより髪が水平に固定されたように見えます。

**修正方法**: 読み込み後にすべてのコライダー半径を 50% 縮小します：

```javascript
const COLLIDER_REDUCTION = 0.5;
springBoneManager.colliders.forEach(collider => {
    if (collider.shape?.radius > 0) {
        collider._originalRadius = collider.shape.radius;
        collider.shape.radius *= COLLIDER_REDUCTION;
    }
});
```

### MToon アウトラインの太さ

VRM モデルをスケーリングすると、MToon のアウトラインが不均衡に太くなります。スクリーンスペースアウトラインに切り替えてください：

```javascript
material.outlineWidthMode = 'screenCoordinates';
material.outlineWidthFactor = 0.005; // 細く一貫したアウトライン
material.needsUpdate = true;
```

### 3D カメラ: ピクセルからワールドへのマッピング

VRM モデルのドラッグ/ズームを実装する場合、**固定のパン速度を使用しないでください**。カメラ距離に基づいてピクセルからワールドへのマッピングを動的に計算します：

```javascript
const worldHeight = 2 * Math.tan(fov / 2) * cameraDistance;
const pixelToWorld = worldHeight / screenHeight;
// マウスのデルタ * pixelToWorld = ワールド空間の移動量
```

## テスト

### テスト構成

```
tests/
├── unit/          # OmniOffline/Realtime クライアント、プロバイダー接続
├── frontend/      # 各 Web UI ページの Playwright テスト
├── e2e/           # 完全なユーザージャーニー（8 ステージ、--run-e2e フラグが必要）
└── utils/         # LLM ベースのレスポンス品質評価器
```

### テストの実行

```bash
# すべてのテスト（e2e を除く）
uv run pytest tests/ -s

# ユニットテストのみ
uv run pytest tests/unit -s

# フロントエンドテスト（Playwright ブラウザが必要）
uv run playwright install
uv run pytest tests/frontend -s

# E2E テスト（明示的なフラグが必要）
uv run pytest tests/e2e --run-e2e -s
```

### テスト用 API キー

`tests/api_keys.json.template` を `tests/api_keys.json` にコピーし、キーを入力してください。このファイルは gitignore されています。

## Issue テンプレート

バグ報告や機能リクエストを提出する際は、GitHub の Issue テンプレートを使用してください：

- **バグ報告**: 再現手順、期待される動作と実際の動作、環境情報を含めてください
- **機能リクエスト**: 機能、ユースケース、関連するコンテキストを記述してください
