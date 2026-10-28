"""Refresh the level snapshot independently of the unsafe legacy full sync."""
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid
from datetime import datetime

from feishu_writeback import WritebackError, rows_from_response

LOCK = threading.RLock()
REQUIRED_FIELDS = {'关卡号', '关卡名称', '关卡特性', '关卡主题以及其他描述'}


def atomic_json(path, data):
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def refresh_levels(client, cache_dir, backup_dir):
    """Publish only a complete validated table; keep a recoverable prior snapshot."""
    with LOCK:
        table = client.target.get('table_ids', {}).get('levels')
        if not table:
            raise WritebackError('尚未配置关卡库目标表')
        schema = client.run('+field-list', table=table)
        fields = schema.get('fields', [])
        if not REQUIRED_FIELDS <= {f.get('name') for f in fields if isinstance(f, dict)}:
            raise WritebackError('关卡库字段不完整，请检查目标表')
        records, ids, offset, revision = [], set(), 0, None
        while True:
            page = client.run('+record-list', table=table, extra=('--offset', str(offset), '--limit', '200'))
            if not isinstance(page.get('has_more'), bool):
                raise WritebackError('关卡分页信息不完整')
            if offset and page.get('rev') != revision:
                raise WritebackError('读取期间关卡库发生变化，请重新刷新')
            revision = page.get('rev')
            rows = rows_from_response({**page, 'has_more': False})
            for row in rows:
                rid = row['_record_id']
                if not rid or rid in ids or not REQUIRED_FIELDS <= row.keys():
                    raise WritebackError('关卡记录缺少字段或存在重复ID')
                if not isinstance(row['关卡名称'], str) or not row['关卡名称'].strip():
                    raise WritebackError('关卡名称为空，无法确认完整数据')
                ids.add(rid)
            records.extend(rows)
            if not page['has_more']:
                break
            if not rows or offset > 100000:
                raise WritebackError('关卡分页异常')
            offset += len(rows)
        if not records:
            raise WritebackError('飞书返回空关卡库，请先核对目标表；不会清空本地关卡')
        cache = Path(cache_dir)
        path, manifest_path = cache / 'levels.json', cache / 'levels.manifest.json'
        old = {p: p.read_bytes() if p.exists() else None for p in (path, manifest_path)}
        stamp = datetime.now().astimezone().isoformat()
        manifest = {
            'manifest_version': 'local-json-v2', 'format': 'json', 'table_id': table,
            'target_fingerprint': hashlib.sha256((client.target['base_token'] + ':' + table).encode()).hexdigest(),
            'record_file': str(path.resolve()), 'records_count': len(records),
            'has_more': False, 'rev': revision, 'updated_at': stamp,
            'snapshot_scope': '关卡库完整只读刷新',
            'columns': {f['name']: {'field_type': f.get('type'), 'stats': {
                'null_count': sum(r.get(f['name']) is None for r in records),
                'empty_count': sum(r.get(f['name']) in ('', []) for r in records)
            }} for f in fields if isinstance(f, dict) and isinstance(f.get('name'), str)}
        }
        backup = Path(backup_dir) / (datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8])
        backup.mkdir(parents=True, exist_ok=False)
        for p, content in old.items():
            if content is not None:
                (backup / p.name).write_bytes(content)
        cache.mkdir(parents=True, exist_ok=True)
        try:
            atomic_json(manifest_path, manifest)
            atomic_json(path, records)
        except OSError:
            # Restore both files if publishing either one fails.
            for p, content in old.items():
                if content is None:
                    p.unlink(missing_ok=True)
                else:
                    p.write_bytes(content)
            raise
        return {'ok': True, 'record_count': len(records), 'updated_at': stamp}
