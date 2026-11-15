---
title: Coding Agent でゼロからプラグインを作る
description: 初心者向けの N.E.K.O プラグイン AI 開発ガイド。Codex、DeepSeek Harness、Claude Code、Cursor、Trae などの Coding Agent を選び、Setup Prompt をコピーするだけで AI が自動作成します。
---

# Coding Agent でゼロからプラグインを作る

Python フレームワークの細かな知識がなくても、普段使っている Coding Agent に **Setup Prompt** を貼り付けるだけで最初の N.E.K.O プラグインを作成できます。リポジトリの準備から公式 `neko-plugin` ツールによる雛形生成・実装・テスト実行まで AI が自動で進めます。

<CodingAgentOnboarding />

## Setup Prompt を貼り付けると AI が自動で行うこと

1. **環境とリポジトリの準備** — `git` と `uv` を確認（足りないものは自分でインストール）し、`Project-N-E-K-O/N.E.K.O` の取得または再利用と依存関係の同期まで進めます。
2. **公式ツールでの雛形作成** — 公式 `neko-plugin` ツールで `plugin/plugins/<plugin_id>/` に標準構成を生成します。
3. **ローカル公式ドキュメントに沿った実装** — リポジトリ内の `docs/` と `plugin/sdk/plugin/` を参照し、非同期の `@plugin_entry` や `@llm_tool` を実装します。
4. **自動チェックとパッケージ出力** — チェック・lint・テストを `0 error(s)` になるまで実行し、必要に応じて `.neko-plugin` パッケージをビルドします。

## 次のステップ

- 各ツールの公式チュートリアルと N.E.K.O の実画面スクリーンショットは [エージェント別チュートリアル](/ja/plugins/coding-agent-guide) をご覧ください。
- 生成されるファイルの役割を知りたい場合は [プラグイン開発入門ガイド](/ja/plugins/plugin-development) または手動手順の [クイックスタート](/ja/plugins/quick-start) を参照してください。
- 完成したプラグインを公開する場合は [プラグインを Market に公開する](/ja/plugins/cli) へ進みます。
