"""kbapp.workflows.reports：由职责边界组织的知识库能力。"""
from kbapp.workflows.status import status as workbench_status
from kbapp.core.markers import AUTO_START, AUTO_END
from kbapp.library.sources import managed
from kbapp.core.config import server_base
from kbapp.core.io import atomic, now
from kbapp.research.graph import chains

def publish_lineage(store):
    from kbapp.research.graph import chains_page
    root = store.base / '_知识库/50_谱系'
    root.mkdir(parents=True, exist_ok=True)
    for path in root.glob('*.md'):
        old = path.read_text(encoding='utf-8')
        if AUTO_START not in old:
            old = '<details>\n<summary>迁移前的引用路径与原有笔记（完整保留）</summary>\n\n' + old + '\n\n</details>\n'
        direction = '' if path.stem == '00_总览' else path.stem[:2]
        report = chains_page(store, direction=direction, limit=100)
        coverage = report['coverage']
        lines = [f'# {path.stem} · 文献关系与方法大类', '',
                 f'[打开完整交互谱系]({server_base(store.settings()["port"])}/#graph)', '',
                 f"全库 {coverage['library_papers']} 篇；当前方向及关联文献 {coverage['papers']} 篇，"
                 f"已有关系 {coverage['edges']} 条（引用 {coverage['citations']}、方法 {coverage['method_edges']}、其他 {coverage['other_edges']}）。", '',
                 f"当前范围 {coverage['unlinked']} 篇尚未建立关系，仍保留在对应大类文献列表。", '',
                 '按稳定中文大类归并，中英文名称、算法变体和策略组件保留在论文原始属性中。每条关系只归入一个大类；跨类别关系保留两端文献。', '',
                 '| 方法大类 | 文献数（含关联端点） | 引用事实 | 方法关系 | 其他关系 | 已核方法 | 待核关系 |',
                 '|---|---:|---:|---:|---:|---:|---:|']
        for branch in report['items']:
            lines.append(f"| {branch['name']} | {branch['nodes']} | {branch['citations']} | {branch['method_edges']} | {branch['other_edges']} | {branch['confirmed']} | {branch['pending']} |")
        lines.extend(['', '各大类可能共享跨类关系的端点文献；顶部文献数按ID去重。引用事实与方法继承分别标注，题名归类不产生已核关系。完整文献、逐条关系与证据在交互谱系中分页查看。'])
        details = []
        for branch in chains(store):
            nodes = branch.get('nodes') or []
            if direction and not any(direction in (node.get('directions') or []) for node in nodes):
                continue
            edges = branch.get('edges') or []
            if not edges:
                continue
            details.extend(['', f"## {branch.get('name') or '未命名方法大类'} · 关系明细"])
            for edge in edges:
                details.extend(['', f"- 方法关系：{edge.get('explanation') or '（尚无论述）'}"])
                for key, label in (('previous_problem', '先前问题'), ('change', '具体改变'), ('conditions', '适用条件')):
                    if edge.get(key):
                        details.append(f"  - {label}：{edge[key]}")
        lines.extend(details)
        block = '\n'.join(lines)
        atomic(path, managed(old, block) if AUTO_START in old else AUTO_START + '\n' + block + '\n' + AUTO_END + '\n\n' + old)

def publish_audit(store):
    status = workbench_status(store)
    coverage = status.get('coverage') or {}
    index = store.active_dir()
    health = status.get('index_health') or {}
    compatible = health.get('embedding_compatible', False)
    block = f"## {now()} 当前运行状态\n\n最近发布：{status['updated']}；版本 `{status['active_version']}`。\n\n库内文献 {status['papers']} 篇，库外元数据节点 {status['external']} 个；全文来源覆盖 {coverage.get('fulltext_papers', 0)} 篇。\n\n当前来源 {health.get('sources', coverage.get('sources', 0))} 个；已保存向量 {health.get('vector_count', 0)} 个，当前权重可用向量 {health.get('usable_vector_count', 0)} 个。" + ('模型权重与索引一致。' if compatible else '旧向量保留；当前权重尚未完成兼容索引，使用关键词检索。') + '\n\n' + '当前有效数据目录：`' + str(index) + '`；来源文件与向量矩阵继续沿用已发布版本。\n\n' + '数据库迁移和真实样本验证见 `evaluation/integrity-upgrade/report.md` 与 `migration.json`；具体测试以记录中的实际结果为准。\n\n' + f"自动启动安装状态：{('已安装' if status['schedule'].get('enabled') else '未安装')}。" + '研究关系、人工复核与模型任务状态见工作台；历史公式不视作人工确认。\n\n' + '\n'.join(('- ' + str(x) for x in coverage.get('warnings', [])))
    audit = store.paths.exports / 'audit.md'
    old = audit.read_text(encoding='utf-8') if audit.exists() else ''
    start = '<!-- KB:AUDIT:START -->'
    end = '<!-- KB:AUDIT:END -->'
    section = start + '\n' + block + '\n' + end
    if start in old and end in old:
        a = old.index(start)
        b = old.index(end, a) + len(end)
        new = old[:a] + section + old[b:]
    else:
        new = section + '\n\n## 迁移前审计快照（历史记录）\n\n' + old
    atomic(audit, new)
