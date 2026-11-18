---
title: AI エージェントで作る
description: コードが書けなくても N.E.K.O プラグインは作れます。Coding Agent を選んで一文を送るだけ。AI に環境づくりから任せるか、インストール済みの N.E.K.O で自分でボタンを押すか選べます。
---

# AI エージェントで作る

コードを書く必要はありません。AI コーディングツールを 1 つ選び、一文を送れば、あとは「許可」とボタンを押すだけです。

**作れるものの例**：猫娘が水分補給を教えてくれるプラグイン、会話中に呼べる天気ツール、今日の予定を表示する小さなパネルなど。一文で説明できるものなら作れます。

## 1. ルートを選ぶ

<CodingAgentOnboarding section="routes" />

## 2. Agent を選ぶ

<CodingAgentOnboarding section="agent" />

## 3. 手順どおりに進める

<CodingAgentRoute route="A">

1. Agent で**空のフォルダを開く**（場所はどこでも。このプロジェクト専用に。開き方は第 2 節で選んだ Agent カード内の公式チュートリアルを参照）。
2. この文を送る：

   <CodingAgentOnboarding section="prompt" route="A" />

3. AI が操作の実行を求めてきたら「許可」を押す。ダウンロードやインストールを自分で行うので時間がかかります。
4. 教えてくれた URL を開く。初回はモデル API を一度入力します（API キーがまだない場合は、ページ上部の案内どおり「無料版」を選べます）：

   ![初回にモデル API を入力する画面](/images/plugins/onboarding/neko-api-setup-ja.png)

   1. コア API のサービスを選ぶ。
   2. 自分の API Key を入力する。
   3. 「設定を保存」を押す。

5. プラグイン管理を開く（[プラグイン管理の開き方](#プラグイン管理の開き方)を参照）。
6. プラグイン一覧で自分のプラグインを押す：

   ![プラグイン一覧で自分のプラグインを選ぶ](/images/plugins/onboarding/neko-plugin-list-ja.png)

   1. 自分のプラグインカードを押して詳細画面へ。

7. 「起動」を押して試す：

   ![プラグイン詳細画面の「起動」「リロード」ボタン](/images/plugins/onboarding/neko-plugin-detail-ja.png)

   1. 「起動」でプラグインを実行する。
   2. 変更は自動でリロードされる。効かなかったときだけ「リロード」を押す。

8. あとは直したいことを AI に言うだけ。次回は「N.E.K.O を起動して」と言えば OK です。

</CodingAgentRoute>

<CodingAgentRoute route="B">

1. まず Git をインストール（ルート B カードに公式ダウンロードへのリンクあり。インストーラーを進めるだけ）。
2. プラグイン管理を開く（[プラグイン管理の開き方](#プラグイン管理の開き方)を参照）。
3. 左の「プラグイン制作」→「新規プラグイン」を押す：

   ![プラグイン制作の入口と「新規プラグイン」ボタン](/images/plugins/onboarding/neko-studio-entry-ja.png)

   1. 左の「プラグイン制作」。
   2. 右上の「新規プラグイン」ボタン。

4. 名前と ID を入力し、「フォルダーを選択」で保存先を選ぶ：

   ![プラグイン名・ID・フォルダーを入力](/images/plugins/onboarding/neko-studio-new-ja.png)

   1. プラグイン名（自由に）。
   2. プラグイン ID（小文字で始まる）。
   3. 「フォルダーを選択」で保存先を選ぶ。

5. 「プレビュー」で標準ファイルを確認し（AGENTS.md が見える）、「プラグインを作成」を押す：

   ![標準ファイルのプレビューと「プラグインを作成」](/images/plugins/onboarding/neko-studio-preview-ja.png)

   1. プレビューで囲まれた AGENTS.md と CLAUDE.md が AI 用の説明書。
   2. 確認したら「プラグインを作成」。

6. 作成完了。**プロジェクトフォルダの場所を覚えておく**：

   ![作成後のプロジェクト情報とソースフォルダ](/images/plugins/onboarding/neko-studio-created-ja.png)

   1. 「ソースディレクトリ」がプロジェクトフォルダの場所。
   2. 「フォルダーを開く」で中身を見られます。
   3. 「プラグイン管理で開く」でプラグイン詳細画面へ。

7. Agent で**このプロジェクトフォルダを開き**、この文を送る：

   <CodingAgentOnboarding section="prompt" route="B" />

8. 「プラグイン管理で開く」で詳細画面に進み、「起動」を押して試す。AI が直すたびに「リロード」を押す：

   ![プラグイン詳細画面の「起動」「リロード」ボタン](/images/plugins/onboarding/neko-plugin-detail-ja.png)

   1. 「起動」でプラグインを実行する。
   2. AI が直すたびに「リロード」を押して反映。

9. エラーが出たら「ログ」タブを開き、中のエラーをそのままコピーして AI に送る：

   ![プラグインのログからエラーをコピー](/images/plugins/onboarding/neko-plugin-logs-ja.png)

   1. 「ログ」タブを押す。
   2. ERROR と書かれた行をコピーして AI に送る。

</CodingAgentRoute>

## プラグイン管理の開き方

ルート A でも B でもプラグイン管理を使います。N.E.K.O のメイン画面から開きます：

![N.E.K.O メイン画面からプラグイン管理を開く](/images/plugins/onboarding/neko-open-plugin-manager-ja.png)

1. 猫娘にマウスを重ねると出る丸い「ネコクロー」ボタンを押す。
2. 出たパネルで「ネコクローマスタースイッチ」と「ユーザープラグイン」のスイッチをオンにする。
3. 「ユーザープラグイン」の行にマウスを重ねると出る「管理パネル」を押すと、プラグイン管理が別ウィンドウで開きます。

## 4. ほかの人に配りたいとき

**ルート B**：ワークベンチの自分のプロジェクト画面で「パッケージをダウンロード」を押す：

![ワークベンチの「パッケージをダウンロード」ボタン](/images/plugins/onboarding/neko-studio-package-ja.png)

1. 「パッケージをダウンロード」で `.neko-plugin` ファイルができます。

**ルート A**：AI にパッケージ化を頼むだけ。

もらった側はプラグイン管理で「インポート」を押し、ファイルを入れるだけ：

![プラグイン管理の「インポート」ボタン](/images/plugins/onboarding/neko-import-ja.png)

![インポートダイアログにプラグインを入れる](/images/plugins/onboarding/neko-import-dialog-ja.png)

1. プラグイン管理で「インポート」を押す。
2. `.neko-plugin` ファイルを枠にドラッグ、または押して選択。

Market への公開はどちらのルートでもでき、GitHub アカウントが必要です。AI にプロジェクトを GitHub へアップロードしてバージョンを公開してもらい、最初の審査申請は [Market の投稿ページ](https://market.project-neko.cn/#/upload)で数回クリックするだけです。詳しい流れは[プラグインを公開](/ja/plugins/cli)を参照してください。ルート B では「プラグイン制作」のワンクリック公開が Steam / デスクトップ版では使えないので、AI に任せてください。

> 新しいプロジェクトに AGENTS.md がない場合は N.E.K.O のバージョンが古いので、最新版に更新してからもう一度お試しください。

---

**ソース版を使っている、または手動で作りたい場合は**[プラグイン開発入門ガイド](/ja/plugins/plugin-development)または[クイックスタート](/ja/plugins/quick-start)をご覧ください。
