# 前提条件

## 必須

| 要件 | バージョン | 備考 |
|------|-----------|------|
| Python | 3.11.x | 必ず3.11である必要があります（3.12以降は不可） |
| uv | 最新版 | Pythonパッケージマネージャー（[インストール](https://docs.astral.sh/uv/getting-started/installation/)） |
| Git | 2.x以上 | リポジトリのクローン用 |

## オプション

| 要件 | 必要な場面 |
|------|-----------|
| Node.js 20以上 | フロントエンド開発またはドキュメントのビルド |
| Docker | コンテナ化されたデプロイメント |
| Steamクライアント | Steam Workshop機能 |

## APIキー

N.E.K.O. には少なくとも1つのAI APIプロバイダーが必要です。**無料**ティア（キー不要）で始めることも、自分のキーを設定することもできます：

| プロバイダー | 提供内容 | サインアップ |
|-------------|---------|-------------|
| 無料（組み込み） | 基本的な音声チャット | サインアップ不要 |
| Qwen（推奨） | 全機能、無料ティアが充実 | [Alibaba Cloud Bailian](https://bailian.console.aliyun.com/) |
| OpenAI | 全機能 | [OpenAI Platform](https://platform.openai.com/) |
| GLM | 無料ティアあり | [Zhipu Open Platform](https://open.bigmodel.cn/) |
| Step | 音声に最適化 | [StepFun](https://platform.stepfun.com/) |
| Gemini | Googleのモデル | [Google AI Studio](https://aistudio.google.com/) |

::: tip ヒント
Alibaba Cloudの新規ユーザーは、本人確認後に大量の無料クレジットを取得できます。ほとんどの開発者にはこちらのオプションをお勧めします。
:::
