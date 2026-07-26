
<div align="center">

![Project N.E.K.O.](https://raw.githubusercontent.com/Project-N-E-K-O/N.E.K.O/main/assets/neko_logo.jpg)

[简体中文](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/README.MD) · [English](README_en.md) · [Русский](README_ru.md)

# Project N.E.K.O.

Browser／Electron surface、永続 memory、具現化 avatar、Agent 機能、plugin SDK を備えた local-first AI companion runtime です。

[![Python 3.11](https://img.shields.io/badge/Python-3.11-blue.svg)](https://www.python.org/)
[![Apache License 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE)
[![開発者ドキュメント](https://img.shields.io/badge/Developer_Docs-project--neko.online-40C5F1)](https://project-neko.online/ja/)
[![Steam](https://img.shields.io/badge/Steam-N.E.K.O.-000000?logo=steam)](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_readme&utm_content=badge_ja)

</div>

このファイルは簡潔な repository overview です。現在の architecture、setup、configuration、API、deployment、plugin、contribution の正本は[開発者ドキュメント](https://project-neko.online/ja/)です。Provider／model 一覧、価格、product version の約束、roadmap 日付は複製しません。

## 現在のリポジトリ境界

- **会話 runtime:** text、audio、vision pipeline と character 設定。
- **Avatar surface:** Live2D、VRM、MMD、PNGTuber、desktop pet 関連 path。
- **Memory:** 会話 event の永続化、projection、recall candidate、evidence／reflection、persona、maintenance queue。
- **Agent:** browser／computer automation、task state transport、外部 Agent adapter、runtime tool service。
- **Plugin:** SDK 契約、built-in plugin、hosted surface、lifecycle hook、routing、packaging gate。
- **Frontend:** 静的／Jinja page、単一の React chat 実装、Vue plugin manager。Browser `/` と Electron の `/chat`、`/subtitle` は別 runtime context です。

実装が存在しても、すべての provider、platform、distribution、任意 integration が同等にサポートされるとは限りません。

## Source から実行

必要要件：

- Python は 3.11 固定。
- [uv](https://docs.astral.sh/uv/)。
- Frontend を再ビルドする場合は Node.js `^20.19.0 || >=22.12.0`。

```bash
git clone --filter=blob:none https://github.com/Project-N-E-K-O/N.E.K.O.git
cd N.E.K.O
uv sync
```

初回 checkout または frontend 変更後に二つの frontend project を build します。

```bash
# Linux / macOS
./build_frontend.sh
```

```powershell
# Windows PowerShell
.\build_frontend.bat
```

サポート対象の service suite を起動します。

```bash
uv run python launcher.py
```

`http://127.0.0.1:48911` を開きます。Service を個別起動する前に[開発環境](ja/guide/dev-setup.md)と[クイックスタート](ja/guide/quick-start.md)を確認してください。

## Port とデプロイ

| Context | Host port | 意味 |
| --- | ---: | --- |
| Source runtime | `48911` | Main Web/API service |
| Source runtime | `48912` | Memory service |
| Docker Compose | `48911` | Nginx HTTP entry |
| Docker Compose | `48912` | Nginx HTTPS entry |

これは異なる二つの port model です。他の内部／既定 service port と override は[環境変数](ja/config/environment-vars.md)にあります。

追跡中の Compose file は image を pull し、`build:` section を持ちません。

```bash
docker compose up -d
```

Local image build、storage、TLS、image 選択は [Docker ガイド](ja/deployment/docker.md)に従ってください。Source／desktop artifact は[デプロイ概要](ja/deployment/index.md)から確認します。

## ドキュメント案内

- [はじめに](ja/guide/index.md)
- [アーキテクチャ](ja/architecture/index.md)
- [API リファレンス](ja/api/index.md)
- [設定](ja/config/index.md)
- [フロントエンド](ja/frontend/index.md)
- [Plugin 開発](ja/plugins/index.md)
- [デプロイ](ja/deployment/index.md)
- [コントリビューション](ja/contributing/index.md)

API/provider 設定は schema-driven です。コピーされた provider／model 一覧ではなく、現在の settings UI と `config/api_providers.json` を確認してください。

## Privacy と telemetry

Runtime が認識する opt-out は `DO_NOT_TRACK=1` または `NEKO_DO_NOT_TRACK=1` です。実行する revision の最新 disclosure は repository root の README と `utils/token_tracker/` を確認してください。この短い翻訳では変化しやすい payload 詳細を複製しません。

## コントリビューションとライセンス

編集前に `.agent/rules/neko-guide.md` と対応する `.agent/skills/*/SKILL.md` を読みます。Project の Python コマンドはすべて `uv run` で実行し、ユーザー向け i18n 変更では 8 個の runtime locale を更新します。

Project N.E.K.O. は [Apache License 2.0](https://github.com/Project-N-E-K-O/N.E.K.O/blob/main/LICENSE) で公開されています。再現可能な bug と範囲を絞った提案には [GitHub Issues](https://github.com/Project-N-E-K-O/N.E.K.O/issues) を使用してください。
