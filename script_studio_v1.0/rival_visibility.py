"""Read-only Feishu status snapshot; original material caches stay untouched."""
import hashlib
import json
import math
import os
import threading
import time
from datetime import datetime

from feishu_writeback import WritebackError, rows_from_response

snapshot_lock = threading.RLock()
MAX_AGE = 60


def target_key(target):
    value = [target['base_token'], target['table_ids']['rival_struct']]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def status_labels(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list) and all(isinstance(v, str) for v in value):
        return [v.strip() for v in value]
    raise WritebackError('竞对状态格式无法确认，请检查飞书状态字段')


def read_snapshot(path, target):
    try:
        with open(path, encoding='utf-8') as f:
            snapshot = json.load(f)
        if snapshot['target'] != target_key(target):
            return None
        if (not isinstance(snapshot['statuses'], dict)
                or not isinstance(snapshot['checked_at'], (float, int))
                or not math.isfinite(snapshot['checked_at'])
                or snapshot['checked_at'] > time.time()
                or not isinstance(snapshot['updated_at'], str)):
            return None
        if any(not isinstance(rid, str) or not rid for rid in snapshot['statuses']):
            return None
        for value in snapshot['statuses'].values():
            status_labels(value)
        return snapshot
    except (OSError, ValueError, KeyError, TypeError, WritebackError):
        return None


def refresh_snapshot(client, path, target):
    """Commit only complete, validated pagination; failure preserves last snapshot."""
    with snapshot_lock:
        table = target['table_ids']['rival_struct']
        schema = client.run('+field-list', table=table)
        field = next((f for f in schema.get('fields', []) if f.get('name') == '状态'), {})
        if field.get('type') != 'select' or '已使用' not in {o.get('name') for o in field.get('options', [])}:
            raise WritebackError('竞对库缺少“状态 / 已使用”选项，已保留上次快照')
        statuses, offset = {}, 0
        revision = None
        while True:
            page = client.run('+record-list', table=table, extra=(
                '--field-id', '状态', '--offset', str(offset), '--limit', '200'))
            if not isinstance(page.get('has_more'), bool):
                raise WritebackError('竞对状态分页不完整，已保留上次快照')
            if offset and page.get('rev') != revision:
                raise WritebackError('读取期间竞对库发生变化，请重新刷新状态')
            revision = page.get('rev')
            rows = rows_from_response({**page, 'has_more': False})
            for row in rows:
                rid = row['_record_id']
                if not rid or rid in statuses or '状态' not in row:
                    raise WritebackError('竞对状态记录不完整或重复，已保留上次快照')
                statuses[rid] = status_labels(row['状态'])
            if not page['has_more']:
                break
            if not rows or offset > 100000:
                raise WritebackError('竞对状态分页异常，已保留上次快照')
            offset += len(rows)
        snapshot = {'target': target_key(target), 'checked_at': time.time(),
                    'updated_at': datetime.now().astimezone().isoformat(), 'statuses': statuses}
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path + '.tmp', 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=2)
        os.replace(path + '.tmp', path)
        return snapshot


def hidden_ids(snapshot, load_cache):
    statuses = snapshot['statuses']
    # Missing remote parents cannot safely be offered as available materials.
    blocked = {rid for rid, labels in statuses.items() if '已使用' in labels}
    blocked.update(r['_record_id'] for r in load_cache('rival_struct') if r['_record_id'] not in statuses)
    result = {'rivals': blocked}
    for key, cache in [('shots', 'rival_shots'), ('components', 'rival_comp')]:
        result[key] = set()
        for row in load_cache(cache):
            parents = row.get('关联竞对视频') or []
            if isinstance(parents, str):
                parents = [parents]
            ids = [p.get('id') if isinstance(p, dict) else p for p in parents]
            if any(rid in blocked or rid not in statuses for rid in ids if rid):
                result[key].add(row['_record_id'])
    return result
