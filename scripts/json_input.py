"""严格读取JSON，避免重复字段覆盖金额或规则。"""
import json

MAX_JSON_DEPTH = 64


class JSONInputError(ValueError):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise JSONInputError("JSON对象有重复字段，请为每个字段保留唯一值")
        result[key] = value
    return result


def reject_constant(_value):
    raise JSONInputError("JSON不能包含NaN或Infinity")


def check_depth(value):
    pending = [(value, 0)]
    while pending:
        node, depth = pending.pop()
        if isinstance(node, (dict, list)):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise JSONInputError("JSON嵌套不能超过64层")
            children = node.values() if isinstance(node, dict) else node
            pending.extend((child, depth) for child in children)


def parse_json(raw):
    try:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        if not isinstance(raw, str):
            raise JSONInputError("JSON内容必须是UTF-8文本")
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        check_depth(value)
        return value
    except JSONInputError:
        raise
    except (ValueError, UnicodeError, RecursionError):
        raise JSONInputError("输入不是有效的UTF-8 JSON，或嵌套层数过多") from None
