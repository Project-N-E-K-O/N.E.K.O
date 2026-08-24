# Windows デスクトップビルド

End user は [Steam store](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_deployment&utm_content=install_ja) から install し、Steam 経由で起動して Provider を desktop/Web UI で設定します。

Python-backend artifact 単体には Electron windows、tray、Steam integration、routing、updater はありません。

`.github/workflows/build-desktop.yml` は Windows x64 Electron artifact と別の Python backend artifact を build します。Scheduled run は必要 stage 成功時に repository の `nightly` prerelease を更新します。

Nightly は unsigned testing build で、次の run に置き換えられます。Stable/auto-update channel ではありません。Project GitHub Releases だけから取得し、built commit を確認して data root を backup してください。

Desktop workflow は configured N.E.K.O.-PC revision、Nuitka standalone backend、packaging checks 対象の config/templates/static/plugins/embedding/tiktoken/browser resources を組み合わせます。Port conflict 時は fallback するため、automation で 48911 を固定しません。
