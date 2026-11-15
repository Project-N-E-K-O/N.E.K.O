"""Literal generated-body overlap; authored fixed originals and buttons are excluded."""

from .numeric_v2_performance import performance_content_blocks, scene_narration_text


def repetition_overlap(current, previous):
    def text(body):
        parts = body.get("segments") or [body]
        generated = {"segments": [{**{key: value for key, value in part.items()
                                      if key != "fixed_narrations"},
                                   **({"scene_narration": scene_narration_text(part)}
                                      if "scene_narration" in part else {})} for part in parts]}
        return "".join(char.casefold() for block in performance_content_blocks(generated)
                       for char in block["text"] if char.isalnum())

    new, old = text(current), text(previous)
    def grams(value):
        return {value[index:index + 5] for index in range(max(0, len(value) - 4))}
    a, b = grams(new), grams(old)
    return {"ngram": 5, "current_chars": len(new), "previous_chars": len(old),
            "overlap": round(len(a & b) / len(a), 4) if a else 0.0}
