# 開発者ガイド

Project N.E.K.O. は avatar rendering、realtime/text interaction、persistent memory、Agent execution、plugin を備える open-source AI companion platform です。このサイトは current repository の contributor/integrator 向けで、pricing/provider marketing ではありません。

主な境界は `app/` services、`main_logic/` と `memory/`、`brain/`、Jinja/static + shared React chat、Vue plugin manager、N.E.K.O.-PC Electron shell、`docker/` です。

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
