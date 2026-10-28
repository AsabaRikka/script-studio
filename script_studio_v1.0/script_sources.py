"""Derive references and actual weapon attribution from persisted facts."""

def normalize_source_ids(values, allowed_ids, sources, context):
    """Resolve display codes only within the manuscript's selected sources."""
    if values is None:
        return []
    if not isinstance(values, list):
        raise ValueError(f"{context}格式错误，应为记录ID数组")
    allowed = set(allowed_ids)
    aliases = {}
    for row in sources:
        rid = row.get('id') or row.get('_record_id')
        code = row.get('code') or row.get('分镜编号') or row.get('组件编号')
        if rid in allowed and isinstance(code, str) and code.strip():
            aliases.setdefault(code.strip(), set()).add(rid)
    resolved = []
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{context}格式错误，应为非空记录ID数组")
        value = value.strip()
        if value in allowed:
            rid = value
        else:
            matches = aliases.get(value, set())
            if len(matches) > 1:
                raise ValueError(f"{context}编号{value}对应多条所选素材，请改为明确的记录ID")
            if not matches:
                raise ValueError(f"{context}引用了本次组合范围外的素材：{value}，请改为本篇所选素材的记录ID")
            rid = next(iter(matches))
        if rid not in resolved:
            resolved.append(rid)
    return resolved

def source_reference(record, load_cache=None):
    revision = record.get('revision_source')
    if revision:
        parts = ['【改写前文案】\n' + revision.get('script','')]
        if revision.get('reference'):
            parts.append('【补充参考】\n' + revision['reference'])
        base = {key:value for key,value in record.items() if key not in ('revision_source','source_reference')}
        if (base.get('source_slot') or {}).get('rivals'):
            parts.append(source_reference(base, load_cache))
        return '\n\n'.join(parts)
    slot = record.get('source_slot') or {}
    rivals = slot.get('rivals', [])
    cached = {}
    if load_cache and any(not r.get('raw_script') for r in rivals):
        cached = {r['_record_id']: r for r in load_cache('rival_struct')}
    parts = []
    for r in rivals:
        text = r.get('raw_script') or cached.get(r.get('id'), {}).get('口播文案', '')
        if text:
            parts.append(f"【来源 {r.get('code') or r.get('id', '')}】\n{text}")
    if parts:
        return '\n\n'.join(parts)
    if slot.get('mode') == '本体事实-自主原创':
        return '本体玩法库自主原创，无竞对原文'
    return record.get('ref_script') or record.get('参考文案') or '未保存来源原文'


def used_weapons(script, candidates):
    found = {}
    for weapon in candidates:
        name = weapon.get('name') or weapon.get('武器名称', '')
        rid = weapon.get('id') or weapon.get('_record_id')
        if name and rid and name in script:
            found[rid] = {'id': rid, 'name': name}
    return list(found.values())
