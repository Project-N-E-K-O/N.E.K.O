---
title: N.E.K.O. は完全にオフラインで動作しますか、通信が必要な機能は何ですか？
description: Project N.E.K.O. でローカル保存・実行されるコンポーネント、外部サービスへ接続する機能、OmniOfflineClient が無通信モードではない理由を説明します。
seoSchemaType: WebPage
---

# N.E.K.O. は完全にオフラインで動作しますか、通信が必要な機能は何ですか？

N.E.K.O. は、インストール後すぐに全機能をオフラインで使える製品ではありません。UI と既定のメモリ保存先はローカルで、一部コンポーネントはセルフホストできますが、通常の無料経路や、多くのモデル、音声、Steam、Cloud、Workshop、オンラインコンテンツ、Browser、Agent 機能にはネットワーク接続が必要です。

最終事実確認日：**2026-07-23**。

## 混同してはいけない四つの用語

| 用語 | このページでの意味 |
|---|---|
| ローカル保存 | ユーザーのデバイス上のファイルまたはデータベースへ書き込むこと |
| ローカル推論 | ユーザー自身のハードウェアでモデルを実行すること |
| 非 Realtime client | Realtime session ではなく、通常の request/response API で text を処理すること |
| 完全オフライン | 外部 endpoint へ接続せずに、想定した workflow を継続できること |

内部名の `OmniOfflineClient` は、**非 Realtime の text Chat Completions 経路**を指します。設定されたモデル endpoint を呼び出すため、完全オフラインモードの証拠にはなりません。

## コンポーネント別ネットワーク表

| コンポーネント | 既定または一般的な場所 | 通信する可能性 | ローカルの選択肢または制限 |
|---|---|---:|---|
| メイン UI と Avatar runtime | ローカルデバイス | 場合による | Rendering はローカル。接続型の機能は通信する |
| キャラクターメモリのファイル | ローカルデバイス | 処理時にあり | 保存は既定でローカルだが、要約・抽出は設定した Provider を使う場合がある |
| BM25 memory recall | ローカル process | Ranking に Provider は不要 | Vector が使えなくても継続できる |
| オプションの memory Embedding | ローカル CPU ONNX | 通常は不要 | Embedding 段階だけがローカルであり、すべての memory LLM task がローカルになるわけではない |
| Core / Assist モデル | 一般的な構成ではリモート | あり | コンポーネントによって互換性のあるセルフホスト endpoint を設定できる |
| 同梱の無料 Profile | Project N.E.K.O. のリモートサービス | あり | ユーザー API キーは不要だが、オフラインではない |
| ASR、TTS、voice registration | Provider による | 多くの場合あり | 一部の local TTS や vLLM-Omni 経路がある。要件は経路ごとに異なる |
| Steam、Workshop、Steam Cloud | Steam サービス | あり | オフラインでは利用できない |
| Browser、Feed、Trend、オンラインコンテンツ | 外部ソース | あり | ネットワークなしでは最新の外部コンテンツを取得できない |
| リモート Agent channel | Channel による | 多くの場合あり | Computer Use の操作はローカルでも、判断やモデル呼び出しはリモートの場合がある |

## 既定でローカルに残るもの

- メイン Web UI はローカルの Main server 上で動作します。
- キャラクターメモリは設定されたキャラクター別 memory directory に保存されます。
- Recent、Facts、Reflections、Persona、Journal、Recovery state は、ユーザーが同期または export 経路を実行しない限り、既定ではローカルのファイルまたはデータベースです。
- Vector inference がなくても BM25 retrieval は利用できます。
- オプションの Embedding inference は local CPU ONNX Execution Provider を使います。
- ユーザーが import した Avatar asset は、import 後にローカルで rendering できます。

保存先がローカルであることだけでは、モデル処理の場所は決まりません。

## デバイス外へ送信される可能性が高いもの

- 選択した chat / Realtime Provider へ送る会話入力
- Summary、extraction、reflection、promotion、review、correction task が使う関連会話またはメモリ text
- Tool output として現在の conversation Provider へ返す memory recall snippet
- 現在の N.E.K.O. サービス経路を通る無料 API request
- 選択した cloud ASR、TTS、voice service へ送る音声 sample または text
- ユーザーが明示的に実行する Steam Cloud / Workshop content
- Online feed、Browser request、リモート Agent work

データフローの詳細は、[N.E.K.O. は会話やメモリをどこへ送信しますか？](./data-and-privacy)を参照してください。

## よりローカルな構成に必要なこと

よりローカルな構成は、コンポーネントごとに組み立てます。

1. 互換性のある local または self-hosted conversation endpoint を選ぶ。
2. Text chat だけでなく、必要なすべての role が対応しているか確認する。
3. 利用できる場合は local speech component を構成する。
4. オプションの Embedding をローカルに保つ。
5. 境界外となる Steam Cloud、Workshop、online feed、Browser work、remote Agent channel を無効化または使用しない。
6. Outbound 通信を遮断して実測し、機能低下する項目を記録する。

Project N.E.K.O. には現在、これらをすべて自動で設定する検証済みの「ワンクリック・オフラインモード」はありません。

## オフライン時に想定される動作

結果は構成によって異なります。ローカル rendering と保存済みファイルは利用できる場合がありますが、リモート会話、無料 Profile、オンライン音声、Workshop、Cloud、Feed、remote Agent channel は失敗または利用不可になる可能性があります。一つのコンポーネント名に「local」や「offline」が含まれていても、システム全体の保証にはなりません。

> この通信境界で問題なければ、[Steam で N.E.K.O. を確認](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=offline_footer_ja)してください。より厳密なローカル要件には、ソース構成で各コンポーネントを個別に検証してください。

## 関連する技術ドキュメント

- [メモリシステム](/ja/architecture/memory-system)
- [API Providers](/ja/config/api-providers)
- [TTS Client](/ja/modules/tts-client)
- [TTS パイプライン](/ja/architecture/tts-pipeline)
- [デプロイ概要](/ja/deployment/)
- [費用と Provider の選択](./cost-and-providers)
