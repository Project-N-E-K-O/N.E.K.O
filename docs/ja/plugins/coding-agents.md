---
title: Coding Agent でゼロからプラグインを作る
description: 初心者向けの N.E.K.O プラグイン AI 開発ガイド。Codex、DeepSeek Harness (dsh)、Claude Code、Cursor、Trae などの Coding Agent を選び、Setup Prompt をコピーして neko-plugin CLI で自動作成できます。
---

# Coding Agent でゼロからプラグインを作る

Python フレームワークの細かな知識がなくても、普段使っている Coding Agent に **Setup Prompt** を貼り付けるだけで最初の N.E.K.O プラグインを作成できます。リポジトリの準備から公式 `neko-plugin` CLI による雛形生成・実装・テスト実行まで AI が自動で進めます。

<CodingAgentOnboarding />

## Setup Prompt を貼り付けると AI が自動で行うこと

1. **環境とリポジトリの準備** — `git` と `uv` を確認し、`Project-N-E-K-O/N.E.K.O` の取得または再利用、`uv sync`、`uv run neko-plugin --help` の確認まで進めます。
2. **公式 CLI での雛形作成** — `uv run neko-plugin init <plugin_id> --type plugin --name "<表示名>"` を実行し、`plugin/plugins/<plugin_id>/` に標準構成を生成します。
3. **ローカル公式ドキュメントに沿った実装** — リポジトリ内の `docs/` と `plugin/sdk/plugin/` を参照し、非同期の `@plugin_entry` や `@llm_tool` を実装します。
4. **自動チェックとパッケージ出力** — `uv run neko-plugin check`・`lint`・`pytest` を `0 error(s)` になるまで実行し、必要に応じて `.neko-plugin` パッケージをビルドします。

## 次のステップ

- 画面ごとの操作手順を確認したい場合は [画面付きガイド：Codex と DeepSeek Harness](/ja/plugins/coding-agent-guide) をご覧ください。
- 生成されるファイルの役割を知りたい場合は [プラグイン開発入門ガイド](/ja/plugins/plugin-development) または手動手順の [クイックスタート](/ja/plugins/quick-start) を参照してください。
- 完成したプラグインを公開する場合は [プラグインを Market に公開する](/ja/plugins/cli) へ進みます。
