---
name: tts-error-reporting
description: Convention for reporting errors from multiprocessing TTS workers to the main process frontend. Use this skill when modifying, adding, or debugging TTS workers in tts_client.py to ensure connection errors, quotas, and API limits correctly display Toast notifications to the user rather than failing silently.
---

# TTS Error Reporting Protocol

To ensure users are properly notified when a TTS service encounters an error (such as quota exhaustion, API issues, or connection failures), TTS workers running in separate processes must propagate errors back to the main process (`core.py`).

## Core Rule: Use `response_queue.put(("__error__", error_msg))`

When a TTS worker (e.g. `step_realtime_tts_worker`, `qwen_realtime_tts_worker`, etc.) catches an error, it **MUST NOT** only log the error using `logger.error()`. It **MUST ALSO** send the error message back to the main process through its `response_queue` using the explicit tuple format `("__error__", error_msg)`.

This ensures that `core.py`'s `tts_response_handler` can intercept the error and translate it into a frontend WebSocket message (`type: 'status'`), triggering a user-friendly Toast notification (e.g., "💥 免费TTS限额已耗尽").

### Example Implementation

```python
# Bad Pattern (Fails silently for user)
except Exception as e:
    logger.error(f"TTS Worker Error: {e}")
    # Worker dies or hangs, user stuck on "Preparing..."

# Good Pattern (Structured JSON error for i18n frontend toasts)
import json
except Exception as e:
    logger.error(f"TTS Worker Error: {e}")
    # Map to specific error codes known to i18n
    error_payload = json.dumps({"code": "API_QUOTA_TIME", "details": str(e)})
    response_queue.put(("__error__", error_payload))

# Acceptable Fallback (Fallback 1008 error if code unknown)
except Exception as e:
    logger.error(f"TTS Worker Error: {e}")
    error_payload = json.dumps({"code": "API_1008_FALLBACK", "msg": str(e)})
    response_queue.put(("__error__", error_payload))
```

### WebSocket Stream Callbacks Example

```python
import json
def on_error(self, message: str): 
    logger.error(f"TTS Error: {message}")
    error_payload = json.dumps({"code": "API_1008_FALLBACK", "msg": message})
    self.response_queue.put(("__error__", error_payload))
```

## Checklist for Adding a New TTS Worker

Whenever you add a new TTS API worker in `tts_client.py`:
1. Ensure the worker signature accepts a `response_queue`.
2. Locate all network initialization blocks, `try/except` loops, and `on_error` WebSocket callbacks.
3. In every local exception block where the worker might fail or drop the connection, add `response_queue.put(("__error__", error_message_string))`.
4. Ensure string encoding handles JSON cleanly if propagating raw API errors.
