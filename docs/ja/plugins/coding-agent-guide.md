---
title: 画面付きガイド：Codex と DeepSeek Harness
description: OpenAI Codex と DeepSeek Harness を使い、空の作業フォルダから最初の N.E.K.O プラグインを作成・検証・起動するまでの手順を画面付きでわかりやすく解説します。
---

# 画面付きガイド：Codex と DeepSeek Harness

このページでは代表的な 2 つの Coding Agent — **OpenAI Codex**（公式デスクトップアプリ）と **DeepSeek Harness**（公式デスクトップアプリ）を使い、空のフォルダから**「水分補給リマインダー（`water_reminder`）」**プラグインを完成させるまでの流れを順番に紹介します。

> Claude Code・Cursor・Trae・Qoder・CodeBuddy・VS Code をお使いの場合は、[Coding Agent でゼロからプラグインを作る](/ja/plugins/coding-agents) から各ツールの導入ステップと Setup Prompt をコピーしてください。貼り付けた後の進め方は同じです。

<CodingAgentWalkthrough />

## 動いたあとに機能を追加するには？

最初のバージョンが動いたら、同じチャットセッションのまま自然な言葉で追加要望を伝えられます：

- **設定項目を追加する**：「`plugin.toml` と `config.example.toml` に通知間隔（分）の設定項目を追加して、プラグイン画面から変更できるようにしたあと、もう一度チェックを実行して。」
- **会話ツールを拡張する**：「`docs/ja/plugins/tool-calling.md` を読んで、今日飲んだコップ数を記録・確認できる `@llm_tool` を追加し、テストも更新して。」
- **Market 公開の準備をする**：「公開前のチェックをすべて通して、`docs/ja/plugins/cli.md` に沿って公開手順を案内して。」
