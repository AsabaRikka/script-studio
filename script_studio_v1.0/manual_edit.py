"""Validate human revisions while preserving generated text and source attribution."""
import copy
import math
import re


def validate_manual(record, script, shots):
    if not isinstance(script, str) or len(script) > 50000:
        raise ValueError('人工修改稿须为文本，最多50000字')
    if shots is None:
        return script, None
    original = record.get('shots') or []
    if not isinstance(shots, list) or not original or len(shots) != len(original):
        raise ValueError('人工分镜数量须与原稿一致，请逐镜修改')
    cleaned = []
    for index, shot in enumerate(shots, 1):
        if not isinstance(shot, dict):
            raise ValueError(f'第{index}镜格式错误')
        row = {}
        for key in ('text', 'visual', 'game_action'):
            value = shot.get(key, '')
            if not isinstance(value, str) or len(value) > 10000 or (key == 'text' and not value.strip()):
                raise ValueError(f'第{index}镜{key}为空或格式错误')
            row[key] = value
        try:
            duration = None if shot.get('duration') is None else float(shot['duration'])
        except (TypeError, ValueError):
            raise ValueError(f'第{index}镜时长无效')
        if duration is not None and (not math.isfinite(duration) or duration < 0):
            raise ValueError(f'第{index}镜参考时长须为非负有限数或留空')
        row['duration'] = duration
        cleaned.append(row)
    # Free manuscript editing may intentionally precede explicit scene assignment.
    return script, cleaned


def apply_manual(record, script, shots):
    script, shots = validate_manual(record, script, shots)
    result = copy.deepcopy(record)
    result['manual_script'] = script
    result['manual_shots'] = shots
    result['_edit_revision'] = int(record.get('_edit_revision', 0)) + 1
    return result
