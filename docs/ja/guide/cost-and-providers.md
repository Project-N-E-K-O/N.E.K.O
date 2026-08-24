---
title: N.E.K.O. は無料ですか、AI API にはどのような費用がかかりますか？
description: Project N.E.K.O. で現在無料の範囲、API キーや有料 Provider が必要になる場面、無料のリモート経路とローカルまたは自己負担構成の違いを説明します。
seoSchemaType: WebPage
---

# N.E.K.O. は無料ですか、AI API にはどのような費用がかかりますか？

N.E.K.O. の基本アプリは現在 Steam で無料です。プロジェクトのコードは Apache License 2.0 で公開されています。ただし、AI Provider、音声サービス、その他の第三者サービスには、個別の料金、利用枠、利用規約が適用される場合があります。

最終事実確認日：**2026-07-23**。料金、利用枠、モデル、Provider の提供状況は変わるため、購入を判断する前に各サービスの最新情報を確認してください。

## 「無料」に含まれる範囲

| 項目 | 現在の位置づけ | 別途費用が発生する可能性 |
|---|---|---|
| Steam の基本アプリ | 無料、早期アクセス | 今後の配布条件は変更される可能性があるため、Steam ページを確認 |
| プロジェクトのソースコード | Apache License 2.0 | 第三者の依存関係、素材、商標、サービスにはそれぞれの条件が適用 |
| 内蔵の無料 Provider 経路 | 同梱の無料 Profile ではユーザーの API キーは不要 | リモートサービスであり、提供状況や利用枠は調整される可能性がある |
| 自分の Provider API キー | Provider のアカウントと費用を自分で管理 | Token、Realtime、音声、画像などの Provider 利用料 |
| Voice cloning と TTS | 複数のクラウドまたはローカルサービス経路がある | クラウド Provider ではキー、アカウント、有料枠が必要な場合がある |
| Steam Cloud と Workshop | 対応する Steam 機能から利用 | ネットワーク接続と該当する Steam アカウントが必要 |

「オープンソース」または「アプリが無料」であることは、すべてのモデル、音声、キャラクター素材、ホスト型サービスが Project N.E.K.O. から無償提供・許諾されることを意味しません。

## AI サービスを使う主な三つの方法

### 1. 同梱の無料経路を使う

現在の設定には、ユーザーが API キーを入力しなくても使える無料の Core / Assist Profile があります。これらは Project N.E.K.O. のリモートサービスへ接続するもので、**ローカルモデルでもオフラインモードでもありません**。

現在の [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0) では、無料 API の入力が N.E.K.O. のサーバーを経由してサービスパートナーへ転送される場合があること、また無料利用枠が調整される可能性があることを説明しています。そのため、このページでは恒久的な一日あたりの利用枠を掲載しません。

### 2. 自分の API キーを使う

対応 Provider を、自分のアカウントと認証情報で設定できます。この場合：

- 料金、Rate limit、地域での提供状況、データ保持条件は Provider が決めます。
- N.E.K.O. の機能ごとに別の Provider role が使われる場合があります。
- Text chat 対応だけでは、Realtime voice、vision、ASR、TTS、Agent への対応を意味しません。
- 選択するモデルによって品質と費用の両方が変わります。

現在の Steam EULA によれば、有料 Provider API を使う場合、入力はデバイスから選択した Provider に送信されます。Provider の最新規約も確認してください。

### 3. ローカルまたはセルフホストのコンポーネントを構成する

オプションのローカル Embedding、一部の音声経路、vLLM-Omni 経路など、ローカルまたはセルフホストのサービスを使えるコンポーネントがあります。ホスト型 API への依存を減らせますが、単一の切り替えではなく、ハードウェア、モデル資産、追加設定が必要になる場合があります。

ローカル構成を「費用なし」または「通信なし」と判断する前に、[N.E.K.O. は完全にオフラインで動作しますか？](./local-and-offline)を確認してください。

## Provider 数を固定表示しない理由

Provider 定義はデータ駆動で、次の領域ごとに独立して変わります。

- 主要会話と Realtime の Profile
- Text、vision、summary、correction、Agent などに使う Assist Profile
- ASR、TTS、voice cloning など機能別の Registry
- 地域、アカウントプラン、リリース

これらを「N 社以上」と一つの数字にまとめると、すぐに古くなります。設定の仕組みは現在の [API Providers リファレンス](/ja/config/api-providers)を確認し、実行中のバージョンに表示される Provider を基準にしてください。

## 選び方

| 優先したいこと | 推奨する始め方 |
|---|---|
| 最小限の設定で試す | 現在利用できる無料 Profile から始め、リモート接続と調整可能な利用枠を前提にする |
| モデルと請求を自分で管理する | 対応 Provider の自分のキーを設定する |
| 外部処理を減らす | ローカルまたはセルフホスト可能なコンポーネントを一つずつ確認する |
| 月額費用を見積もる | Provider の Usage dashboard と最新料金表を使う。料金は N.E.K.O. が決めるものではない |
| 想定外のデータ送信を避ける | [N.E.K.O. は会話やメモリをどこへ送信しますか？](./data-and-privacy)を読む |

> Provider を選ぶ前に無料の本体を試したい場合は、[Steam で N.E.K.O. を確認](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=cost_footer_ja)してから、このページで API 経路を比較してください。

## 関連する技術ドキュメント

- [API Providers](/ja/config/api-providers)
- [モデル設定](/ja/config/model-config)
- [TTS Client](/ja/modules/tts-client)
- [ローカルとオフラインの境界](./local-and-offline)
- [Steam ストアページ](https://store.steampowered.com/app/4099310/__NEKO/?utm_source=project-neko.online&utm_medium=referral&utm_campaign=buyer_guides&utm_content=cost_source_ja)
- [Steam EULA](https://store.steampowered.com/eula/4099310_eula_0)
