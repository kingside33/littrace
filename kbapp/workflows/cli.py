"""统一命令路由；帮助、迁移计划及 dry-run 不创建业务存储。"""
import argparse
import json
import os
import sys
from kbapp.core.paths import Paths

def parser():
    p = argparse.ArgumentParser(description='本地文献知识库')
    p.add_argument('--base', help='文献系统根目录；默认 KB_BASE 或仓库根目录')
    p.add_argument('--data-dir', help='运行数据目录；默认 KB_DATA_DIR 或 <base>/_index')
    commands = p.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve', help='启动本地工作台')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--no-background', action='store_true', help='关闭调度、参考文献采集和研究后台任务')
    update = commands.add_parser('update', help='完整增量更新')
    update.add_argument('--local-only', action='store_true')
    update.add_argument('--full', action='store_true')
    update.add_argument('--bootstrap', action='store_true')
    update.add_argument('--embed-limit', type=int)
    update.add_argument('--dry-run', action='store_true')
    index = commands.add_parser('index', help='仅补齐向量，不采集全文或运行 OCR')
    index.add_argument('--resume', metavar='VERSION', help='续算指定未发布版本')
    search = commands.add_parser('search', help='证据检索')
    search.add_argument('query')
    search.add_argument('-k', type=int, default=12)
    search.add_argument('--bm25-only', action='store_true')
    search.add_argument('--json', action='store_true')
    for field in ('kind', 'direction', 'year', 'paper', 'section', 'collection', 'tag', 'document'):
        search.add_argument('--' + field, default='')
    search.add_argument('--modality', choices=['text', 'figures', 'formulas', 'all'], help='模态筛选')
    search.add_argument('--project', action='store_true')
    research = commands.add_parser('research', help='研究任务与核验的创建、查看和续跑')
    research.add_argument('action', choices=['run', 'status', 'stop', 'resume', 'repair'])
    research.add_argument('--id')
    research.add_argument('--apply', action='store_true', help='应用定向修复；默认仅预览')
    research.add_argument('--subject', help='仅修复指定文献或候选对，供小样本验收')
    research.add_argument('--limit', type=int, help='本次追加修复对象的上限')
    research.add_argument('--kind', default='search', choices=['search', 'answer', 'compare', 'maintenance', 'lineage', 'verification'])
    research.add_argument('--query', default='')
    research.add_argument('--paper', action='append', default=[])
    research.add_argument('--direction', default='')
    research.add_argument('--project', action='store_true')
    trace = commands.add_parser('trace', help='上游引用追溯任务（按设置页层数，沿引用逐层向上）')
    trace.add_argument('action', choices=['run', 'status', 'pause', 'resume', 'paths'])
    trace.add_argument('--id', help='追溯任务 ID；status/paths 省略时列最近 30 个任务')
    trace.add_argument('--paper', help='run：种子文献（文献 ID/citekey/OpenAlex WID/DOI）；paths：过滤单篇')
    trace.add_argument('--limit', type=int, default=20, help='paths 每篇最多展示的路径条数')
    export = commands.add_parser('export', help='导出离线页面或同步自动笔记')
    export.add_argument('kind', choices=['offline', 'notes', 'ris'])
    export.add_argument('options', nargs=argparse.REMAINDER)
    data = commands.add_parser('data', help='运行数据迁移和运维工具')
    data.add_argument('action', choices=['plan', 'migrate', 'rollback', 'tool'])
    data.add_argument('options', nargs=argparse.REMAINDER, help='tool 后为工具名称和选项')
    doctor = commands.add_parser('doctor', help='状态、架构及回归验证')
    doctor.add_argument('action', choices=['status', 'architecture', 'tests', 'integrity', 'relations', 'retrieval', 'consistency', 'attachments'], default='status', nargs='?')
    doctor.add_argument('options', nargs=argparse.REMAINDER)
    backup = commands.add_parser('backup', help='创建 SQLite 一致性备份')
    backup.add_argument('--keep', type=int, default=7, help='保留最近 N 份自动备份')
    backup.add_argument('--label', default='manual', help='备份标签，例如 before-upgrade')
    return p

def output(value):
    print(json.dumps(value, ensure_ascii=False, indent=2, default=str))

