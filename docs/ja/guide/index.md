# はじめに

**Project N.E.K.O.**（**N**etworked **E**mpathetic **K**nowledging **O**rganism）は、リアルタイムの音声/テキストインタラクション、Live2D/VRMモデルレンダリング、永続的メモリ、エージェントベースのタスク実行を統合したオープンソースAIコンパニオンプラットフォームです。

## N.E.K.O. とは？

N.E.K.O. はAIコンパニオンのためのUGC（ユーザー生成コンテンツ）プラットフォームです。ユーザーは独自の個性、声、ビジュアルモデルを持つAIキャラクターを作成、カスタマイズ、共有できます。システムは以下をサポートしています：

- **リアルタイム音声会話** — Realtime APIプロバイダー（Qwen、OpenAI、Gemini、Step、GLM）を使用したWebSocket経由の通信
- **Live2DおよびVRMモデルレンダリング** — 感情マッピングされたアニメーション付き
- **永続的メモリ** — セマンティック検索と時間インデックス付き履歴によるセッション間の記憶保持
- **バックグラウンドエージェント実行** — MCP、Computer Use、Browser Useアダプター経由
- **音声クローン** — カスタムTTSボイス対応
- **Steam Workshop連携** — コンテンツ共有機能
- **プラグインシステム** — 開発者向け拡張機能

## 対象読者

このドキュメントは、以下を目的とする**開発者**向けに書かれています：

- N.E.K.O. のコアコードベースへのコントリビュート
- N.E.K.O. の機能を拡張するプラグインの開発
- N.E.K.O. のREST/WebSocket APIとの連携
- カスタム環境への N.E.K.O. のデプロイ
- デバッグや拡張のためのシステムアーキテクチャの理解

## クイックリンク

| 目的 | 参照先 |
|------|--------|
| 開発環境のセットアップ | [開発環境セットアップ](./dev-setup) |
| アーキテクチャの理解 | [アーキテクチャ概要](/ja/architecture/) |
| プラグインの開発 | [プラグインクイックスタート](/plugins/quick-start) |
| APIとの連携 | [APIリファレンス](/api/) |
| Dockerでのデプロイ | [Dockerデプロイメント](/deployment/docker) |
| システムの設定 | [設定リファレンス](/config/) |

## 技術スタック

| レイヤー | 技術 |
|----------|------|
| バックエンドフレームワーク | FastAPI + Uvicorn |
| リアルタイム通信 | WebSocket（ネイティブ + Alibaba DashScope） |
| サービス間メッセージング | ZeroMQ（PUB/SUB + PUSH/PULL） |
| LLM連携 | LangChain + OpenAI互換API |
| TTS | DashScope CosyVoice、GPT-SoVITS |
| フロントエンド | Vanilla JS、Pixi.js（Live2D）、Three.js（VRM） |
| メモリストレージ | SQLite + テキストEmbedding |
| パッケージ管理 | uv（Python 3.11） |
| コンテナ化 | Docker（マルチアーキテクチャ） |
