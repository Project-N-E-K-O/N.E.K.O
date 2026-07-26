---
title: アクセス解析と Cookie に関するお知らせ
description: Project N.E.K.O. ドキュメントサイトが、任意の同意後に Google Analytics を使用し、選択を保存して撤回できるようにする仕組みを説明します。
seoSchemaType: WebPage
---

# アクセス解析と Cookie に関するお知らせ

最終更新日：2026 年 7 月 23 日。

このお知らせは、`project-neko.online` の Project N.E.K.O. ドキュメントサイトに適用されます。

## 選択する前

Google Analytics は読み込まれず、Google Analytics へのリクエストも送信されません。**アクセス解析を許可**または**拒否**を選ぶまで、同意パネルは選択を保存しません。

## アクセス解析を許可した場合

サイトは Measurement ID `G-N4QZK4PHE3` を使用して Google Analytics 4 を読み込み、役立つドキュメントページやドキュメントへの流入経路を把握するためにページビューイベントを送信します。訪問者が N.E.K.O. の Steam ページへのリンクを選択した場合は、リンク先 URL、リンクテキスト、CTA の配置、現在のページ URL、ページタイトルを含む `steam_cta_click` イベントも送信します。

Google Analytics は、ページの URL とタイトル、参照元、ブラウザと端末の情報、おおよその位置情報などを処理する場合があります。サイト設定では、広告ストレージ、広告ユーザーデータ、広告パーソナライズ、Google Signals、広告パーソナライズシグナルを無効にしています。

GA4 の「データ保持」設定の対象となるユーザーレベルおよびイベントレベルのデータは、最長 14 か月保持されます。プロパティ管理者はこの期間を 2 か月に短縮できます。この設定は、集計済みの標準レポートには影響しません。詳しくは [Google Analytics のデータ保持](https://support.google.com/analytics/answer/7667196?hl=ja) をご覧ください。

同意後、Google Analytics は `_ga` や `_ga_<measurement-id>` などのファーストパーティ Cookie を設定する場合があります。Cookie とデータ収集に関する Google の説明：

- [Google Analytics における Cookie の使用](https://support.google.com/analytics/answer/11397207?hl=ja)
- [Google Analytics のデータ収集](https://support.google.com/analytics/answer/11593727?hl=ja)
- [Google プライバシーポリシー](https://policies.google.com/privacy?hl=ja)

## 選択の保存方法

ブラウザは、ローカルストレージの `neko.docs.analytics-consent.v1` に選択を保存します。保存されるのは、許可または拒否、形式バージョン、保存時刻だけです。選択は 180 日後に期限切れとなり、サイトが再度確認します。

拒否した場合、Google Tag は読み込まれません。以前の同意を撤回すると、サイトはアクセス解析の同意状態を拒否に変更し、スクリプトからアクセス可能な `_ga` Cookie の削除を試み、Google Tag を読み込まずにページを再読み込みします。

## 選択の変更または撤回

各ドキュメントページの下部にある**アクセス解析の設定**ボタンから、いつでも許可または拒否を選べます。拒否してもドキュメントの利用には影響しません。

このお知らせに関する質問は、[Project N.E.K.O. GitHub リポジトリ](https://github.com/Project-N-E-K-O/N.E.K.O/issues)からプロジェクトへお問い合わせください。
