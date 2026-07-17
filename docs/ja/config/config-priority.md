# 設定の優先順位

Project 全体の「environment > user file > Provider file > code」という規則はありません。

`utils/config_manager/` は code defaults、選択済み Provider profile、writable `core_config.json` から runtime settings を組み立てます。`config/api_providers.json` が欠損/不正なら code fallback を使い、Assist JSON は同じ key の code default に重ねます。

Ports は `config/network.py` が順に `NEKO_<PORT_NAME>`、互換用の裸 `<PORT_NAME>`、Electron `port_config.json`、code default を読みます。競合時は launcher が fallback port を選ぶ場合があります。

Docker の `entrypoint.sh` は `/app/config/core_config.json` がない時、または `NEKO_FORCE_ENV_UPDATE` 指定時だけ API 関連 `NEKO_*` から生成します。これは初期化で、source runtime 全体への live overlay ではありません。起動後に Web UI で有効値を確認してください。
