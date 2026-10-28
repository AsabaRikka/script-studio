"""Normalize model select labels without guessing or discarding unknown options."""
import json
import re


def normalize_labels(value, field='情绪标签'):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        if value.startswith('['):
            try:
                value = json.loads(value)
            except ValueError:
                raise ValueError(f'{field}格式错误：请使用标签文字或文字数组')
        else:
            value = re.split(r'[,，、;；|｜/\n]+', value)
    if not isinstance(value, list) or any(not isinstance(label, str) for label in value):
        raise ValueError(f'{field}格式错误：请使用标签文字或文字数组')
    return list(dict.fromkeys(label.strip() for label in value if label.strip()))
