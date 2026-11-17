"""显式应用组装；导入本模块不会打开数据库或启动后台任务。"""
from dataclasses import dataclass, field
from kbapp.core.paths import Paths
from kbapp.storage.store import Store
from kbapp.search.indexing import Search

@dataclass
class AppContext:
    paths: Paths
    store: Store
    search: Search
    background_jobs: bool = True

    jobs: object = field(init=False)

    def __post_init__(self):
        from kbapp.workflows.jobs import JobCoordinator
        self.jobs = JobCoordinator(self.store)

    @classmethod
    def create(cls, base=None, data_dir=None, *, background_jobs=True):
        paths = Paths.from_base(base, data_dir)
        if (paths.data / 'workbench.sqlite3').exists():
            raise ValueError('发现旧数据布局，请先执行 data migrate；未自动创建第二套数据库')
        journal = paths.state / 'layout-migration.json'
        if journal.exists():
            import json
            if json.loads(journal.read_text(encoding='utf-8'))['status'] not in {'complete','rolled_back'}:
                raise ValueError('目录迁移尚未完成，请先执行 data migrate 续迁')
        store = Store(paths=paths)
        from kbapp.research.verified import audit_legacy_seeds
        audit_legacy_seeds(store)
        return cls(paths, store, Search(store), background_jobs)
