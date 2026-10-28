"""Local immutable prompt bundles, generation traces and revision-bound feedback."""
import copy
import hashlib
import json
import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path

LOCK = threading.RLock()


def now():
    return datetime.now().astimezone().isoformat()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(',', ':'), allow_nan=False).encode('utf8')).hexdigest()


def script_text(record, target='ai'):
    if target == 'manual':
        return record.get('manual_script') or record.get('人工修改稿') or ''
    return record.get('full_script') or record.get('二创文案') or ''


def body_identity(record, target='ai'):
    text = script_text(record, target)
    if not isinstance(text, str) or not text.strip() or len(text) > 50000:
        raise ValueError('评价对象须为非空正文，最多50000字')
    code = record.get('material_code') or record.get('素材编号') or ''
    if not isinstance(code, str) or not code or len(code) > 200:
        raise ValueError('评价对象缺少有效素材编号')
    return {'material_code': code, 'target': target, 'text_hash': fingerprint(text),
            'edit_revision': int(record.get('_edit_revision', 0)), 'text': text}


class QualityStore:
    def __init__(self, root):
        self.root = Path(root)

    def path(self, group, key):
        if group not in {'bundles', 'traces', 'feedback', 'state', 'jobs', 'plans', 'datasets'}:
            raise ValueError('未知质量数据目录')
        if not re.fullmatch(r'[a-z0-9_-]{1,80}', str(key)):
            raise ValueError('无效质量数据编号')
        return self.root / group / (key + '.json')

    def read(self, group, key):
        with LOCK:
            path = self.path(group, key)
            return json.loads(path.read_text(encoding='utf8')) if path.is_file() else None

    def write(self, group, key, value, immutable=False):
        with LOCK:
            path = self.path(group, key)
            if immutable and path.exists():
                return self.read(group, key)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
                os.replace(tmp, path)
            finally:
                if tmp.exists():
                    tmp.unlink()
            return copy.deepcopy(value)

    def list(self, group, limit=100):
        with LOCK:
            paths = sorted((self.root / group).glob('*.json'), key=lambda p: p.stat().st_mtime, reverse=True)
            return [json.loads(p.read_text(encoding='utf8')) for p in paths[:limit]]

    def bundle(self, templates, name='自定义版本', revision=None):
        key = fingerprint(templates)
        return self.write('bundles', key, {'id': key, 'name': name, 'revision': revision,
                          'created_at': now(), 'templates': templates}, immutable=True)

    def version_state(self):
        return self.read('state', 'versions') or {'revision': 0, 'versions': {}}

    def change_version_state(self, key, retired, revision):
        with LOCK:
            state = self.version_state()
            if state['revision'] != revision:
                raise RuntimeError('版本状态已更新，请重新读取后操作')
            if not re.fullmatch('[a-f0-9]{64}', key):
                raise ValueError('无效版本哈希')
            state['versions'][key] = {'retired': bool(retired), 'updated_at': now()}
            state['revision'] += 1
            return self.write('state', 'versions', state)

    def add_feedback(self, record, target, decision, reasons, note, event_id, reviewer='human'):
        identity = body_identity(record, target)
        event = {**identity, 'id': event_id, 'decision': decision, 'reviewer': reviewer,
                 'reasons': reasons, 'note': note,
                 'generation_meta': record.get('generation_meta') or {'status': 'unknown'},
                 'creative_meta': record.get('creative_meta') or {}}
        with LOCK:
            old = self.read('feedback', event_id)
            if old:
                if {k: v for k, v in old.items() if k != 'created_at'} != event:
                    raise RuntimeError('该反馈编号已提交不同内容，请重新操作')
                return old
            return self.write('feedback', event_id, {**event, 'created_at': now()}, immutable=True)

    def feedback_for(self, record, target='ai'):
        identity = body_identity(record, target)
        # Feedback is small and indexed separately; no historical draft is rewritten.
        matching = [event for event in self.list('feedback', 10000)
                    if event['material_code'] == identity['material_code'] and event['target'] == target]
        exact = [event for event in matching if event['text_hash'] == identity['text_hash']
                 and event['edit_revision'] == identity['edit_revision']]
        return {'identity': identity, 'events': exact, 'latest': exact[0] if exact else None,
                'stale_count': len(matching) - len(exact)}
