"""项目路径与数据布局；构造和查询均不创建目录。"""
from contextlib import closing
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sqlite3
LAYOUT_VERSION = 1
DIRECTORIES = ('state', 'indexes', 'cache', 'files', 'runtime', 'exports', 'backups', 'archive')
CACHE_NAMES = {'ocr_cache': 'ocr_cache', 'ocr_pages': 'ocr_pages', 'recognized-sources': 'recognized_sources', 'workbench-vector-cache': 'vectors', 'local-reading-cache': 'local_reading', 'relation-review-cache': 'relation_review', 'reference-metadata': 'references', 'metadata-cache': 'metadata', 'figures': 'extraction/figures', 'formulas': 'extraction/formulas', 'references': 'extraction/references'}

def legacy_target(relative):
    """一次性迁移映射。历史文件不参与新运行时的隐式查找。"""
    relative = Path(relative)
    parts = relative.parts
    name = parts[0]
    rest = Path(*parts[1:]) if len(parts) > 1 else Path()
    if name in DIRECTORIES:
        return relative
    if name == 'versions':
        return Path('indexes/versions') / rest
    if name in {'workbench.sqlite3', 'workbench.sqlite3-wal', 'workbench.sqlite3-shm'}:
        return Path('state') / relative
    if name == 'workbench-settings.json':
        return Path('state/settings.json')
    if name in CACHE_NAMES:
        return Path('cache') / CACHE_NAMES[name] / rest
    if name == 'citation' and len(parts) > 1 and (parts[1] == 'openalex'):
        return Path('cache/openalex').joinpath(*parts[2:])
    if name == 'citation' and rest == Path('evolution.html'):
        return Path('exports/evolution.html')
    if name in {'external-pdfs', 'pdf_fetch'}:
        return Path('files') / relative
    if name == 'model-queue.sqlite3':
        return Path('runtime') / relative
    if name.endswith('.lock'):
        return Path('runtime/locks') / relative
    if name == 'agent-errors':
        return Path('runtime/logs/agents') / rest
    if name.endswith('.log'):
        return Path('runtime/logs') / relative
    if name in {'evaluation', 'import'}:
        return Path('exports') / relative
    if name in {'current.json', 'llms.txt', 'compact.md', 'audit.json', 'audit.md', 'workbench-report.json'}:
        return Path('exports') / relative
    return Path('archive/legacy') / relative

@dataclass(frozen=True)
class Paths:
    base: Path
    data: Path

    @classmethod
    def from_base(cls, base=None, data_dir=None):
        project = Path(base or os.environ.get('KB_BASE') or Path(__file__).resolve().parents[2]).resolve()
        environment_data = os.environ.get('KB_DATA_DIR') if base is None or project == Path(os.environ.get('KB_BASE', project)).resolve() else None
        data = Path(data_dir or environment_data or project / '_index').resolve()
        return cls(project, data)

    @classmethod
    def for_data(cls, data):
        data = Path(data).resolve()
        return cls(data.parent, data)

    @property
    def state(self):
        return self.data / 'state'

    @property
    def indexes(self):
        return self.data / 'indexes'

    @property
    def cache(self):
        return self.data / 'cache'

    @property
    def files(self):
        return self.data / 'files'

    @property
    def runtime(self):
        return self.data / 'runtime'

    @property
    def exports(self):
        return self.data / 'exports'

    @property
    def backups(self):
        return self.data / 'backups'

    @property
    def archive(self):
        return self.data / 'archive'

    @property
    def database(self):
        return self.state / 'workbench.sqlite3'

    def active_index(self):
        if self.database.is_file():
            with closing(sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True)) as db, db:
                row = db.execute("SELECT value FROM state WHERE key='active_version'").fetchone()
            if row and json.loads(row[0]):
                return self.indexes / 'versions' / json.loads(row[0])
        return self.indexes / 'unpublished'

    def ensure_layout(self):
        for name in DIRECTORIES:
            (self.data / name).mkdir(parents=True, exist_ok=True)

    @property
    def assets(self):
        return Path(__file__).resolve().parents[1] / 'web/assets'
