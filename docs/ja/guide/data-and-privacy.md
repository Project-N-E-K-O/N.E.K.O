---
title: N.E.K.O. は会話やメモリをどこへ送信しますか？
description: Project N.E.K.O. のローカルメモリ、AI Provider、無料 API 転送、Telemetry、Proactive vision、Steam Cloud、Workshop の技術的なデータフローを説明します。
seoSchemaType: WebPage
---

# N.E.K.O. は会話やメモリをどこへ送信しますか？

N.E.K.O. はキャラクターメモリを既定でローカルに保存します。ただし、モデル Provider、memory processing task、無料 API 経路、cloud speech、Steam Cloud、Workshop、online feed、Browser 機能、remote Agent channel を使うと、関連する内容がデバイス外へ送られる場合があります。

最終事実確認日：**2026-07-23**。

::: warning 技術データフローに関する注意
このページは、現在の実装と公式配布時の説明を技術的に整理したものです。特定地域向けの Privacy Policy、Provider agreement、法的レビューの代わりにはなりません。
:::

## データフロー概要

```text
ユーザー入力
├── ローカルの会話・キャラクター別メモリ保存
├── 選択した conversation / Realtime Provider
├── オプションの memory processing Provider
├── 無料 API の転送経路
├── オプションの Telemetry
├── オプションの proactive screen context
├── ユーザーが実行する Steam Cloud / Workshop 操作
└── オンラインコンテンツ、Browser、音声、Agent サービス
```

## 経路ごとの動作

| 経路 | 対象データ | 送信先 | 重要な境界 |
|---|---|---|---|
| キャラクターメモリ保存 | Recent turn、Facts、Reflections、Persona、Journal、Index | 設定されたローカル memory directory | ローカル保存は、すべての処理がローカルであることを意味しない |
| Conversation Provider | 現在の Prompt、会話 context、添付入力 | 選択したモデル Provider | Provider の規約、保持、地域、アカウントプランが適用される |
| Memory maintenance | 関連する会話または memory text | 設定された summary / extraction / correction Provider | 該当 task の実行時だけ使うが、ユーザー内容を含む場合がある |
| 明示的な memory recall | 選択された recall snippet | Tool output として現在の conversation Provider | Memory database 全体が自動送信されるわけではない |
| 同梱の無料 API 経路 | 無料 request に必要な入力 | N.E.K.O. の転送サービスとサービスパートナー | 現在の Steam EULA は自分の有料 API を使う経路と区別している |
| Telemetry | 下記の利用・運用 metadata | N.E.K.O. telemetry service | 環境変数で無効化できる |
| Proactive vision | 有効な機能に必要な screen stream / screenshot | ローカル pipeline と設定した vision / model 経路 | Privacy mode は proactive viewing を止める。手動 screenshot は別経路 |
| Steam Cloud | Allowlist 内のキャラクター設定・memory file | Steam Auto Cloud | Snapshot は memory directory 全体の backup ではない |
| Workshop 公開 | ユーザーが選択した card、対応 model file、preview、任意の reference voice | Steam Workshop | 公開判断と asset license はユーザーが管理する |
| DEBUG 診断 | 一部 debug 経路の query / tool argument | ユーザーが共有しない限りローカル log | すべての log が content-free とは限らない |

## 無料 API と自分の Provider キー

現在の [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0) は、次の二つの経路を説明しています。

- 有料 Provider API を使う場合、入力はデバイスから選択した Provider に送られる。
- 無料 API サービスを使う場合、入力は N.E.K.O. のサーバーを経由してサービスパートナーへ転送される場合がある。

選択した Provider には独自の規約が適用されます。設定に Provider entry が存在しても、すべての Provider の保持・Privacy 動作が同じとは限りません。

## ローカルメモリとリモート処理

Memory system はキャラクターごとに Recent、Facts、Reflections、Persona を分け、時系列のローカル database を基礎記録として使います。次の maintenance task は LLM を利用する場合があります。

- Recent history の圧縮と review
- Fact の extraction と correction
- Reflection の synthesis と promotion
- Persona の merge と contradiction handling
- 明示的に recall した結果を現在の conversation model へ返す処理

オプションの Embedding inference は local CPU ONNX ですが、LLM を使う maintenance task まで自動的にローカルになるわけではありません。現在の runtime contract は[メモリシステム](/ja/architecture/memory-system)を参照してください。

## Telemetry

Repository README では Telemetry が既定で有効で、次のような運用カテゴリを収集すると説明しています。

- Model と call type
- Token、request、error count
- Application version、experiment 情報、Locale、timezone、distribution
- Pseudonymous device identifier。該当する Steam 環境では Steam の numeric ID を含む場合がある

README では、会話の原文、音声、画像、API key、email address、phone number は Telemetry payload に含めないとも説明しています。実装と README は今後も一致している必要があります。

無効化するには、次のいずれかを設定します。

```text
DO_NOT_TRACK=1
```

または：

```text
NEKO_DO_NOT_TRACK=1
```

## Screen と Proactive vision の制御

Privacy mode は Proactive vision を停止し、その screen stream を解放します。ただし、manual screenshot やユーザーが開始する screen sharing が技術的に不可能になるという意味ではありません。初回起動時の状態は配布方法や地域によって異なる場合があるため、共通の既定値を想定せず、現在の設定を確認してください。

Agent と Plugin には別々の enablement / readiness control があります。[Agent システム](/ja/architecture/agent-system)を参照してください。Task HUD の詳細ページは現在[英語版](/architecture/task-hud-system)のみです。

## Steam Cloud は部分的なキャラクタースナップショット

Cloud Save は Steam Auto Cloud を通じて一つのキャラクターユニットを upload / download します。Allowlist には Recent、Facts、Persona、Reflections、`time_indexed.db` などの一般的な flat file が含まれますが、現在の sharded archive、一部 metadata、recovery journal、SQLite sidecar は含まれません。

Download はローカルの同名キャラクターデータを置き換える可能性があるため、確認、active session の処理、ローカル operation backup を使います。完全な backup / migration と呼ぶ前に [Cloud Save API](/ja/api/rest/cloudsave)を確認してください。

## 現在利用できる制御

| 制御 | 実行できること | 証明できないこと |
|---|---|---|
| Provider を選ぶ | 該当 request の送信先を変更する | 他のすべての機能も同じ Provider を使うこと |
| Telemetry を無効化 | プロジェクト自身の Telemetry 経路を止める | 第三者 Provider への request がゼロであること |
| Privacy mode を有効化 | Proactive screen viewing を止める | Manual screenshot が要求されないこと |
| Agent channel を無効化 | その channel からの dispatch を止める | Chat / memory Provider がローカルであること |
| Cloud / Workshop を使わない | その任意転送経路を避ける | Model API がオフラインであること |
| 現在のキャラクターを削除 | 現在の runtime character memory path を削除する | すべての historical legacy directory や Provider 側の copy が削除されること |

> このデータフロー境界を確認したうえで試す場合は、[Steam で N.E.K.O. を確認](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=privacy_footer_ja)してください。個人情報を送る前に、選択した AI Provider の規約も確認してください。

## 関連ドキュメントと情報源

- [メモリシステム](/ja/architecture/memory-system)
- [Cloud Save API](/ja/api/rest/cloudsave)
- [Agent システム](/ja/architecture/agent-system)
- [ローカルとオフラインの境界](./local-and-offline)
- [費用と Provider の選択](./cost-and-providers)
- [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0)
- [プロジェクトリポジトリ](https://github.com/Project-N-E-K-O/N.E.K.O)
