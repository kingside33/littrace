"""上游追溯引擎的任务作用域表;幂等建表,不动 store.py 与 kbapp/storage/schema.py。

四张表全部按任务 id 各记各的:任务行快照启动时的层数,节点/边/队列是「本任务
看到了什么、沿哪条路径、哪些分支为什么停」;文献记录(papers 表)与 OpenAlex
原始响应缓存(<cache>/references/openalex/<wid>.json)跨任务共享,不在这里。

frontier(kbapp/storage/store.py:36-38)是全库共享的两层展开队列表,主键
(wid,direction) 没有任务作用域;本模块不读不写它——旧两层队列的 complete 与
追溯任务是否完毕无关,这一隔离由 tests/test_trace.py 锚定。
"""

DDL = """
CREATE TABLE IF NOT EXISTS trace_tasks(id TEXT PRIMARY KEY,seed_paper_id TEXT,seed_wid TEXT,seed_label TEXT,
  depth INTEGER,unlimited INTEGER DEFAULT 0,status TEXT,requests INTEGER DEFAULT 0,pid INTEGER,
  error TEXT DEFAULT '',created TEXT,updated TEXT);
CREATE TABLE IF NOT EXISTS trace_nodes(task_id TEXT,wid TEXT,paper_id TEXT,min_depth INTEGER,
  refs_state TEXT DEFAULT '',status TEXT DEFAULT 'pending',stop_reason TEXT DEFAULT '',error TEXT DEFAULT '',
  attempts INTEGER DEFAULT 0,updated TEXT,PRIMARY KEY(task_id,wid));
CREATE TABLE IF NOT EXISTS trace_edges(task_id TEXT,src_wid TEXT,dst_wid TEXT,depth INTEGER,updated TEXT,
  PRIMARY KEY(task_id,src_wid,dst_wid));
CREATE TABLE IF NOT EXISTS trace_queue(task_id TEXT,wid TEXT,depth INTEGER,cursor TEXT DEFAULT '*',
  pending TEXT DEFAULT '[]',status TEXT DEFAULT 'pending',error TEXT DEFAULT '',updated TEXT,
  PRIMARY KEY(task_id,wid));
CREATE INDEX IF NOT EXISTS trace_queue_next ON trace_queue(task_id,status,depth,updated);
"""


def ensure_schema(store):
    """幂等建表;引擎每个入口先调用,代价是一次 IF NOT EXISTS 探测。"""
    with store.db() as db:
        db.executescript(DDL)
