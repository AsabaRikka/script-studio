import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from feishu_writeback import WritebackError
import level_cache
import server


class LevelCacheTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cache = Path(self.tmp.name) / 'cache'
        self.backup = Path(self.tmp.name) / 'backups'
        self.cache.mkdir()
        self.old = b'[{"_record_id":"old"}]'
        (self.cache / 'levels.json').write_bytes(self.old)
        (self.cache / 'levels.manifest.json').write_text('{"records_count":1}')
        self.fields = [{'name': name, 'type': 'text'} for name in sorted(level_cache.REQUIRED_FIELDS)]
        self.schema = {'fields': self.fields}
        self.target = {'base_token': 'test-secret', 'table_ids': {'levels': 'test-levels'}}

    def page(self, ids, more=False, rev=3):
        return {'fields': self.fields, 'data': [
            [1 if f['name'] == '关卡号' else '测试关卡' for f in self.fields] for _ in ids
        ], 'record_id_list': ids, 'has_more': more, 'rev': rev}

    def client(self, *pages):
        return Mock(target=self.target, run=Mock(side_effect=[self.schema, *pages]))

    def refresh(self, client):
        return level_cache.refresh_levels(client, self.cache, self.backup)

    def test_full_pagination_backup_manifest_and_no_secret(self):
        client = self.client(self.page(['lv1'], True), self.page(['lv2']))
        result = self.refresh(client)
        self.assertEqual(result['record_count'], 2)
        self.assertEqual(client.run.call_args.kwargs['extra'], ('--offset', '1', '--limit', '200'))
        self.assertEqual([r['_record_id'] for r in json.loads((self.cache / 'levels.json').read_text(encoding='utf-8'))], ['lv1', 'lv2'])
        manifest = (self.cache / 'levels.manifest.json').read_text(encoding='utf-8')
        self.assertEqual(json.loads(manifest)['records_count'], 2)
        self.assertFalse(json.loads(manifest)['has_more'])
        self.assertNotIn('test-secret', manifest)
        self.assertEqual(next(self.backup.glob('*/levels.json')).read_bytes(), self.old)
        self.assertEqual(len(json.loads(manifest)['columns']), len(self.fields))

    def test_incomplete_empty_duplicate_changed_revision_and_timeout_preserve_cache(self):
        bad = [
            [self.page([])],
            [self.page(['lv1'], True), self.page([] , True)],
            [self.page(['lv1'], True), self.page(['lv1'])],
            [self.page(['lv1'], True), self.page(['lv2'], rev=4)],
            [{'fields': self.fields, 'data': [], 'record_id_list': []}],
            [WritebackError('timeout')],
            [{'records': [{'record_id': 'lv1', 'fields': {'关卡名称': '不完整'}}], 'has_more': False}],
        ]
        for pages in bad:
            with self.subTest(pages=pages), self.assertRaises(WritebackError):
                self.refresh(self.client(*pages))
            self.assertEqual((self.cache / 'levels.json').read_bytes(), self.old)
            self.assertEqual((self.cache / 'levels.manifest.json').read_text(), '{"records_count":1}')

    def test_wrong_schema_and_publish_failure_preserve_both_files(self):
        client = Mock(target=self.target, run=Mock(return_value={'fields': []}))
        with self.assertRaises(WritebackError):
            self.refresh(client)
        original = level_cache.atomic_json
        def fail_data(path, data):
            if path.name == 'levels.json':
                raise OSError('disk error')
            original(path, data)
        with patch.object(level_cache, 'atomic_json', side_effect=fail_data), self.assertRaises(OSError):
            self.refresh(self.client(self.page(['lv1'])))
        self.assertEqual((self.cache / 'levels.json').read_bytes(), self.old)
        self.assertEqual((self.cache / 'levels.manifest.json').read_text(), '{"records_count":1}')

    def test_route_updates_overview_and_failure_is_visible(self):
        with patch.object(server, 'DATA_DIR', str(self.cache)), patch.object(server, 'LarkClient', return_value=self.client(self.page(['lv1'], True), self.page(['lv2']))):
            client = TestClient(server.app)
            self.assertEqual(client.post('/api/levels/refresh').json()['record_count'], 2)
            overview = client.get('/api/overview').json()
            self.assertEqual(overview['counts']['levels'], 2)
            self.assertEqual({r['id'] for r in overview['levels']}, {'lv1', 'lv2'})
        with patch.object(server.level_cache, 'refresh_levels', side_effect=WritebackError('timeout')):
            result = TestClient(server.app).post('/api/levels/refresh')
        self.assertEqual(result.status_code, 503)
        self.assertIn('已保留原关卡缓存', result.json()['detail'])
