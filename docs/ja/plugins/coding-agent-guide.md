---
title: エージェント別チュートリアル
description: Codex・DeepSeek Harness・Claude Code・Cursor・Trae・Qoder・CodeBuddy・VS Code など、対応する Coding Agent の公式入門チュートリアルを一覧化し、N.E.K.O プラグイン管理の実画面スクリーンショットも掲載しています。
---

# エージェント別チュートリアル

下の一覧から使いたい Coding Agent を選び、公式チュートリアルの手順でインストール・ログインして空のフォルダを開きます。そのあとは [Coding Agent でゼロからプラグインを作る](/ja/plugins/coding-agents) に戻って Setup Prompt を AI に貼り付けるだけです。

<CodingAgentTutorials />

## 動いたあとに機能を追加するには？

最初のバージョンが動いたら、同じチャットセッションのまま自然な言葉で追加要望を伝えられます：

- **設定項目を追加する**：「`plugin.toml` と `config.example.toml` に通知間隔（分）の設定項目を追加して、プラグイン画面から変更できるようにしたあと、もう一度チェックを実行して。」
- **会話ツールを拡張する**：「`docs/ja/plugins/tool-calling.md` を読んで、今日飲んだコップ数を記録・確認できる `@llm_tool` を追加し、テストも更新して。」
- **Market 公開の準備をする**：「公開前のチェックをすべて通して、`docs/ja/plugins/cli.md` に沿って公開手順を案内して。」
