# クイックスタート

Steam、standalone release、source checkout のどれを使うか決めていない場合は、先に[導入方法の比較](./install-options)を確認してください。

[開発環境](./dev-setup)を完了して実行します。

```bash
uv run python launcher.py
```

Launcher は cooperating services と selected ports を報告します。報告 URL を使い、48911 を固定しません。

Main URL の `/api_key` で Core/Assist Provider、credentials を設定し connectivity check。Provider/model list は revision-specific です。

Fresh data root は locale character defaults と Yui Origin から初期化されます。Identifier/display name は active data から来るため、旧固定名「小天」は current contract ではありません。Text chat は shared React、voice は core/TTS と microphone permission に依存します。

| Route | Purpose |
| --- | --- |
| `/character_card_manager` | character/avatar |
| `/model_manager` | avatar models |
| `/live2d_emotion_manager` | Live2D mapping |
| `/vrm_emotion_manager` | VRM mapping |
| `/voice_clone` | provider-dependent cloning |
| `/memory_browser` | memory output/status |
| `/api_key` | provider/credential |

すべての Provider/deployment が全機能を持つわけではありません。Agent/hosted plugin は agent/tool service が必要です。
