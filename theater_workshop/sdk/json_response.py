"""JSON parsing with string-preserving bounded syntax repair shared with InkAI."""
from __future__ import annotations
import json
from typing import Any, Dict

class JSONResponseParser:
    def parse_json_response(self, response: str) -> Dict[str, Any]:
        """Parse a JSON response into structured data."""
        try:
            # 尝试直接解析
            return json.loads(response)
        except json.JSONDecodeError:
            pass

        # 尝试处理markdown格式的JSON (优先处理)
        try:
            if '```json' in response:
                # 提取markdown代码块中的JSON
                start_marker = '```json'
                end_marker = '```'
                start = response.find(start_marker)
                if start != -1:
                    start += len(start_marker)
                    end = response.find(end_marker, start)
                    if end != -1:
                        json_str = response[start:end].strip()
                        # 修复常见的JSON格式问题
                        json_str = self._fix_json_format(json_str)
                        result = json.loads(json_str)
                        print(f"成功解析markdown JSON")
                        return result
        except json.JSONDecodeError as e:
            print(f"markdown JSON解析失败: {e}")
            pass

        # 尝试提取JSON部分（从第一个{到最后一个}）
        try:
            start = response.find('{')
            end = response.rfind('}') + 1
            if start != -1 and end != 0:
                json_str = response[start:end]
                # 修复常见的JSON格式问题
                json_str = self._fix_json_format(json_str)
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass

        # 如果无法解析，尝试提取可能的文本内容
        # 如果响应看起来像markdown，尝试提取文本部分
        if '```' in response:
            # 尝试提取markdown中的文本内容
            lines = response.split('\n')
            text_content = []
            in_code_block = False
            for line in lines:
                if line.strip().startswith('```'):
                    in_code_block = not in_code_block
                    continue
                if not in_code_block and line.strip():
                    text_content.append(line)

            if text_content:
                return {"content": '\n'.join(text_content)}

        # 最后尝试，返回原始文本但添加错误标记
        print(f"警告：无法解析JSON响应，返回原始文本")
        return {"content": response, "parse_error": True}


    def _fix_json_format(self, json_str: str) -> str:
        """Repair bounded syntax errors without guessing or rewriting string values."""
        json_str = json_str.strip()
        pieces = []
        index = 0
        while index < len(json_str):
            char = json_str[index]
            if char == '"':
                try:
                    value, end = json.decoder.scanstring(json_str, index + 1, False)
                except json.JSONDecodeError:
                    return json_str
                token = json_str[index:end]
                # Escape actual controls while retaining the decoded text. Valid
                # escapes and punctuation inside strings remain untouched.
                pieces.append(json.dumps(value, ensure_ascii=False)
                              if any(ord(char) < 32 for char in token) else token)
                index = end
                continue
            if char == ',' and json_str[index + 1:].lstrip().startswith(('}', ']')):
                index += 1
                continue
            pieces.append(char)
            index += 1
        candidate = ''.join(pieces)
        for _ in range(3):
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError as error:
                repaired = self._smart_fix_json_by_error(candidate, error)
                if repaired == candidate:
                    break
                candidate = repaired
        return candidate

    def _smart_fix_json_by_error(self, json_str: str, error: json.JSONDecodeError) -> str:
        """Insert only a missing structural delimiter after a completed JSON token."""
        delimiter = {
            "Expecting ',' delimiter": ',',
            "Expecting ':' delimiter": ':',
        }.get(error.msg)
        if delimiter is None:
            return json_str
        # The decoder emits these errors outside a string. Never infer missing
        # quotes or replace characters inside an ambiguous string token.
        return json_str[:error.pos] + delimiter + json_str[error.pos:]
