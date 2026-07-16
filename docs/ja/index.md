---
layout: home

hero:
  name: Project N.E.K.O.
  text: 開発者ドキュメント
  tagline: 現在のコードに基づく、ローカル companion runtime、memory、Agent service、plugin、browser UI、Electron route の文書です。
  image:
    src: /logo.jpg
    alt: N.E.K.O. ロゴ
  actions:
    - theme: brand
      text: はじめる
      link: /ja/guide/
    - theme: alt
      text: 実行とデプロイ
      link: /ja/deployment/
    - theme: alt
      text: API リファレンス
      link: /ja/api/
    - theme: alt
      text: GitHub で見る
      link: https://github.com/Project-N-E-K-O/N.E.K.O

features:
  - icon: 🧭
    title: Runtime を選ぶ
    details: Source 開発では / に browser UI を提供し、Electron distribution では /chat や /subtitle などの独立した route／window を使います。
    link: /ja/guide/quick-start
    linkText: ここから開始
  - icon: 🎙️
    title: 会話と Avatar
    details: Text、audio、vision、character、Live2D、VRM、MMD、PNGTuber、desktop pet の現在の所有境界に従い、React chat UI を複製しません。
    link: /ja/frontend/
    linkText: Frontend 構成
  - icon: 🧠
    title: 永続 Memory
    details: Event 永続化、projection、recall candidate、evidence／reflection、persona、maintenance queue、任意の local-vector retrieval は別レイヤーです。
    link: /ja/architecture/memory-system
    linkText: Memory アーキテクチャ
  - icon: 🤖
    title: Agent と Plugin
    details: Task state、browser／computer automation、外部 Agent adapter、plugin routing、SDK、Hosted UI、packaging を実装済み path から追跡します。
    link: /ja/architecture/agent-system
    linkText: Agent アーキテクチャ
  - icon: ▶️
    title: Source から起動
    details: Python 3.11 を uv 経由で使い、repository script で二つの frontend を build し、uv run python launcher.py でサポート対象 suite を起動します。
    link: /ja/guide/dev-setup
    linkText: 開発環境
  - icon: 🔌
    title: Port とデプロイ
    details: Source の既定は main service 48911、memory service 48912 です。Docker の host 48911/48912 は Nginx HTTP/HTTPS なので、意味を混同しないでください。
    link: /ja/deployment/
    linkText: デプロイ方式
  - icon: 📡
    title: API 契約
    details: 現在の router で確認された REST、WebSocket、内部 service、Web page、runtime tool、cloud-save staging、capture bridge を参照できます。
    link: /ja/api/
    linkText: API を開く
  - icon: 🧰
    title: 設定とコントリビューション
    details: 現行 schema と surface ごとの優先順位を使い、uv、i18n、privacy、構造対称性、test、packaging gate に従います。
    link: /ja/contributing/
    linkText: 安全に貢献する
---
