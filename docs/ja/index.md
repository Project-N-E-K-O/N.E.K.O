---
layout: home
title: Project N.E.K.O. 開発者ドキュメント
titleTemplate: false
description: オープンソース AI コンパニオン Project N.E.K.O. の開発者ドキュメント。導入、デプロイ、モデル設定、永続メモリ、アバター、エージェント、API、プラグイン開発を解説します。

hero:
  name: Project N.E.K.O.
  text: 開発者ドキュメント
  tagline: 任意の画面コンテキスト連携、永続メモリ、エージェントチャンネル、具現化アバターを備えたプロアクティブなマルチモーダル AI コンパニオン。
  image:
    src: /logo.jpg
    alt: N.E.K.O. ロゴ
  actions:
    - theme: brand
      text: はじめる
      link: /ja/guide/
    - theme: brand
      text: Steamで入手
      link: 'https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_home&utm_content=hero_ja'
    - theme: alt
      text: APIリファレンス
      link: /ja/api/
    - theme: alt
      text: GitHubで見る
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🎮
    title: Steamワークショップ & コミュニティ
    details: Steamで配信中。ワークショップでは、キャラクターカード、対応アバター素材、プレビュー、任意の参照音声を共有できます。
    link: 'https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=docs_home&utm_content=feature_ja'
    linkText: Steamで見る
  - icon: 🎙️
    title: オムニモーダル対話
    details: 音声・テキスト・ビジョンを統合した対話ループ。RNNoiseニューラルノイズ除去、AGC、VADによる超低レイテンシのリアルタイム音声。
    link: /ja/architecture/
    linkText: 詳しく見る
  - icon: 💬
    title: プロアクティブチャット
    details: 対応機能を有効にすると、画面コンテキスト、対応フィード、音楽、ミームをプロアクティブな対話に利用できます。プライバシーモードでは能動的な画面参照を停止できます。
    link: /ja/guide/
    linkText: 詳しく見る
  - icon: 🧠
    title: 五次元メモリシステム
    details: キャラクターごとに、作業コンテキスト、直近記憶、事実、振り返り、ペルソナを管理します。Embedding がなくても BM25 を利用でき、任意のローカル Embedding で意味検索を強化できます。
    link: /ja/architecture/memory-system
    linkText: 仕組みを見る
  - icon: 🤖
    title: エージェントフレームワーク
    details: 有効化され利用可能な Computer Use、Browser Use、ユーザープラグイン、OpenClaw、OpenFang チャンネルで任意のバックグラウンドタスクを実行します。個別タスクと全アクティブタスクを停止できます。
    link: /ja/architecture/agent-system
    linkText: エージェントを探る
  - icon: 🔌
    title: プラグインエコシステム
    details: プラグイン SDK とマーケットプレイスで拡張でき、デコレーター API、非同期ライフサイクルフック、プラグイン間通信、有効化時のエージェントエントリを提供します。
    link: /ja/plugins/
    linkText: プラグインを作る
  - icon: 🎭
    title: Live2D・VRM・MMD・PNGTuber
    details: 4 種類の対応アバター形式をメイン UI とデスクトップペットのホスト形態で利用でき、形式ごとの表情、リップシンク、アニメーション、操作に対応します。音色登録は複数のクラウド／ローカルバックエンドを利用でき、要件はサービスごとに異なります。
    link: /ja/frontend/
    linkText: フロントエンドガイド
  - icon: 🌐
    title: 設定可能な AI プロバイダー & 国際化
    details: コア対話、補助、音声など複数の Provider Profile を設定できます。利用可能な Provider はバージョンや地域で変わり、製品 UI とプロンプトは 8 言語に対応します。
    link: /ja/config/api-providers
    linkText: プロバイダー一覧
---
