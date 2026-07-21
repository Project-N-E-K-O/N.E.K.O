# API Provider

Provider は data-driven です。同梱定義は `config/api_providers.json`、Python fallback は `config/api_profiles.py` にあります。Provider 数や model ID の文書 snapshot に依存しないでください。

- **Core**: `core_url(s)`、`core_model`、`core_api_key`
- **Assist**: URL、token-plan URL、conversation/summary/correction/emotion/vision/agent models、credential placeholder、`provider_type`

すべての Provider が全 role を実装するわけではありません。`utils/api_config_loader.py` は JSON を cache/convert し、Assist JSON を同 key の code defaults に重ね、欠損/不正時は fallback を使います。`assist_api_key_fields` が credential field を対応付けます。

同じ JSON の keybook、API-key registry、native TTS/voice、livestream、moderation は個別 consumer/schema です。変更時は key を安定させ、secret を入れず、表示文言は 8 locale を同期し、tests/docs build を実行してください。詳細は [API Provider fields](/api_providers_fields)。
