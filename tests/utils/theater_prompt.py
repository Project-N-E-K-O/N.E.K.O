"""Read theater prompt data for assertions independently of its wire layout."""

import json
import re


def prompt_sections(content):
    """Decode each paired data block without rewriting source strings."""

    return {
        label: json.loads(payload)
        for label, payload in re.findall(
            r"======以下为([^\n=]+)======\n(.*?)\n======以上为\1======",
            str(content), re.DOTALL,
        )
    }


def prompt_payload(content):
    """Expose original field paths in partitioned and unchanged review prompts."""

    text = str(content)
    sections = prompt_sections(text)
    if not sections:
        return json.loads(text[text.index("{"):])
    result = {}
    for data in sections.values():
        for key, value in data.items():
            if key in result:
                assert isinstance(result[key], dict) and isinstance(value, dict)
                assert not result[key].keys() & value.keys(), key
                result[key] = {**result[key], **value}
            else:
                result[key] = value
    return result
