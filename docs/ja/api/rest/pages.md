# Pages ルーター

Web UI の HTML ページを提供します。すべてのページは Jinja2 テンプレートでレンダリングされます。

## ルート

| パス | テンプレート | 説明 |
|------|----------|-------------|
| `/` | `index.html` | メインチャットインターフェース |
| `/model_manager` | `model_manager.html` | Live2D/VRM モデル管理 |
| `/live2d_parameter_editor` | `live2d_parameter_editor.html` | Live2D パラメータの微調整 |
| `/live2d_emotion_manager` | `live2d_emotion_manager.html` | Live2D 感情-アニメーションマッピング |
| `/vrm_emotion_manager` | `vrm_emotion_manager.html` | VRM 感情-アニメーションマッピング |
| `/chara_manager` | `chara_manager.html` | キャラクター設定エディタ |
| `/voice_clone` | `voice_clone.html` | 音声クローニングインターフェース |
| `/api_key` | `api_key_settings.html` | API キー設定 |
| `/steam_workshop_manager` | `steam_workshop_manager.html` | Steam Workshop 管理 |
| `/memory_browser` | `memory_browser.html` | メモリの閲覧と編集 |
| `/{lanlan_name}` | `index.html` | キャラクター固有のチャット（キャッチオール） |

::: info
`/{lanlan_name}` キャッチオールルートは同じメインインターフェースを提供しますが、特定のキャラクターを事前に選択します。
:::
