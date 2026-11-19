"""可配置的本机缓存目录:路径默认值、覆盖、复用与错误可见性。

覆盖范围只到 ``paths.cache``;state / indexes / files 仍跟随数据目录,
所以未设置 ``cache_dir`` 的安装行为与历史完全一致(这条是首个用例)。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ['KB_NO_STARTUP_JOBS'] = '1'
os.environ['KB_NO_REFERENCE_WORKER'] = '1'
from fastapi.testclient import TestClient

from kbapp.core.paths import Paths
from kbapp.storage.store import Store
from support import service_for


class PathsCacheRoot(unittest.TestCase):
    """The Paths half: no Store, no settings file, just the layout rule."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name) / '_index'

    def test_default_cache_is_under_the_data_dir(self):
        paths = Paths.from_base(self.temp.name, data_dir=str(self.data))
        self.assertFalse(paths.cache_overridden)
        self.assertEqual(paths.cache, self.data / 'cache')
        self.assertEqual(paths.default_cache, self.data / 'cache')

    def test_override_moves_only_the_cache_subtree(self):
        cache = Path(self.temp.name) / 'elsewhere' / 'cache'
        paths = Paths.from_base(self.temp.name, data_dir=str(self.data), cache_dir=str(cache))
        self.assertTrue(paths.cache_overridden)
        self.assertEqual(paths.cache, cache.resolve())
        # Everything else still follows the data directory.
        self.assertEqual(paths.state, self.data / 'state')
        self.assertEqual(paths.indexes, self.data / 'indexes')
        self.assertEqual(paths.files, self.data / 'files')
        self.assertEqual(paths.database, self.data / 'state' / 'workbench.sqlite3')
        # And the historical location stays discoverable for a rollback.
        self.assertEqual(paths.default_cache, self.data / 'cache')

    def test_ensure_layout_creates_the_overridden_cache(self):
        cache = Path(self.temp.name) / 'elsewhere' / 'cache'
        paths = Paths.from_base(self.temp.name, data_dir=str(self.data), cache_dir=str(cache))
        paths.ensure_layout()
        self.assertTrue(cache.is_dir())
        self.assertTrue((self.data / 'state').is_dir())
        # The default cache must not be created when an override is in force:
        # two live cache trees would make "which one is in use" ambiguous.
        self.assertFalse((self.data / 'cache').exists())

    def test_for_data_accepts_an_override(self):
        data = Path(self.temp.name) / '_index'
        cache = Path(self.temp.name) / 'c'
        paths = Paths.for_data(str(data), cache_dir=str(cache))
        self.assertEqual(paths.cache, cache.resolve())


class StoreCacheDir(unittest.TestCase):
    """The Store half: the setting, its persistence and the visible failures."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / 'custom-cache'
        self.store = Store(Path(self.temp.name) / 'lib')

    def reopen(self):
        self.store = Store(Path(self.temp.name) / 'lib')
        return self.store

    def test_empty_setting_keeps_the_historical_layout(self):
        self.assertFalse(self.store.paths.cache_overridden)
        self.assertEqual(self.store.paths.cache, self.store.paths.data / 'cache')
        info = self.store.cache_info()
        self.assertEqual(info['path'], str(self.store.paths.data / 'cache'))
        self.assertFalse(info['overridden'])

    def test_saving_a_cache_dir_applies_it_immediately(self):
        self.store.save_settings({'cache_dir': str(self.cache)})
        # No restart between save and use: the same process already points at it.
        self.assertTrue(self.store.paths.cache_overridden)
        self.assertEqual(self.store.paths.cache, self.cache.resolve())
        self.assertTrue(self.cache.is_dir())
        info = self.store.cache_info()
        self.assertTrue(info['overridden'])
        self.assertTrue(info['exists'])

    def test_the_choice_survives_a_restart(self):
        self.store.save_settings({'cache_dir': str(self.cache)})
        reopened = self.reopen()
        self.assertTrue(reopened.paths.cache_overridden)
        self.assertEqual(reopened.paths.cache, self.cache.resolve())

    def test_clearing_the_setting_returns_to_the_default(self):
        self.store.save_settings({'cache_dir': str(self.cache)})
        self.store.save_settings({'cache_dir': ''})
        self.assertFalse(self.store.paths.cache_overridden)
        self.assertEqual(self.store.paths.cache, self.store.paths.data / 'cache')

    def test_a_relative_path_is_refused_with_a_readable_message(self):
        with self.assertRaises(ValueError) as caught:
            self.store.save_settings({'cache_dir': 'relative/cache'})
        self.assertIn('绝对路径', str(caught.exception))

    def test_an_unwritable_target_fails_loudly_and_keeps_the_old_path(self):
        # A file where the cache directory should go: mkdir cannot succeed.
        blocker = Path(self.temp.name) / 'blocker'
        blocker.write_text('not a directory', encoding='utf-8')
        with self.assertRaises(OSError):
            self.store.save_settings({'cache_dir': str(blocker / 'cache')})
        # The in-memory layout did not half-switch.
        self.assertEqual(self.store.paths.cache, self.store.paths.data / 'cache')

    def test_cache_info_counts_files_and_bytes(self):
        self.store.save_settings({'cache_dir': str(self.cache)})
        (self.cache / 'ocr_pages').mkdir(parents=True, exist_ok=True)
        (self.cache / 'ocr_pages' / 'p0001.txt').write_text('abc', encoding='utf-8')
        (self.cache / 'top.json').write_text('{}', encoding='utf-8')
        info = self.store.cache_info()
        self.assertEqual(info['files'], 2)
        self.assertEqual(info['bytes'], 5)
        self.assertEqual(info['default_path'], str(self.store.paths.data / 'cache'))


class CacheSettingsApi(unittest.TestCase):
    """The HTTP half: the setting round-trips and the projection is present."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cache = Path(self.temp.name) / 'api-cache'
        self.store = Store(Path(self.temp.name) / 'lib')
        state = service_for(self.store)
        self.client = TestClient(state.app)
        # Non-GET requests carry the session token (service.py).
        self.headers = {'X-KB-Token': state.TOKEN}

    def test_get_settings_exposes_the_cache_projection(self):
        body = self.client.get('/api/settings').json()
        self.assertIn('cache', body)
        self.assertIn('path', body['cache'])
        self.assertIn('files', body['cache'])
        self.assertIn('overridden', body['cache'])
        self.assertEqual(body['cache_dir'], '')

    def test_post_settings_saves_and_applies_the_cache_dir(self):
        response = self.client.post('/api/settings', json={'cache_dir': str(self.cache)}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        body = self.client.get('/api/settings').json()
        self.assertEqual(body['cache_dir'], str(self.cache))
        self.assertTrue(body['cache']['overridden'])
        self.assertEqual(body['cache']['path'], str(self.cache.resolve()))
        self.assertTrue(self.cache.is_dir())

    def test_post_settings_reports_a_bad_path_instead_of_swallowing_it(self):
        response = self.client.post('/api/settings', json={'cache_dir': 'not-absolute'}, headers=self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn('绝对路径', response.text)


if __name__ == '__main__':
    unittest.main()
