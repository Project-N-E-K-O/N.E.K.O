# 設定ファイル

N.E.K.O. は書き込み可能な user data と repository 同梱 defaults を分離します。

既定の data root:

- Windows: `%LOCALAPPDATA%\N.E.K.O`
- macOS: `~/Library/Application Support/N.E.K.O`
- Linux: `$XDG_DATA_HOME/N.E.K.O`、未設定時 `~/.local/share/N.E.K.O`

ユーザーが別の保存場所を選んでいる場合があります。診断時は現在の storage location を確認してください。

選択した root の `config/` には `core_config.json`、`characters.json`、`user_preferences.json`、`voice_storage.json`、`workshop_config.json` などがあります。機能が追加ファイルを作る場合もあります。通常は current schema を扱う Web UI で編集します。

Repository の `config/` は user data ではありません。`api_providers.json`、8 locale の `characters/*.json`、Python defaults を含みます。`utils/config_manager/` が初回に対応 defaults を移行し、その後は data root に書き込みます。

Character identifier/display name は active data から来ます。予約済み avatar data は `_reserved.avatar` にあり、legacy top-level `live2d` は互換用に残る場合があります。翻訳表示名を stable ID にしないでください。

手動編集前に app を停止し、data root を backup してください。個人設定と secrets は commit しません。
