# ChatGPT subscription provider

N.E.K.O. can use eligible ChatGPT plan usage through **Continue with ChatGPT**, without an OpenAI API key. Availability and limits depend on the selected account and the permission granted to N.E.K.O. This integration uses the public Responses API with OAuth; it does not run the Codex CLI or its coding-agent loop.

## Setup

1. Open API settings on the machine running N.E.K.O., using `localhost` or `127.0.0.1`.
2. Expand **Subscriptions & plans**, between **API Key Book** and **Per-Feature Models (Advanced)**, and click **Continue with ChatGPT** on the ChatGPT card. Complete sign-in and grant ChatGPT plan usage in the system browser. Use **Add account** for a separate registration; the account selector shows the active registration with a check mark.
3. On the same card, click **Load models from this account**, tick the features ChatGPT should take over, pick their models and switch on **Use this subscription**. Per-feature models do not need to be turned on and **Save Settings** is not needed. Switch it off and those features go back to their previous settings. See [Subscriptions & plans](/config/subscriptions).

Supported features: Chat Model, Memory Upkeep (Large Model), Recognition Model (small), Familiar Model (general), Familiar Model (small), Familiar Model (advanced), Mini-game Chat Model, Mini-game Memory Model and Mini-game Recognition Model; images go to the Chat Model. Function calls use Neko's existing tool execution. Speech recognition, Realtime Voice (local), Text-to-Speech, Image generation and the Decision Model (dedicated API) keep their own providers.

NekoPower task agents can use Familiar Model tiers handed to ChatGPT (dsh does not take these models, so such tasks run on the built-in runtime). A plugin model slot that follows a ChatGPT tier reports it as unsupported; plugins need an API of their own.

Switching the active ChatGPT account changes subsequent ChatGPT requests. Fetch models again and choose a model available to that account. Signing out clears the selected registration's tokens; the ChatGPT subscription keeps using an account that is still signed in, and turns off when the last one signs out, so its features go back to their previous settings. If remote revocation cannot be confirmed, disconnect the application in [ChatGPT Settings → Usage](https://chatgpt.com/settings/usage).

## Implementation

- OAuth authorization code + PKCE, random state and OIDC nonce; a temporary listener binds exclusively to `127.0.0.1` on an available port at `/auth/callback`. An attempt expires after five minutes.
- New registrations use `dynamic_agent_client`. Token exchange and renewal use the issued client ID. Returning sign-in keeps the registration and stable host ID.
- ID tokens are verified with OpenAI's JWKS, RS256, issuer, audience, expiry, nonce and subject. The token response must grant `chatgpt.tokens.use.direct`.
- Credentials live in the runtime's **local state directory**, `chatgpt_oauth.json`, outside cloud-save configuration. Unix files have mode `0600`; writes are atomic. File locking serializes token refresh across Neko processes. Tokens are never returned to the settings frontend or saved in `core_config.json`.
- Model discovery calls `GET https://api.openai.com/v1/models` with the selected account's access token. No account-independent model list is hardcoded.
- Inference calls `POST https://api.openai.com/v1/responses` with `store: false`, `stream: true`, and `reasoning.encrypted_content`. The adapter converts text, images, function schemas and tool results. Encrypted reasoning accompanying function calls travels with their opaque metadata in Neko history.
- Function calls are delivered for execution only after `response.completed`. For compatibility, an upstream `response.incomplete` with `incomplete_details.reason` set to `max_output_tokens` returns available text with `finish_reason="length"`, without tool calls. If no text was produced, ordinary calls raise an output-limit error; connection probes accept this valid response as evidence of connectivity. Other incomplete, failed or disconnected streams raise errors. Plan-limit errors do not rotate accounts or fall back to paid API keys.
- NekoPower task agents use the same OAuth/Responses client. Truncated tool calls are rejected; terminal authorization/plan failures stop the task rather than retrying another output mode.
- Neko's existing caller-side input budgeting and request timeouts remain in force. Under OpenAI's [preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations), the ChatGPT plan endpoint does not support the `max_output_tokens` request field. The adapter omits that field and does not enforce a caller-specified hard output-token limit. There is no model-name-based Codex context-window override. This provider is explicitly selected by protocol and pins the public OpenAI origin, independently of the existing API-key provider.

OAuth controls require a local connection and same-origin requests, and are refused behind a reverse proxy or in a remote deployment. Remote/headless sign-in and importing credentials from another application are not included. The subscription request path is distinct from ChatGPT's legacy `backend-api/codex/responses` endpoint.

## Verification

Automated tests cover PKCE/callback behavior, ID-token validation, account isolation, token rotation under concurrency, logout, origin checks, configuration routing, streamed Responses conversion, tool round trips, failure terminals and account-specific model discovery. They use generated test keys and simulated HTTP responses.

Before submitting a release, complete a real-account smoke test: sign in, fetch models, save a text-model selection, send a short conversation and an image, execute a function tool, restart Neko and send another turn, then sign out. This requires browser consent and consumes the account's plan usage; mocked tests do not establish account eligibility or live service acceptance.

## References

- [OpenAI registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [OpenAI models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [OpenAI accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [OpenCode ChatGPT provider](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/plugin/provider/chatgpt.ts)

The flow was checked against these sources on 2026-11-09. The Python implementation is written for Neko's client contracts; it does not copy OpenCode's TypeScript implementation.
