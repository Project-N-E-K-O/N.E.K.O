"""JSON parsing and bounded syntax repair extracted unchanged from InkAI."""
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
        """Repair common JSON formatting problems with a simplified strategy."""
        import re

        # 【关键强化】第一步：去除首尾空白字符（包括换行符、制表符、空格）
        json_str = json_str.strip()
        print(f"🔧 已去除首尾空白字符（强化版）")

        # 定义修复模式（按优先级顺序）
        fix_patterns = [
            # 1. 替换中文引号和控制字符（但不处理JSON结构中的换行符）
            (lambda s: s.replace('"', '"').replace('"', '"').replace(''', "'").replace(''', "'"),
             "替换中文引号"),

            # 2. 处理JSON结构中的换行符和制表符（只在字符串值内转义）
            (self._fix_control_chars_in_strings, "修复字符串值中的控制字符"),

            # 2. 移除对象和数组末尾的多余逗号
            (lambda s: re.sub(r',(\s*[}\]])', r'\1', s), "移除多余逗号"),

            # 3. 【终极修复】转义字符串内部的未转义引号
            # 使用状态机方法，准确处理所有嵌套引号情况
            (self._escape_inner_quotes_in_strings, "转义字符串内部的未转义引号"),

            # 4. 核心：修复未转义的双引号 - 在数组中
            # 修正版：使用更宽松的匹配，允许内容中包含双引号
            (lambda s: re.sub(r'(\[\s*")(.*?)("(?=\s*[,\]]))',
                             lambda m: m.group(1) + m.group(2).replace('"', '\\"') + m.group(3), s),
             "修复数组中的未转义引号"),

            # 5. 修复缺失的定界符
            (self._fix_missing_delimiters_simple, "修复缺失的定界符"),
        ]

        # 逐层尝试修复 - 修正版（先修复再验证）
        for fix_func, fix_name in fix_patterns:
            # **关键修改：先应用修复，再尝试解析！**
            old_str = json_str
            json_str = fix_func(json_str)
            if old_str != json_str:
                print(f"🔧 {fix_name} 已应用")

            # 尝试解析，看当前修复是否已解决问题
            try:
                json.loads(json_str)
                print(f"✅ {fix_name} 修复成功，JSON有效!")
                return json_str
            except json.JSONDecodeError:
                pass  # 继续下一个修复

        # 所有常规修复都失败后，才调用智能修复
        try:
            json.loads(json_str)
            return json_str
        except json.JSONDecodeError as e:
            print(f"🔧 尝试智能修复最后的结构问题")
            current_error = e
            for _ in range(3):
                repaired_json = self._smart_fix_json_by_error(
                    json_str,
                    current_error,
                )
                if repaired_json == json_str:
                    break
                json_str = repaired_json
                # 一个响应可能同时缺少多个定界符；每次修复后立即复验，
                # 只在仍有结构错误时继续同一条有界修复链。
                try:
                    json.loads(json_str)
                    print("✅ 智能修复成功，JSON有效!")
                    return json_str
                except json.JSONDecodeError as next_error:
                    current_error = next_error
            print("⚠️ 智能修复未能产生有效JSON")

        return json_str


    def _fix_missing_delimiters_simple(self, json_str: str) -> str:
        """Repair simple missing delimiters."""
        # 启发式修复：如果以逗号或冒号结尾，尝试添加 }
        stripped = json_str.rstrip()
        if stripped.endswith((',', ':')):
            json_str = stripped.rstrip(',').rstrip(':') + '}'
            print(f"   添加缺失的 '}}'")
        return json_str


    def _smart_fix_json_by_error(self, json_str: str, error: json.JSONDecodeError) -> str:
        """Repair JSON using the parser's error details."""
        error_msg = str(error)
        print(f"🔧 智能修复JSON错误: {error_msg}")

        # 获取错误位置
        error_pos = getattr(error, 'pos', None)
        error_lineno = getattr(error, 'lineno', None)
        error_colno = getattr(error, 'colno', None)

        # 通用delimiter修复：提取 Expecting 'X' delimiter 中的 X
        import re
        delimiter_pattern = r"Expecting '(.+?)' delimiter"
        delimiter_match = re.search(delimiter_pattern, error_msg)
        if delimiter_match:
            missing_char = delimiter_match.group(1)

            # 直接在错误位置插入缺失的符号
            if error_pos is not None and error_pos <= len(json_str):
                print(f"🔧 在位置 {error_pos} 插入缺失的 '{missing_char}'")
                fixed_json = json_str[:error_pos] + missing_char + json_str[error_pos:]
                return fixed_json
            else:
                print(f"⚠️ 错误位置无效: {error_pos}")
                return json_str

        # 方案2：处理特殊情况
        special_fixes = {
            "Unterminated string": '"',
        }

        for pattern, fix_char in special_fixes.items():
            if pattern in error_msg and error_pos is not None and error_pos <= len(json_str):
                print(f"🔧 特殊修复: {pattern}")
                return json_str[:error_pos] + fix_char + json_str[error_pos:]

        # 方案3：控制字符处理
        if "Invalid control character" in error_msg and error_pos is not None and error_pos < len(json_str):
            char_to_escape = json_str[error_pos]
            escaped_char = self._escape_control_char(char_to_escape)
            if escaped_char:
                print(f"🔧 转义控制字符 {repr(char_to_escape)} -> {escaped_char}")
                return json_str[:error_pos] + escaped_char + json_str[error_pos + 1:]

        print(f"⚠️ 无法修复: {error_msg}")
        return json_str


    def _escape_control_char(self, char: str) -> str:
        """Escape control characters into JSON-compatible representations."""
        escape_map = {
            '\n': '\\n',
            '\r': '\\r',
            '\t': '\\t',
            '\b': '\\b',
            '\f': '\\f',
        }
        return escape_map.get(char, None)  # 如果不是已知控制字符，返回None


    def _fix_control_chars_in_strings(self, json_str: str) -> str:
        """Escape control characters only within string values, preserving JSON structure."""
        import re

        # 匹配字符串值，并在其中转义控制字符
        def fix_string_content(match):
            quote_start = match.group(1)  # 开始引号和前缀
            content = match.group(2)      # 字符串内容
            quote_end = match.group(3)    # 结束引号和后缀

            # 只转义字符串内容中的控制字符
            fixed_content = (content.replace('\n', '\\n')
                                   .replace('\r', '\\r')
                                   .replace('\t', '\\t')
                                   .replace('\b', '\\b')
                                   .replace('\f', '\\f'))

            return quote_start + fixed_content + quote_end

        # 匹配键值对中的字符串值
        json_str = re.sub(r'("[^"]*"\s*:\s*")(.*?)(")', fix_string_content, json_str, flags=re.DOTALL)

        # 匹配数组中的字符串值
        json_str = re.sub(r'(\[\s*")(.*?)(")', fix_string_content, json_str, flags=re.DOTALL)

        return json_str


    def _escape_inner_quotes_in_strings(self, json_str: str) -> str:
        """Use a state machine to escape unescaped double quotes inside JSON strings."""
        result = []
        in_string = False  # 是否在字符串内
        escape_next = False  # 下一个字符是否被转义

        for i, char in enumerate(json_str):
            if escape_next:
                # 当前字符被转义，直接添加
                result.append(char)
                escape_next = False
                continue

            if char == '\\':
                # 遇到反斜杠，标记下一个字符需要转义
                result.append(char)
                escape_next = True
                continue

            if char == '"':
                if not in_string:
                    # 进入字符串
                    in_string = True
                    result.append(char)
                else:
                    # 在字符串内部
                    # 检查下一个字符，判断这个引号是否是字符串的结束
                    next_chars = json_str[i+1:].lstrip()  # 跳过后续空白字符
                    if next_chars and next_chars[0] in [':', ',', '}', ']']:
                        # 下一个有效字符是JSON结构符，说明这是字符串结束
                        in_string = False
                        result.append(char)
                    else:
                        # 下一个字符不是结构符，说明这是字符串内部的引号，需要转义
                        result.append('\\"')
            else:
                # 普通字符
                result.append(char)

        fixed_str = ''.join(result)
        if fixed_str != json_str:
            print(f"🔧 转义了字符串内部的未转义引号")
        return fixed_str
