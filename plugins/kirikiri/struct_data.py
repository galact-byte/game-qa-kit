"""仅处理本桥产生的 saveStruct 数据子集；不执行 TJS 表达式。"""
import json
import re

_TOKEN = re.compile(r'\s*(\(const\)|%\[|=>|\[|\]|,|"(?:\\.|[^"\\])*"|-?[0-9]+)')


def decode(text):
    tokens = []
    offset = 0
    while offset < len(text):
        match = _TOKEN.match(text, offset)
        if not match:
            if not text[offset:].strip():
                break
            raise ValueError('观察数据含未允许的 TJS 语法')
        token = match[1]
        if token != '(const)':
            tokens.append(token)
        offset = match.end()
    position = 0

    def take(expected=None):
        nonlocal position
        if position == len(tokens):
            raise ValueError('观察数据不完整')
        token = tokens[position]
        position += 1
        if expected is not None and token != expected:
            raise ValueError('观察数据分隔符错误')
        return token

    def value(depth=0):
        if depth > 16:
            raise ValueError('观察数据嵌套过深')
        token = take()
        if token in ('%[', '['):
            result = {} if token == '%[' else []
            if position < len(tokens) and tokens[position] != ']':
                while True:
                    if isinstance(result, dict):
                        key = take()
                        if not key.startswith('"'):
                            raise ValueError('观察数据键必须是字符串')
                        key = json.loads(key)
                        take('=>')
                        if key in result:
                            raise ValueError('观察数据含重复键')
                        result[key] = value(depth + 1)
                    else:
                        result.append(value(depth + 1))
                    if position < len(tokens) and tokens[position] == ']':
                        break
                    take(',')
            take(']')
            return result
        if token.startswith('"'):
            return json.loads(token)
        try:
            return int(token)
        except ValueError as error:
            raise ValueError('观察数据值无效') from error

    result = value()
    if position != len(tokens) or not isinstance(result, dict):
        raise ValueError('观察数据必须是单一字典')
    return result
