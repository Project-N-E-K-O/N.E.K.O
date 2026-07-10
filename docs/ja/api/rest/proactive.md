# 自発的会話 API

**プレフィックス:** `/api/proactive`

自発的会話の**モード**と、その基盤となる自発的会話の**設定**フィールドを読み取り・変更するためのエンドポイントです。すべての書き込みは `utils.preferences.save_global_conversation_settings` を経由するため、フィールドのホワイトリスト・型検証・アトミック書き込みのロジックは一箇所で管理されています。

::: info
これらのエンドポイントは `POST /api/proactive_chat`（[システム API](./system.md) を参照）とは異なります。後者は自発的会話メッセージを**生成**します。ここのエンドポイントは自発的会話の設定を読み取り・更新するだけです。
:::

## モード

モードはサーバー側で定義された自発的会話フィールドのプリセットです。利用可能なプリセットは `off`、`normal`、`focus`、`frequent` です。永続化されたフィールドがどのプリセットにも一致しない場合、モードは `custom` として報告されます。

### `GET /api/proactive/mode`

現在のモードと現在の自発的会話フィールドをあわせて読み取ります。

**レスポンス:**

```json
{
  "success": true,
  "mode": "normal",
  "available_modes": ["off", "normal", "focus", "frequent"],
  "settings": { "proactiveChatEnabled": true, "proactiveChatInterval": 15 }
}
```

`mode` は永続化されたフィールドから推定され、どのプリセットにも一致しない場合は `custom` になります。`settings` には自発的会話に関連するフィールドのみが含まれます。

### `POST /api/proactive/mode`

プリセットモードを適用します。

**ボディ:**

```json
{ "mode": "focus" }
```

`mode` は `off`、`normal`、`focus`、`frequent` のいずれかである必要があり、未知の値は拒否されます。

**レスポンス:**

```json
{
  "success": true,
  "mode": "focus",
  "applied": { "proactiveChatEnabled": true }
}
```

`applied` は保存後にディスクから読み戻した結果です（値と型の両方による厳密な比較）。プリセットのフィールドが永続化に失敗した場合は、フィールド名の `rejected` 配列も返されます。

::: info
モードの切り替えは `proactiveVisionEnabled`（プライバシーモードのスイッチ）を決して変更しません。プリセットはこのフィールドを意図的に含みません。キャラクターが画面を見てよいかどうかはユーザー自身の選択です。
:::

## 設定

設定エンドポイントは、プリセットを経由せずに自発的会話フィールドを直接読み取り・部分更新します。

### `GET /api/proactive/settings`

現在の自発的会話フィールド（会話設定のホワイトリスト部分集合）を読み取ります。

**レスポンス:**

```json
{
  "success": true,
  "settings": { "proactiveChatEnabled": true, "proactiveChatInterval": 15 }
}
```

### `POST /api/proactive/settings`

自発的会話フィールドを部分的に更新します。ボディは書き込み可能な自発的会話フィールド（例: `proactiveChatEnabled`、`proactiveChatInterval`、`proactiveVisionInterval`）のみを受け付けます。認識されないフィールドは静かに無視され、`save_global_conversation_settings` がさらに型/範囲の検証を行います。

**ボディ:**

```json
{ "proactiveChatEnabled": true, "proactiveChatInterval": 30 }
```

**レスポンス:**

```json
{
  "success": true,
  "applied": { "proactiveChatEnabled": true, "proactiveChatInterval": 30 }
}
```

`applied` は保存後にディスクから読み戻した結果です。値または型の検証に失敗したフィールドは `rejected` 配列に列挙されます。ボディに `proactiveVisionEnabled` が含まれる場合、そのフィールドは拒否され `rejected_user_owned` で報告されます。

::: info
`proactiveVisionEnabled` はユーザー所有のフィールドです（プライバシーモードのスイッチの裏返しで、画面内容の取得に関わります）。**プロアクティブチャット**のエンドポイントはこれを変更せず、`rejected_user_owned` で報告します。設定はメインの conversation-settings 保存経路（UI のプライバシーモードのスイッチ）で行われ、ユーザー自身の選択です。ここで送信すると、適用されずに `rejected_user_owned` に含まれて返されます。
:::