def trace_command(store, args):
    """上游追溯子命令的五个动词；层数只来自设置页（任务行快照启动时层数），CLI 不传。

    run 同步推进一轮直至 完成/暂停/预算耗尽/缺 key；ValueError 统一冒泡，
    由 main 的 trace 分支收敛为一句中文与退出码 1。
    """
    from kbapp.workflows.trace import handle_list, handle_pause, handle_resume, handle_start, handle_status
    if args.action == 'run':
        if not args.paper:
            raise ValueError('trace run 需要 --paper 指定种子文献（文献 ID、citekey、OpenAlex WID 或 DOI）')
        return handle_start(store, args.paper)
    if args.action in {'pause', 'resume'} and not args.id:
        raise ValueError(f'trace {args.action} 需要 --id 指定追溯任务')
    if args.action == 'pause':
        return handle_pause(store, args.id)
    if args.action == 'resume':
        return handle_resume(store, args.id)
    if not args.id:
        # 列表走轻查询（摘要 + 聚合计数），不做逐任务全量快照；--id 仍是全量。
        return handle_list(store)
    snapshot = handle_status(store, args.id)
    if args.action == 'status':
        return snapshot
    return trace_paths_view(store, snapshot, args.paper, args.limit)

def trace_paths_view(store, snapshot, paper, limit):
    """来源路径输出：每篇已发现文献的层数、停止原因与种子→该篇的真实引用路径。

    路径本身由引擎的 DFS 给出（快照 nodes[].paths），这里只做 --paper 过滤与
    --limit 展示截断，不重算。
    """
    if limit < 1:
        raise ValueError('路径展示条数应为正整数')
    nodes = snapshot['nodes']
    if paper:
        target = store.resolve(paper)
        wid = paper.strip().removeprefix('https://openalex.org/')
        node = next((n for n in nodes if n['paper_id'] == target or n['wid'] == wid), None)
        if not node:
            raise ValueError('该文献不在本追溯任务的结果里')
        nodes = [node]
    return {'task_id': snapshot['id'], 'seed': snapshot['seed'], 'depth_display': snapshot['depth_display'],
            'papers': [{'wid': n['wid'], 'paper_id': n['paper_id'], 'title': n['title'], 'min_depth': n['min_depth'],
                        'status': n['status'], 'stop_reason': n['stop_reason'], 'note': n['note'],
                        'paths': n['paths'][:limit], 'paths_total': len(n['paths'])} for n in nodes]}

