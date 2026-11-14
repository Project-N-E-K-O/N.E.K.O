# ランタイムツール API

ランタイムツール API は、ローカルプラグインやコンパニオンプロセスの HTTP コールバックを、モデルが呼び出せるツールとして登録します。メインサーバーの `/api/tools` で提供され、登録内容は現在のキャラクター別セッションマネージャーへ適用され、変更リクエストが返る前にアクティブなモデルセッションへ同期されます。

> [!IMPORTANT]
> これはローカル統合契約であり、リモート管理 API ではありません。全エンドポイントはループバッククライアントだけを受け入れます。コールバック URL も HTTP/HTTPS で、`localhost`、`127.0.0.0/8`、`::1`、または IPv4-mapped ループバックを指す必要があります。それ以外の呼び出し元には HTTP `403`、非ループバックの URL には検証エラー HTTP `422` が返ります。

登録はランタイム状態であり、サーバー再起動後は残りません。グローバル登録は、リクエスト処理時に存在するキャラクターのセッションマネージャーだけに適用されます。

## ルート一覧

| メソッド | パス | 用途 |
|---|---|---|
| `POST` | `/api/tools/register` | リモートのモデルツールを登録または置換する |
| `POST` | `/api/tools/unregister` | 名前でツールを削除する |
| `POST` | `/api/tools/clear` | 指定した `source` タグのツールを削除する |
| `POST` | `/api/tools/release-host` | 指定したプラグインホストインスタンスが所有するツールを削除する |
| `GET` | `/api/tools` | 現在の登録を一覧する |

## 登録

```http
POST /api/tools/register
Content-Type: application/json

{
  "name": "get_weather",
  "description": "Get weather for a location.",
  "parameters": {
    "type": "object",
    "properties": {
      "location": {"type": "string"}
    },
    "required": ["location"]
  },
  "callback_url": "http://127.0.0.1:9333/tools/get_weather",
  "role": null,
  "source": "plugin:weather",
  "timeout_seconds": 30
}
```

| フィールド | 要件 | 既定値 |
|---|---|---|
| `name` | 1～64 文字の文字列 | 必須 |
| `description` | モデルに提示するツール説明 | `""` |
| `parameters` | ツール引数の JSON Schema オブジェクト | 空オブジェクト schema |
| `callback_url` | ループバック上の HTTP/HTTPS URL | 必須 |
| `role` | 存在するキャラクター名。`null` は現在の全ロール | `null` |
| `source` | `clear` 用のライフサイクルタグ。プラグインは `plugin:<id>` を推奨 | `"external"` |
| `timeout_seconds` | 0 より大きく 300 以下 | `30` |

登録は対象 registry の同名ツールを置換します。存在しない非 null の `role` は HTTP `404`、フィールド検証失敗は HTTP `422` です。

レスポンスは全成功、部分成功、全失敗を区別します：

```json
{
  "ok": true,
  "registered": "get_weather",
  "affected_roles": ["Lanlan"],
  "failed_roles": []
}
```

どのロールも登録を受け入れなかった場合は `ok: false` です。部分成功では `ok: true` のまま、`failed_roles` に同期失敗が入ります。

## コールバック契約

モデルがリモートツールを呼ぶと、メインサーバーは次を送信します：

```json
{
  "name": "get_weather",
  "arguments": {"location": "Shanghai"},
  "call_id": "call_123",
  "raw_arguments": "{\"location\":\"Shanghai\"}"
}
```

成功コールバックは `output` に任意の JSON 値を返せます：

```json
{"output": {"temperature_c": 28}, "is_error": false}
```

HTTP を失敗させずツールレベルのエラーを返す場合：

```json
{"error": "location not found", "is_error": true}
```

ディスパッチャーは HTTP `4xx`/`5xx` をツールエラーとして扱います。非 JSON レスポンスはテキスト出力になります。タイムアウトや接続失敗もモデルから見えるツールエラーへ変換されます。

`plugin:` で始まる source では、接続レベルの失敗が 3 回連続すると、source、コールバック origin、ホストインスタンス（指定されている場合）が一致するツールを全キャラクターのレジストリから削除します。読み取りタイムアウト、HTTP エラー応答、ツールレベルの `is_error` は削除カウントに含まれません。

コールバックのペイロードには、ユーザー会話から導かれたモデル引数が含まれる場合があります。コールバックをローカルに保ち、プラグインの他のユーザーデータ経路と同じプライバシー処理を適用してください。

## 登録解除とクリア

全ロールから 1 つの名前を削除：

```http
POST /api/tools/unregister
Content-Type: application/json

{"name": "get_weather", "role": null}
```

1 つの source が登録した全ツールを削除：

```http
POST /api/tools/clear
Content-Type: application/json

{"source": "plugin:weather", "role": null}
```

どちらも `role` で既存キャラクターを 1 つ指定できます。レスポンスには `affected_roles` と `failed_roles` があり、`unregister` の `removed` は真偽値、`clear` の `removed` は削除件数です。

## プラグインホストのクリーンアップ

プラグインホストは `plugin:` で始まる source の登録に、次のオプションフィールドを含めます。

| フィールド | 意味 |
|---|---|
| `host_instance_id` | ホスト起動ごとに生成する 1～64 文字の ID |
| `host_pid` | ローカルのホストプロセス ID（正の整数） |
| `host_process_started_at` | Unix 秒で表したプロセス開始時刻（正の有限値） |

3 つのフィールドがそろうと、メインサーバーは約 5 秒ごとにコールバック先の `GET /health` を調べ、応答の `host_instance_id` を比較します。最初の確認が成功するまでは、元のプロセスが動作している限り最大約 60 秒間起動を待ちます。それを過ぎると、すでに応答したホストと同じ基準で判定します。到達不能と判定する確認が 3 回続くか、同じアドレスが別のインスタンスに割り当てられたと確認できた場合、旧インスタンスのツールを各キャラクターのレジストリと再登録用の記録から削除し、アクティブなセッションの更新をスケジュールします。これらのフィールドがない登録には、従来のコールバック接続失敗時の削除が適用されます。

正常終了時、プラグインホストは次のリクエストでクリーンアップを開始します。

```http
POST /api/tools/release-host
Content-Type: application/json

{"host_instance_id": "<インスタンス ID>"}
```

応答は `{"ok": true, "removed": <件数>}` です。件数は各キャラクターのレジストリから削除した合計で、リクエストは繰り返し送信できます。新しいインスタンスが再登録したツールは、同じ名前とポートを使っていても新しいインスタンスに属します。`unregister` に `expected_host_instance_id` を指定すると、対象をそのインスタンスに限定できます。

## 一覧

`GET /api/tools` は現在の全ロールを一覧し、`GET /api/tools?role=Lanlan` は 1 ロールだけを選びます。存在しない場合は HTTP `404` です。

```json
{
  "ok": true,
  "tools_by_role": {
    "Lanlan": [
      {
        "name": "get_weather",
        "description": "Get weather for a location.",
        "source": "plugin:weather",
        "callback_url": "http://127.0.0.1:9333/tools/get_weather",
        "is_remote": true
      }
    ]
  }
}
```
