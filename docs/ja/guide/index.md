# 開発者ガイド

Project N.E.K.O. は avatar rendering、realtime/text interaction、persistent memory、Agent execution、plugin を備える open-source AI companion platform です。このサイトは current repository の contributor/integrator 向けで、pricing/provider marketing ではありません。

主な境界は `app/` services、`main_logic/` と `memory/`、`brain/`、Jinja/static + shared React chat、Vue plugin manager、N.E.K.O.-PC Electron shell、`docker/` です。

## 利用前に N.E.K.O. を確認する

| 質問 | ガイド |
| --- | --- |
| アプリは無料で、AI サービスにはどのような費用がかかるか | [料金と Provider](./cost-and-providers) |
| 完全にオフラインで動作するか | [ローカルとオフラインの境界](./local-and-offline) |
| 会話やメモリがどこへ送信される可能性があるか | [技術データフローとプライバシー制御](./data-and-privacy) |
| どの導入経路を選ぶべきか | [Steam、GitHub Releases、ソース](./install-options) |

## 開発を始める

| Goal | Page |
| --- | --- |
| Tools | [前提条件](./prerequisites) |
| Setup | [開発環境](./dev-setup) |
| First run | [クイックスタート](./quick-start) |
| Repository | [プロジェクト構造](./project-structure) |
| Services | [アーキテクチャ](/ja/architecture/) |
| Plugin | [Plugin Quick Start](/ja/plugins/quick-start) |
| Deploy | [デプロイ](/ja/deployment/) |

Python examples はすべて `uv run`。同 revision の entrypoint/loader/workflow と異なる場合は current code を優先し、docs drift を報告してください。
