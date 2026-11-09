# ChatGPT subscription provider

N.E.K.O. can use eligible ChatGPT plan usage through **Continue with ChatGPT**, without an OpenAI API key. Availability and limits depend on the selected account and the permission granted to N.E.K.O. This integration uses the public Responses API with OAuth; it does not run the Codex CLI or its coding-agent loop.

## Setup

1. Open API settings on the machine running N.E.K.O., using `localhost` or `127.0.0.1`.
2. In **ChatGPT / Codex**, click **Continue with ChatGPT**. Complete sign-in and grant ChatGPT plan usage in the system browser. Use **Add account** for a separate registration; the account selector shows the active registration with a check mark.
3. Expand **Custom API configuration** and enable it. For a text or vision model slot, select **ChatGPT (subscription)**.
4. Use **Fetch models** to select a model available to the active account. Save settings. No API key is needed in this slot.

The provider supports conversation, summary, correction, emotion, vision, agent and game text-model slots. Function calls use Neko's existing tool execution. Speech recognition, realtime audio, speech synthesis and image generation keep their own providers. Keep the existing core/assist configuration for these services.

Switching the active ChatGPT account changes subsequent ChatGPT requests. Fetch models again and choose a model available to that account. Signing out clears the selected registration's tokens. If remote revocation cannot be confirmed, disconnect the application in [ChatGPT Settings → Usage](https://chatgpt.com/settings/usage).

## Implementation

- OAuth authorization code + PKCE, random state and OIDC nonce; a temporary listener binds exclusively to `127.0.0.1` on an available port at `/auth/callback`. An attempt expires after five minutes.
- New registrations use `dynamic_agent_client`. Token exchange and renewal use the issued client ID. Returning sign-in keeps the registration and stable host ID.
- ID tokens are verified with OpenAI's JWKS, RS256, issuer, audience, expiry, nonce and subject. The token response must grant `chatgpt.tokens.use.direct`.
- Credentials live in the runtime's **local state directory**, `chatgpt_oauth.json`, outside cloud-save configuration. Unix files have mode `0600`; writes are atomic. File locking serializes token refresh across Neko processes. Tokens are never returned to the settings frontend or saved in `core_config.json`.
- Model discovery calls `GET https://api.openai.com/v1/models` with the selected account's access token. No account-independent model list is hardcoded.
- Inference calls `POST https://api.openai.com/v1/responses` with `store: false`, `stream: true`, and `reasoning.encrypted_content`. The adapter converts text, images, function schemas and tool results. Encrypted reasoning accompanying function calls travels with their opaque metadata in Neko history.
- Only `response.completed` completes a turn. Failed, incomplete or disconnected streams raise errors; function calls are delivered for execution only after completion. Plan-limit errors do not rotate accounts or fall back to paid API keys.
- Neko's existing caller input/output budgets remain in force. There is no model-name-based Codex context-window override. This provider is explicitly selected by protocol and pins the public OpenAI origin, independently of the existing API-key provider.

OAuth controls require a local connection and same-origin requests. Remote/headless sign-in and importing credentials from another application are not included. The subscription request path is distinct from ChatGPT's legacy `backend-api/codex/responses` endpoint.

## Verification

Automated tests cover PKCE/callback behavior, ID-token validation, account isolation, token rotation under concurrency, logout, origin checks, configuration routing, streamed Responses conversion, tool round trips, failure terminals and account-specific model discovery. They use generated test keys and simulated HTTP responses.

Before submitting a release, complete a real-account smoke test: sign in, fetch models, save a text-model selection, send a short conversation and an image, execute a function tool, restart Neko and send another turn, then sign out. This requires browser consent and consumes the account's plan usage; mocked tests do not establish account eligibility or live service acceptance.

## References

- [OpenAI registration and sign-in](https://developers.openai.com/siwc/token-sharing-open-source/sign-in)
- [OpenAI models and inference](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
- [OpenAI accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
- [OpenCode ChatGPT provider](https://github.com/anomalyco/opencode/blob/dev/packages/core/src/plugin/provider/chatgpt.ts)

The flow was checked against these sources on 2026-11-09. The Python implementation is written for Neko's client contracts; it does not copy OpenCode's TypeScript implementation.
