"""Account only provider-reported usage; do not estimate image/TTS tokens."""


def record_usage(job, response, model, stage):
    usage = getattr(response, "usage", None)
    metadata = getattr(response, 'response_metadata', None)
    if usage is None and isinstance(metadata, dict):
        usage = metadata.get('token_usage')
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    usage = usage if isinstance(usage, dict) else {}
    def count(value):
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    prompt = count(usage.get("prompt_tokens"))
    completion = count(usage.get("completion_tokens"))
    if prompt is None and count(usage.get('input_tokens')) is not None:
        prompt = usage['input_tokens'] + sum(count(usage.get(key)) or 0 for key in ('cache_creation_input_tokens', 'cache_read_input_tokens'))
    if completion is None:
        completion = count(usage.get('output_tokens'))
    total = count(usage.get("total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    prompt_details = usage.get("prompt_tokens_details")
    completion_details = usage.get("completion_tokens_details")
    cached = count(prompt_details.get("cached_tokens")) if isinstance(prompt_details, dict) else None
    if cached is None:
        cached = count(usage.get("prompt_cache_hit_tokens"))
    if cached is None:
        cached = count(usage.get('cache_read_input_tokens'))
    reasoning = count(completion_details.get("reasoning_tokens")) if isinstance(completion_details, dict) else None
    stats = job.setdefault("usage", {"calls": [], "input_tokens": 0, "output_tokens": 0,
                                   "total_tokens": 0, "missing_usage_calls": 0})
    stats["calls"].append({"model": model, "stage": stage, "input_tokens": prompt,
                           "output_tokens": completion, "total_tokens": total,
                           "cached_tokens": cached, "reasoning_tokens": reasoning})
    for key, value in (("input_tokens", prompt), ("output_tokens", completion), ("total_tokens", total)):
        if value is not None:
            stats[key] += value
    if any(value is None for value in (prompt, completion, total)):
        stats["missing_usage_calls"] += 1
