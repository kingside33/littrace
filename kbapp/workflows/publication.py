"""从唯一数据库指针导出机器入口，不通过 JSON 反向决定活动版本。"""
from contextlib import closing
import json
import sqlite3
from kbapp.core.io import atomic

def publish_manifest(paths):
    if not paths.database.exists():
        return
    with closing(sqlite3.connect(paths.database.as_uri() + '?mode=ro', uri=True)) as db:
        state = {key: json.loads(value) for key, value in db.execute("SELECT key,value FROM state WHERE key IN ('active_version','last_success','coverage','last_update_finished','export_sync')")}
    version = state.get('active_version')
    if not version:
        return
    index = paths.indexes / 'versions' / version
    atomic(paths.exports / 'current.json', {'version': version, 'index_dir': str(index), 'corpus': str(index / 'corpus.jsonl'), 'updated': state.get('last_success'), 'index_published_at':state.get('last_success'), 'update_finished_at':state.get('last_update_finished'), 'export_sync':state.get('export_sync',{}), 'coverage': state.get('coverage', {})})
    atomic(paths.exports / 'llms.txt', f'''# 文献知识库：机器检索入口

活动版本：{version}
项目根目录：{paths.base}
运行数据目录：{paths.data}

优先通过统一命令检索：
python kb.py search "查询词" --json
python kb.py search "查询词" --bm25-only --json
python kb.py doctor status

当前版本的结构化来源：{index / 'corpus.jsonl'}
当前索引描述：{index / 'corpus_meta.json'}
派生版本入口：{paths.exports / 'current.json'}
稳定身份、关系、审批、人工判断和任务历史：{paths.database}
离线图谱：{paths.exports / 'evolution.html'}

SQLite 中的 active_version 是活动版本的唯一权威。读取来源时保留 paper_id、source_id、页码、url 与证据等级；摘要和个人笔记不能替代已核原文。向量可用性以当前权重签名和工作台状态为准。
''')


def sync_exports(store, strict=False):
    """可单独重试派生文件；索引发布不依赖后续模型工作或离线导出。"""
    from kbapp.core.io import now
    from kbapp.workflows.notes import publish_notes
    from kbapp.workflows.reports import publish_lineage, publish_audit
    from kbapp.workflows.export import export_static
    value = dict(status='running', version=store.state('active_version'), updated=now())
    store.set_state('export_sync',value)
    publish_manifest(store.paths)
    try:
        publish_notes(store,[p for p in store.papers().values() if p.get('kind')=='library'])
        publish_lineage(store)
        publish_audit(store)
        export_static(store)
        value.update(status='synced',updated=now())
    except Exception as exc:
        value.update(status='failed',error=str(exc)[:1000],updated=now())
        store.set_state('export_sync',value)
        publish_manifest(store.paths)
        if strict:
            raise
        return value
    store.set_state('export_sync',value)
    publish_manifest(store.paths)
    return value
