# Core image generation

This capability replaces the plugin proposal in [PR #2830](https://github.com/Project-N-E-K-O/N.E.K.O/pull/2830).
The provider protocol design is adapted from Alumin-Hydro's contribution. The
plugin, its panel, generation history, cache and chat hooks are not included.

## Configure

In API management, enable custom model configuration and expand **Image generation**.
Choose OpenAI, Qwen Beijing, Qwen Singapore, or an OpenAI-compatible custom endpoint.
Named providers use their corresponding API Key Book entry; their endpoints are
fixed to prevent forwarding a provider credential to an unrelated host.
Custom endpoints use an independent key and require HTTPS. Changing a custom
endpoint requires replacing or explicitly clearing the saved custom key.

The settings use the existing core_config.json persistence and /core_api masked
secret round trip. They do not establish a new credential store. The default
unconfigured slot has no fallback to conversation/vision models or free services.
Saving settings never generates a paid test image.

## Call

    from utils.image_generation import ImageRequest, generate_image

    result = await generate_image(ImageRequest(prompt="A small garden", size="1024x1024"))
    # result.provider, result.model
    # result.image.data: bytes, OR result.image.url: temporary HTTPS URL

ConfigManager.get_model_api_config("image") and its asynchronous counterpart
resolve the same configuration. Each generation uses one configuration snapshot.
OpenAI and DashScope implement the same adapter contract in separate modules.

There is deliberately no public HTTP generation route or automatic consumer.
Callers own concurrency, authorization, artifact validation/decoding, retrieval,
storage and display. Returned bytes have been base64-decoded but have not been
decoded as an image. URLs are untrusted, may expire, and are never downloaded by
this module. A future server-side downloader must enforce its own SSRF/DNS policy,
size limits and redirect policy; syntactic HTTPS validation is not sufficient.

The initial adapters support OpenAI Images generations (including GPT Image) and
DashScope's asynchronous text2image/image-synthesis API (Wan 2.1/2.5 family).
Newer DashScope multimodal-generation protocols are a separate adapter extension,
not inferred from a model name.

Requests generate one image, with a bounded prompt, dimensions, response body and
total timeout (180 seconds by default, configurable up to 600 seconds). The total
deadline also governs transport requests and polling; no shorter read timeout is
imposed. Requests advertise Accept-Encoding: identity and reject compressed
responses before reading them to prevent unbounded decompression. No automatic retries are performed because submission may already
be billed. Cancellation propagates; it does not cancel an already submitted remote
job. Failures expose stable ImageGenerationError.code values without provider
bodies, prompts or credentials. No prompt/result logging or disk writes occur.

Protocol references:
- https://developers.openai.com/api/docs/guides/image-generation
- https://www.alibabacloud.com/help/en/model-studio/text-to-image-v2-api-reference

## Validation

Tests use httpx.MockTransport and never call a paid provider. They cover request
shape, regional credential isolation, masked settings, configuration snapshots,
endpoint changes, response bounds, errors, cancellation and frontend round trips.
