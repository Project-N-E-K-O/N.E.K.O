# モデル設定

N.E.K.O. は単一の global model ではなく **role** 単位で解決します。選択 Provider profile が defaults を提供し、`core_config.json` の対応値が個別 role を上書きできます。

主な fields は `CORE_MODEL`、`CONVERSATION_MODEL`、`SUMMARY_MODEL`、`CORRECTION_MODEL`、`EMOTION_MODEL`、`VISION_MODEL`、`AGENT_MODEL`、`REALTIME_MODEL`、`TTS_MODEL` です。

Web UI で Core/Assist Provider と credential を設定し、connectivity check 後に必要な supported role の model/URL/key を設定します。保存済み endpoint は current profile candidates に含まれる間だけ再利用されます。

カスタム API で role の model ID を空欄にすると、その role が実際に指す Provider の同じ tier の default を使います。「Assist に追従」は Assist Provider、「Core に追従」は Core Provider、指定 Provider はその Provider 自身の default です。無料版と固定モデルの Provider（Kimi Code など）は保存済み model ID を無視し、常に自身のモデルを使います。「カスタム」endpoint には Provider default がないため、空欄だと Assist API の現在のモデル名がそのまま使われます。明示的に入力してください。

Web UI の各 model ID 欄には「モデルを取得」ボタンがあり、upstream endpoint が提供する model を一覧し、入力内容で絞り込めます（`POST /api/config/list_models`）。空欄のときは現在使われている model が灰色の placeholder で表示されます。入力しても使われない場所ではボタンが無効です：無料版と固定モデルの Provider、会話/要約を追従するミニゲーム slot、realtime・TTS・ミニゲーム slot の追従モード。

Model IDs、endpoints、thinking controls、token limits、voice catalog は変化します。running revision と同じ `config/api_providers.json` と Web UI を確認し、文書例を compatibility promise にしないでください。

新しい role/field は loader、config manager、router/UI、tests、8 locale を同時に更新します。
