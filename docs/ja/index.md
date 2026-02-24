---
layout: home

hero:
  name: Project N.E.K.O.
  text: 開発者ドキュメント
  tagline: 生きたAIコンパニオンメタバースを構築しよう — オープンソース、拡張可能、マルチモーダル。
  image:
    src: /logo.jpg
    alt: N.E.K.O. ロゴ
  actions:
    - theme: brand
      text: はじめる
      link: /ja/guide/
    - theme: alt
      text: APIリファレンス
      link: /api/
    - theme: alt
      text: GitHubで見る
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🏗️
    title: マイクロサービスアーキテクチャ
    details: 3サーバー構成（Main、Memory、Agent）で、WebSocketリアルタイム通信、ZeroMQイベントバス、ホットスワップ可能なLLMセッションを備えています。
    link: /ja/architecture/
    linkText: 詳しく見る
  - icon: 🔌
    title: プラグインSDK
    details: Pythonプラグインで N.E.K.O. を拡張できます。デコレーターベースのAPI、非同期サポート、ライフサイクルフック、永続的な状態管理に対応しています。
    link: /plugins/
    linkText: プラグインを作る
  - icon: 🌐
    title: REST & WebSocket API
    details: 包括的なAPIサーフェス — キャラクター、モデル、メモリ、エージェントをカバーする10のRESTルーターと、リアルタイム音声/テキストチャット用のストリーミングWebSocketプロトコル。
    link: /api/
    linkText: APIリファレンス
  - icon: 🧠
    title: メモリシステム
    details: Embeddingによるセマンティック検索、時間インデックス付き履歴、スライディングウィンドウによる圧縮された最近のメモリ、永続的なユーザー設定。
    link: /ja/architecture/memory-system
    linkText: 仕組みを見る
  - icon: 🤖
    title: エージェントフレームワーク
    details: MCP、Computer Use、Browser Useアダプターによるバックグラウンドタスク実行。自動タスク計画、重複排除、並列機能評価に対応。
    link: /ja/architecture/agent-system
    linkText: エージェントを探る
  - icon: 🎨
    title: Live2D & VRM
    details: Live2DとVRMモデルレンダリング、感情マッピング、音声クローン、5言語の国際化対応を備えたリッチなフロントエンド。
    link: /frontend/
    linkText: フロントエンドガイド
---
