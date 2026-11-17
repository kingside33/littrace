from __future__ import annotations
from kbapp.workflows.status import status as workbench_status
import re
from urllib.parse import quote
from kbapp.core.io import atomic, now
from kbapp.core.markers import AUTO_END, AUTO_START, PROJECT, STAGES
from kbapp.library.sources import managed, paper_url, repair_links
from kbapp.core.config import server_base

def publish_notes(store, papers):
    from kbapp.integrations.scheduling import file_slot
    with file_slot(store.root, 'workbench-note-sync.lock'):
        return _publish_notes(store, papers)

def guarded_metadata(store, paper_id, old, block):
    """接管旧自动入口时保留未知差异；后续仅更新未被人工修改的版本。"""
    key = paper_id + '::metadata'
    proposed = managed('', block).strip()
    match = re.search(re.escape(AUTO_START) + '.*?' + re.escape(AUTO_END), old, re.S)
    current = match.group(0) if match else ''
    with store.db() as db:
        row = db.execute('SELECT * FROM note_sync WHERE paper_id=?', (key,)).fetchone()
    base = row['base_text'] if row else ''
    malformed = not match and (AUTO_START in old or AUTO_END in old)
    conflict = malformed or (current != proposed and (row and current != base or (not row and bool(current))))
    content = old if conflict else managed(old, block)
    with store.db() as db:
        db.execute('INSERT OR REPLACE INTO note_sync VALUES(?,?,?,?,?)', (key, base if conflict else proposed, proposed, 'conflict' if conflict else 'synced', now()))
    return content

def _publish_notes(store, papers):
    import yaml
    vault = store.base / '_知识库'
    state = store.state('card_fields', {})
    for p in papers:
        path = store.base / p['card']
        if not path.resolve().is_relative_to(vault.resolve()):
            continue
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        initial = old
        template = vault / '笔记模板/文献卡片模板.md'
        if not initial and template.is_file():
            import kbapp.library.cards as cards
            rec = dict(p, tagfields=p.get('tagfields') or {}, metadata_source=p.get('metadata_source', 'zotero'), authors=p.get('authors') or [])
            initial = cards.render(cards.load_template(str(template)), cards.build_ctx(rec))
        fm = {}
        body = initial
        m = re.match('^---\\s*\\n(.*?)\\n---\\s*\\n', initial, re.S)
        if m:
            try:
                fm = yaml.safe_load(m.group(1)) or {}
            except yaml.YAMLError:
                fm = None
            body = initial[m.end():] if fm is not None else initial
        fresh = {k: p.get(k) for k in ('title', 'authors', 'year', 'doi', 'venue', 'zotero_key', 'pdf')}
        fresh.update(type='paper', paper_id=p['id'], citekey=p['citekey'], project=p.get('project', ''), project_stages=p.get('project_stages', []), source_status=p.get('source_status', 'active'))
        if fm is not None:
            prior = state.get(p['id'], {})
            for k, v in fresh.items():
                if k not in fm or (k in prior and fm[k] == prior[k]):
                    fm[k] = v
            if not old:
                fm['directions'] = [f"[[20_方向/{__import__('kbapp.library.cards', fromlist=['*']).DIR_NAME[d]}]]" for d in p['directions']]
                if not initial:
                    body = f"# {p['title']}\n\n## 我的笔记\n\n"
            content = '---\n' + yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).rstrip() + '\n---\n' + body
            state[p['id']] = fresh
        else:
            content = old
        block = f"## 自动文献入口\n\n[阅读原文]({paper_url(p)}) · [演化与证据]({server_base(store.settings()['port'])}/?paper={quote(p['id'], safe='')})\n\n课题阶段：{' → '.join(p.get('project_stages', [])) or '长期文献积累'}\n\n最新元数据：{p.get('year') or '年份待核'} · {p.get('title', '')}\n\n摘要：{p.get('abstract') or '暂无摘要'}"
        content = repair_links(guarded_metadata(store, p['id'], content, block), p)
        if content != old and (not path.exists() or path.read_text(encoding='utf-8') == old):
            atomic(path, content)
    store.set_state('card_fields', state)
    for path in vault.rglob('*.md'):
        if '.obsidian' in path.parts or '笔记模板' in path.parts:
            continue
        old = path.read_text(encoding='utf-8')
        fixed = repair_links(old)
        if path.parent.name == '40_学者':
            fixed = fixed.replace('contains(authors, this.file.name)', 'contains(authors, this.aliases[0])')
            fixed = fixed.replace('a != this.file.name', 'a != this.aliases[0]')
        if fixed != old:
            atomic(path, fixed)
    status = workbench_status(store)
    block = f"""# 更新与审批\n\n最近成功发布：{status['updated'] or '首次构建中'}\n\n文献 {status['papers']} 篇 · 库外节点 {status['external']} 个 · 待审批全文 {status['pending_approvals']} 篇\n\n[打开研究工作台]({server_base(store.settings()['port'])}/) · [全文审批]({server_base(store.settings()['port'])}/#approvals)\n\n自动更新：{('已安装启动任务，每天北京时间 09:00，开机补跑' if status['schedule'].get('enabled') else '尚未安装自动启动任务；可通过网页或命令行手动更新')}。云端调用需先配置费用上限。\n\n全文获取：先收集元数据与摘要，关键论文经批准后获取。\n\n```dataview\nTABLE year AS 年份, project_stages AS 课题阶段\nFROM "10_文献"\nWHERE project\nSORT year DESC\nLIMIT 20\n```\n"""
    stages = ' → '.join((STAGES[d] for d in sorted(STAGES)))
    for rel, content in [('00_首页/04_更新与审批.md', block), ('60_课题/当前课题.md', f'# {PROJECT}\n\n{stages}\n\n长期研究方向和课题阶段分别维护；同一篇论文可以支撑多个阶段。\n\n' + block)]:
        path = vault / rel
        atomic(path, managed(path.read_text(encoding='utf-8') if path.exists() else '', content))
    home = vault / '00_首页/00_总览.md'
    if home.exists():
        atomic(home, managed(home.read_text(encoding='utf-8'), f'## 研究工作台\n\n[[00_首页/04_更新与审批|更新状态与全文审批]] · [[60_课题/当前课题|当前课题]] · [交互图谱与检索]({server_base(store.settings()["port"])}/)'))