def main(argv=None):
    if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    args = parser().parse_args(argv)
    paths = Paths.from_base(args.base, args.data_dir)
    os.environ['KB_BASE'] = str(paths.base)
    os.environ['KB_DATA_DIR'] = str(paths.data)
    if args.command == 'backup':
        from kbapp.workflows.backup import create_backup
        output(create_backup(paths, keep=args.keep, label=args.label))
        return 0
    if args.command == 'data':
        if args.action == 'tool':
            from kbapp.workflows.tools.registry import run_tool
            return run_tool(args.options, paths)
        from kbapp.workflows import data
        result = getattr(data, args.action)(paths)
        output({k: v for k, v in result.items() if k not in {'operations', 'before', 'after'}} | {'operations': [{'source': o['source'], 'target': o['target'], 'status': o['status'], 'files': len(o['files'])} for o in result.get('operations', [])]})
        return 0
    if args.command == 'update' and args.dry_run:
        output({'base': paths.base, 'data': paths.data, 'local_only': args.local_only, 'stages': ['同步元数据', '按质量识别', '切块与图表公式', '补齐索引', '发布版本', '研究核验与导出']})
        return 0
    if args.command == 'doctor' and args.action == 'consistency':
        from kbapp.workflows.consistency import check
        from kbapp.storage.store import Store
        output(check(Store(paths.base, data_dir=paths.data)))
        return 0
    if args.command == 'doctor' and args.action in {'architecture', 'tests', 'integrity', 'relations', 'retrieval', 'attachments'}:
        from kbapp.workflows.diagnostics import run
        return run(args.action, args.options, paths)
    if args.command=='research' and args.action=='repair' and not args.apply:
        import sqlite3
        from contextlib import closing
        from kbapp.workflows.lineage_repair import preview
        from collections import Counter
        with closing(sqlite3.connect(paths.database.as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            actions=preview(db,args.id)
            if args.subject:
                actions=[a for a in actions if a['subject']==args.subject]
            output({'run_id':args.id,'total':len(actions),'pending':dict(Counter(a['kind'] for a in actions)),'applied':False})
        return 0
    from kbapp.workflows.context import AppContext
    context = AppContext.create(paths.base, paths.data, background_jobs=not getattr(args, 'no_background', False))
    store = context.store
    if args.command == 'serve':
        import uvicorn
        from kbapp.web.service import create_app
        uvicorn.run(create_app(context), host='127.0.0.1', port=args.port, access_log=False)
    elif args.command == 'update':
        from kbapp.workflows.update import run_update
        output(run_update(store, bootstrap=args.bootstrap, embed_limit=args.embed_limit, local_only=args.local_only, full=args.full))
    elif args.command == 'index':
        if args.resume:
            from kbapp.workflows.resume import resume
            output(resume(args.resume, store=store))
        else:
            from kbapp.workflows.vector_jobs import queue_vectors, run_vectors
            job = queue_vectors(store)
            output(run_vectors(store, job['id']) if job.get('state') == 'started' else job)
    elif args.command == 'search':
        from kbapp.integrations.models import set_cancelled
        from kbapp.workflows.references import attach_citations
        if not args.bm25_only:
            set_cancelled(store, ['embedding'], False)
        filters = {field: getattr(args, field) for field in ('kind', 'direction', 'year', 'paper', 'section', 'collection', 'tag', 'document', 'project')}
        if args.modality and args.modality != 'all':
            filters['kind'] = {'text': 'paper_text', 'figures': 'figure', 'formulas': 'formula'}[args.modality]
        result = attach_citations(store, context.search.search(args.query, k=min(max(args.k, 1), 50), vectors=not args.bm25_only, wait_vectors=not args.bm25_only, **filters))
        if args.json:
            output(result)
        else:
            print(f"{result.get('mode', '')} · {len(result['results'])} 条证据")
            for item in result['results']:
                print(f"\n{item.get('title', '')} · {item.get('url', '')}\n{item.get('text', '')}")
    elif args.command == 'research':
        from kbapp.workflows.agents import create_run, get_run, list_runs, control_run, step
        if args.action == 'repair':
            from kbapp.workflows.lineage_repair import repair
            output(repair(store,args.id,apply=args.apply,limit=args.limit,subject=args.subject))
        elif args.action == 'status':
            output(get_run(store, args.id) if args.id else list_runs(store))
        elif args.kind == 'verification' and args.action == 'run':
            from kbapp.workflows.verification import run_verification
            from kbapp.core.io import digest, now
            job = digest(now())[:20]
            with store.db() as db:
                db.execute('INSERT INTO jobs VALUES(?,?,?,?,?,?,?,?,?)', (job, 'verification', 'queued', os.getpid(), '{}', '准备核验', now(), None, None))
            run_verification(store, job, {'direction': args.direction, 'project': args.project})
        elif args.action == 'stop':
            output(control_run(store, args.id, 'stop'))
        else:
            if args.action == 'resume':
                result = control_run(store, args.id, 'resume')
                rid = args.id
                import time
                while get_run(store, rid, False)['status'] in {'queued', 'running', 'stopping'}:
                    time.sleep(0.2)
            else:
                result = create_run(store, {'kind': args.kind, 'query': args.query, 'paper_ids': args.paper, 'filters': {'direction': args.direction, 'project': args.project}}, start=False)
                rid = result['id']
                from kbapp.core.locks import update_lock
                with update_lock(store.root, 'workbench-agents.lock'):
                    while get_run(store, rid, False)['status'] in {'queued', 'running'}:
                        step(store, rid)
            output(get_run(store, rid))
    elif args.command == 'trace':
        try:
            output(trace_command(store, args))
        except ValueError as exc:
            output({'error': str(exc)})
            return 1
    elif args.command == 'export':
        if args.kind == 'offline':
            from kbapp.workflows.publication import sync_exports
            state = sync_exports(store,strict=True)
            output({'file':paths.exports/'evolution.html','export_sync':state})
        elif args.kind == 'notes':
            from kbapp.workflows.notes import publish_notes
            publish_notes(store, [p for p in store.papers().values() if p.get('kind') == 'library'])
        else:
            from kbapp.workflows.tools.registry import run_tool
            return run_tool(['gen_ris', *args.options], paths)
    else:
        from kbapp.workflows.status import status
        output(status(store))
    return 0
