# モデル設定

N.E.K.O. は単一の global model ではなく **role** 単位で解決します。選択 Provider profile が defaults を提供し、`core_config.json` の対応値が個別 role を上書きできます。

主な fields は `CORE_MODEL`、`CONVERSATION_MODEL`、`SUMMARY_MODEL`、`CORRECTION_MODEL`、`EMOTION_MODEL`、`VISION_MODEL`、`AGENT_MODEL`、`REALTIME_MODEL`、`TTS_MODEL` です。

Web UI で Core/Assist Provider と credential を設定し、connectivity check 後に必要な supported role の model/URL/key を設定します。保存済み endpoint は current profile candidates に含まれる間だけ再利用されます。

Model IDs、endpoints、thinking controls、token limits、voice catalog は変化します。running revision と同じ `config/api_providers.json` と Web UI を確認し、文書例を compatibility promise にしないでください。

新しい role/field は loader、config manager、router/UI、tests、8 locale を同時に更新します。
