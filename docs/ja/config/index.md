# 設定の概要

N.E.K.O. には別々の設定面があり、共通の万能な優先順位はありません。

| 設定面 | 推奨操作 | 情報源 |
| --- | --- | --- |
| User settings、characters、API Key | Web UI | 選択された data root の JSON |
| Provider 定義と model-role defaults | repository data | `config/api_providers.json` と code fallback |
| Ports と一部 runtime switches | environment / desktop settings | `config/network.py`、`config/memory_settings.py`、`port_config.json` |

通常は N.E.K.O. を起動して Web UI から設定します。個人の API Key のために同梱ファイルを変更しないでください。

- [設定ファイル](./config-files)
- [API Provider](./api-providers)
- [モデル設定](./model-config)
- [環境変数](./environment-vars)
- [設定優先順位](./config-priority)

API Key を Git、screenshot、Issue、log に含めないでください。
