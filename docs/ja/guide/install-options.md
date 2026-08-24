---
title: N.E.K.O. は Steam、GitHub Releases、ソースのどこから導入すべきですか？
description: Project N.E.K.O. の Steam、GitHub Release、ソースからの導入方法を、対応プラットフォーム、更新、制限、対象ユーザーごとに比較します。
seoSchemaType: WebPage
---

# N.E.K.O. は Steam、GitHub Releases、ソースのどこから導入すべきですか？

一般ユーザーには Steam、Linux などの standalone asset が必要な場合は GitHub Releases、開発・統合・高度なカスタマイズを行う場合はソースからのセットアップが適しています。

最終事実確認日：**2026-07-23**。確認時点の最新 stable GitHub Release は **v0.8.3** でした。固定されたバージョンとして扱わず、常に現在の Release ページを確認してください。

## 導入経路の比較

| 経路 | 事実確認時に表示されたプラットフォーム | 適しているユーザー | 重要な制限 |
|---|---|---|---|
| [Steam](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_table_ja) | Windows、macOS | 簡単な導入と Workshop、Achievement、Steam Cloud を利用したい一般ユーザー | 早期アクセス。対応プラットフォームは現在の Steam ページに従う |
| [GitHub Releases](https://github.com/Project-N-E-K-O/N.E.K.O/releases) | v0.8.3 では Windows、Linux、macOS arm64 | Standalone release asset または Linux package が必要なユーザー | Asset 名とプラットフォーム範囲は Release ごとに変わる |
| Source checkout | 現在の依存関係と検証済み環境による | Contributor、Integrator、custom deployment | Python 3.11、`uv`、互換性のある Node tooling、手動設定が必要 |
| Nightly prerelease | Workflow run による | 最新変更のテスト | Stable release の保証ではない |

Windows、macOS、Linux の build asset が存在しても、一つの download URL がすべてのプラットフォームを提供するわけではありません。特に Steam URL を Linux の download URL として扱わないでください。

## Steam を選ぶ場合

- 一般的な desktop installation を使いたい。
- Steam Workshop、Achievement、Steam Cloud を使いたい。
- 現在の Steam ストアページに自分のプラットフォームが掲載されている。
- 製品が早期アクセスであることを理解している。

Steam の基本アプリは現在無料です。AI Provider の料金と規約は別に適用されるため、[N.E.K.O. は無料ですか？](./cost-and-providers)も確認してください。

## GitHub Releases を選ぶ場合

- Steam 以外で公開された standalone asset が必要。
- 現在の Release が提供する Linux AppImage または tar archive が必要。
- Release note と正確な file name を確認したい。
- Steam 配布とは独立して特定バージョンをテストしたい。

2026-07-23 の確認時点で、[v0.8.3](https://github.com/Project-N-E-K-O/N.E.K.O/releases/tag/v0.8.3) には次の asset がありました。

```text
N.E.K.O_0.8.3.1_win.zip
N.E.K.O_0.8.3_win.zip
N.E.K.O_0.8.3_linux.AppImage
N.E.K.O_0.8.3_linux.tar.gz
N.E.K.O_0.8.3_mac_arm64.zip
```

これは当該 Release の履歴情報であり、今後も同じ asset が提供される保証ではありません。

## ソースからセットアップする場合

- Code または docs に貢献する。
- Model、memory、Agent、Plugin、deployment の動作を調査・変更する。
- Custom local / server deployment を構築する。
- 必要な開発ツールを管理できる。

現在の source development には次が必要です。

- Python **3.11**
- Python environment と command の実行に [`uv`](https://docs.astral.sh/uv/)
- Repository lockfile と互換性のある Node。Plugin Manager は現在 `^20.19.0 || >=22.12.0` を要求
- 有効にする機能が必要とする platform-specific dependency

[前提条件](./prerequisites)、[開発環境の構築](./dev-setup)、[クイックスタート](./quick-start)から始めてください。

## Stable release と Nightly output

Cross-platform workflow は Windows、macOS、Linux の output を生成できます。Scheduled output は **Nightly prerelease** です。Nightly asset はテストに使えますが、最新 stable release または長期サポート package として案内しないでください。

## 導入前の確認

1. 選択した経路でプラットフォームと CPU architecture を確認する。
2. 現在の Release note または Steam Early Access notice を読む。
3. 無料 remote Profile、自分の Provider key、local component のどれを使うか決める。
4. [ローカルとオフラインの境界](./local-and-offline)を確認する。
5. [技術データフローと Privacy control](./data-and-privacy)を確認する。

> 標準のデスクトップ導入を始める場合は、[Steam から N.E.K.O. をインストール](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_footer_ja)してください。Steam がプラットフォームに合わない場合は、上記の別経路を利用してください。

## 関連ドキュメント

- [前提条件](./prerequisites)
- [開発環境の構築](./dev-setup)
- [クイックスタート](./quick-start)
- [デプロイ概要](/ja/deployment/)
- [GitHub Releases](https://github.com/Project-N-E-K-O/N.E.K.O/releases)
- [Steam ストアページ](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=install_source_ja)
