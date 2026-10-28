"""Local prompt overrides and version presets, separate from credentials and source caches."""
import copy
import json
import os
import re
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_FILE = ROOT / 'prompt_defaults.json'
OVERRIDE_FILE = ROOT / 'prompt_overrides.json'
lock = threading.RLock()

def defaults():
    return json.loads(DEFAULT_FILE.read_text(encoding='utf8'))

def validate_templates(templates):
    spec = defaults()
    if not isinstance(templates, dict) or set(templates) != set(spec['templates']):
        raise ValueError('提示词板块不完整，请重新打开编辑器')
    for key, text in templates.items():
        if not isinstance(text, str) or not text.strip() or len(text) > 50000:
            raise ValueError(f'{key} 须为非空文本，最多50000字')
        tokens = re.findall(r'\{\{([a-z_0-9]+)\}\}', text)
        if set(tokens) != set(spec['tokens'][key]):
            raise ValueError(f'{key} 的变量占位符须保留：' + '、'.join(spec['tokens'][key]))
    return templates

def _match_version_id(templates, versions):
    for item in versions:
        if item.get('templates') == templates:
            return item.get('id')
    return 'custom'

def _write_override_data(payload):
    path = Path(OVERRIDE_FILE)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf8')
    os.replace(temporary, path)

def get_prompt_settings():
    with lock:
        spec = defaults()
        data = json.loads(Path(OVERRIDE_FILE).read_text(encoding='utf8')) if Path(OVERRIDE_FILE).is_file() else {}
        templates = data.get('templates', spec['templates'])
        validate_templates(templates)
        builtin_versions = copy.deepcopy(spec.get('versions', []))
        custom_versions = []
        for item in data.get('custom_versions', []):
            if isinstance(item, dict) and item.get('id') and item.get('name') and isinstance(item.get('templates'), dict):
                try:
                    validate_templates(item['templates'])
                    custom_versions.append({**item, 'builtin': False})
                except ValueError:
                    continue
        versions = [*builtin_versions, *custom_versions]
        active_version = _match_version_id(templates, versions)
        return {
            **spec,
            'templates': templates,
            'defaults': spec['templates'],
            'revision': data.get('revision', 0),
            'versions': versions,
            'active_version': active_version,
        }

def save_prompt_settings(templates, revision, version_name=None, version_description='', quality_root=None):
    with lock:
        current = get_prompt_settings()
        if revision != current['revision']:
            raise RuntimeError('提示词已在其他窗口修改，请重新打开后编辑')
        validate_templates(templates)
        from quality_store import QualityStore, fingerprint
        state = QualityStore(quality_root or Path(OVERRIDE_FILE).parent / 'data_cache' / 'quality').version_state()
        if state['versions'].get(fingerprint(templates), {}).get('retired'):
            raise ValueError('此提示词版本已停用，请到提示词实验室恢复后再采用')
        raw = json.loads(Path(OVERRIDE_FILE).read_text(encoding='utf8')) if Path(OVERRIDE_FILE).is_file() else {}
        custom_versions = list(raw.get('custom_versions', []))
        if version_name is not None:
            clean_name = str(version_name).strip()
            if not clean_name or len(clean_name) > 80:
                raise ValueError('提示词版本名称须为1–80个字符')
            existing = next((item for item in custom_versions if item.get('name') == clean_name), None)
            if existing:
                existing['templates'] = copy.deepcopy(templates)
                if version_description is not None:
                    existing['description'] = str(version_description).strip()[:300]
            else:
                custom_versions.append({
                    'id': 'custom_' + uuid.uuid4().hex[:10],
                    'name': clean_name,
                    'description': str(version_description or '用户自定义保存版本').strip()[:300],
                    'builtin': False,
                    'templates': copy.deepcopy(templates),
                })
        _write_override_data({
            'templates': templates,
            'revision': revision + 1,
            'custom_versions': custom_versions,
        })
        return get_prompt_settings()

def delete_prompt_version(version_id, revision):
    with lock:
        current = get_prompt_settings()
        if revision != current['revision']:
            raise RuntimeError('提示词已在其他窗口修改，请重新打开后编辑')
        spec = defaults()
        if any(item.get('id') == version_id for item in spec.get('versions', [])):
            raise ValueError('内置提示词版本不可删除')
        raw = json.loads(Path(OVERRIDE_FILE).read_text(encoding='utf8')) if Path(OVERRIDE_FILE).is_file() else {}
        custom_versions = list(raw.get('custom_versions', []))
        filtered = [item for item in custom_versions if item.get('id') != version_id]
        if len(filtered) == len(custom_versions):
            raise ValueError('要删除的自定义版本不存在')
        _write_override_data({
            'templates': current['templates'],
            'revision': revision + 1,
            'custom_versions': filtered,
        })
        return get_prompt_settings()
