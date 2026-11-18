# Image message history

An offline image turn contains `image_url` blocks and a text block. Temporary
prompt text may be replaced after the reply, while later user turns still need
the original pixels. Replacing the whole message content with a string loses
those pixels immediately, even though the first request carried the image.

`utils/image_history.py` owns two pure transformations:

- `replace_history_text` replaces only the matching temporary text block. Image
  and other attachment blocks, their order, and unrelated text remain intact.
- `without_image_pixels` removes image blocks and adds a bounded attachment
  receipt. It leaves other blocks intact and is idempotent.

The streaming finalizer retains its existing identity check, so cancellation or
a concurrent history change cannot replace another turn. Message replacement
preserves metadata and rebinds resident knowledge to the replacement. Image
eviction uses the same metadata and resident-anchor contract.

Retention remains bounded to the latest two image-bearing user turns by
default. A zero retention limit removes all image pixels. Eviction records
attachment presence, not successful delivery, visual understanding, or a
generated image summary. Previous assistant descriptions are not independently
verified visual facts. No additional model call, disk storage or logging of
image bytes is introduced.

The receipt lives with the original turn and follows normal history trimming
and context clearing. Restart persistence is the responsibility of the history
store; this module does not reconstruct deleted pixels.

Regression checks use generated image fixtures and the real offline streaming
and request-projection code, with only provider transport replaced:

```sh
uv run --no-sync python -m pytest -q \
  tests/unit/test_image_history.py \
  tests/unit/test_offline_turn_cancellation_e2e.py \
  tests/unit/test_turn_context_request_view.py \
  tests/unit/test_proactive_vision_screenshot_staging.py \
  tests/unit/test_offline_provider_frame_publish.py \
  tests/unit/test_tool_calling.py
```
