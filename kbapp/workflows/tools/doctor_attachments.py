"""kbapp.workflows.tools.doctor_attachments：整库 PDF 可用性只读检查（doctor attachments）。

零写入诊断：不建目录、不落盘、不触碰 cache/索引/状态文件、不启动后台任务、
不触发同步；Zotero 库以 mode=ro 打开并复制进内存后立即关闭源文件，绝不写回
Zotero。分派链：kb.py doctor attachments → cli.py 早退分支（在 AppContext.create
之前返回）→ diagnostics.run → 本模块 main(options)。

口径：附件状态按文件探测（os.stat 区分缺失与权限不足，不用 os.path.exists）；
「可读取」只表示能打开并读到首块，不宣称内容完整；PDF 判定与
kbapp.library.attachments.classify_attachment 一致（content_type 含 pdf 或文件名
以 .pdf 结尾）。文献类别允许重叠：主 PDF 缺失而副本可读的文献同时计入
pdf_missing 与 pdf_readable，两个事实都保留。
"""
import argparse
import json
import os
import sqlite3
import stat
import sys
from kbapp.core.io import read_json
from kbapp.core.paths import Paths
from kbapp.integrations.zotero import fill_attachments, load_items, ro_connect

PROBE_BLOCK = 65536
STATES = ('readable', 'missing', 'no_permission', 'unreadable', 'not_file')
STATE_LABELS = {'readable': '可读取', 'missing': '路径缺失', 'no_permission': '权限不足',
                'unreadable': '无法读取', 'not_file': '非文件链接'}
CATEGORIES = ('no_pdf', 'pdf_readable', 'pdf_missing', 'pdf_unreadable')
CATEGORY_LABELS = {'no_pdf': '无 PDF 附件', 'pdf_readable': '有可读 PDF',
                   'pdf_missing': '含缺失 PDF', 'pdf_unreadable': '含无法读取 PDF'}
PROBLEM_STATES = ('missing', 'no_permission', 'unreadable')
SOURCE_NOTE = ('数据来源：本地 Zotero 库 zotero.sqlite 只读快照（mode=ro 打开并复制进内存），'
               '反映最近一次同步后的条目与当前本地文件。')


def probe_file(path):
    """只读探测单个文件，返回状态词；不抛常规 OSError。

    不用 os.path.exists 判定：Windows 上权限拒绝时它同样返回 False，会把权限
    不足误报成缺失；这里用 os.stat 区分 FileNotFoundError 与 PermissionError。
    """
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return 'missing'
    except PermissionError:
        return 'no_permission'
    except OSError:
        return 'unreadable'
    if stat.S_ISDIR(st.st_mode):
        return 'unreadable'
    try:
        with open(path, 'rb') as fh:
            fh.read(PROBE_BLOCK)
    except PermissionError:
        return 'no_permission'
    except OSError:
        return 'unreadable'
    return 'readable'


def attachment_state(att):
    """附件状态：raw_path 为空（如 linked_url 网页链接）记 not_file，否则探测文件。

    probe_file 是注入点：测试可替换本模块的 probe_file 令其抛
    PermissionError/OSError，在此映射为 no_permission/unreadable，
    避免依赖 CI 账户的真实权限行为。
    """
    if not att.get('raw_path'):
        return 'not_file'
    try:
        return probe_file(att.get('abs_path'))
    except PermissionError:
        return 'no_permission'
    except OSError:
        return 'unreadable'


def is_pdf_attachment(att):
    """PDF 口径：content_type 含 pdf 或文件名以 .pdf 结尾；无路径的附件不算 PDF。"""
    if not att.get('raw_path'):
        return False
    name = str(att.get('abs_path') or att.get('raw_path') or '').lower()
    content_type = str(att.get('content_type') or '').lower()
    return 'pdf' in content_type or name.endswith('.pdf')


def resolve_zotero_dir(explicit, paths):
    """解析顺序：--zotero-dir → settings.json 的 zotero_dir → 环境变量 KB_ZOTERO_DIR。

    与 kb.py update 同口径：Store.settings()（kbapp/storage/store.py:146-153）把
    settings.json 覆盖到 DEFAULTS 之上，而 DEFAULTS['zotero_dir'] 来自
    KB_ZOTERO_DIR（kbapp/core/config.py:8）——所以 settings.json 赢、环境变量输。
    doctor 必须体检「最近同步」的那个库（kbapp/library/sources.py:108 走的就是
    store.settings()），否则用户同时配置两处时会静默给出与同步无关的结论。
    --zotero-dir 是 doctor 自身的临时覆盖入口，优先级最高。

    这里不构造 Store（那会建库、破坏零写入），只复现同一份合并规则：
    settings.json 提供了该键就以其为准，否则取 DEFAULTS['zotero_dir'] 的来源
    环境变量 KB_ZOTERO_DIR（空串视为未设置）。
    settings.json 读不动时 PermissionError/OSError 原样上抛，由 main 映射为
    退出码 2——绝不静默退回环境变量换成另一个库。
    """
    if explicit:
        return explicit
    data = read_json(paths.state / 'settings.json', {}) or {}
    if 'zotero_dir' in data:
        value = data['zotero_dir']
    else:
        value = os.environ.get('KB_ZOTERO_DIR', '')
    return value if isinstance(value, str) and value else None


def collect_report(zotero_dir, base_dir):
    """只读扫描库与本地文件，返回报告 dict；发现不了 zotero.sqlite 抛 FileNotFoundError，
    库损坏或被占用时由 ro_connect 抛 sqlite3.Error，均由 main 映射为退出码 2。"""
    db_path = os.path.join(zotero_dir, 'zotero.sqlite')
    if not os.path.exists(db_path):
        raise FileNotFoundError(db_path)
    conn = ro_connect(db_path)
    try:
        papers = load_items(conn)
        fill_attachments(conn, papers, base_dir, zotero_dir)
    finally:
        conn.close()
    total = {s: 0 for s in STATES}
    summary = {'items': len(papers), 'attachments': 0, 'pdf_attachments': 0,
               'non_pdf_attachments': 0, 'attachments_by_state': total}
    categories = {c: 0 for c in CATEGORIES}
    libraries = {}
    problems = []
    for paper in papers:
        lib_id = paper.get('library_id')
        lib = libraries.setdefault(str(lib_id), {
            'items': 0, 'attachments': 0, 'pdf_attachments': 0, 'non_pdf_attachments': 0,
            'attachments_by_state': {s: 0 for s in STATES},
            'items_by_category': {c: 0 for c in CATEGORIES}})
        lib['items'] += 1
        pdf_states = []
        for att in paper.get('attachments', []):
            state = attachment_state(att)
            pdf = is_pdf_attachment(att)
            total[state] += 1
            lib['attachments_by_state'][state] += 1
            lib['attachments'] += 1
            summary['attachments'] += 1
            if pdf:
                summary['pdf_attachments'] += 1
                lib['pdf_attachments'] += 1
                pdf_states.append(state)
            else:
                summary['non_pdf_attachments'] += 1
                lib['non_pdf_attachments'] += 1
            if state in PROBLEM_STATES:
                problems.append({'library_id': lib_id, 'item_key': paper.get('key'),
                                 'attachment_key': att.get('key'), 'state': state})
        if not pdf_states:
            categories['no_pdf'] += 1
            lib['items_by_category']['no_pdf'] += 1
        else:
            if 'readable' in pdf_states:
                categories['pdf_readable'] += 1
                lib['items_by_category']['pdf_readable'] += 1
            if 'missing' in pdf_states:
                categories['pdf_missing'] += 1
                lib['items_by_category']['pdf_missing'] += 1
            if 'no_permission' in pdf_states or 'unreadable' in pdf_states:
                categories['pdf_unreadable'] += 1
                lib['items_by_category']['pdf_unreadable'] += 1
    summary['items_by_category'] = categories
    summary['libraries'] = dict(sorted(libraries.items()))
    problems.sort(key=lambda item: (str(item['library_id']), str(item['item_key']),
                                    str(item['attachment_key'])))
    return {'source': 'local_zotero_readonly_snapshot', 'summary': summary, 'problems': problems}


def print_summary(report):
    print(SOURCE_NOTE)
    s = report['summary']
    print(f"文献 {s['items']} 篇；附件 {s['attachments']} 个"
          f"（PDF {s['pdf_attachments']}，非 PDF {s['non_pdf_attachments']}）")
    states = s['attachments_by_state']
    print('附件状态：' + '，'.join(f"{STATE_LABELS[k]} {states[k]}" for k in STATES))
    cats = s['items_by_category']
    print('文献类别（允许重叠）：' + '，'.join(f"{CATEGORY_LABELS[k]} {cats[k]}" for k in CATEGORIES))
    for lib_id, lib in s['libraries'].items():
        print(f"分库 {lib_id}：文献 {lib['items']} 篇，附件 {lib['attachments']} 个"
              f"（PDF {lib['pdf_attachments']}，非 PDF {lib['non_pdf_attachments']}）")
    print(f"问题项 {len(report['problems'])} 条；--problems 仅看问题项，--json 输出稳定 JSON。")


def print_problems(report):
    problems = report['problems']
    print(f"问题项 {len(problems)} 条（路径缺失/权限不足/无法读取的附件）：")
    if not problems:
        print('  （无）')
    for item in problems:
        print(f"  库 {item['library_id']} 文献 {item['item_key']} 附件 "
              f"{item['attachment_key']}：{STATE_LABELS[item['state']]}")


def main(argv=None):
    ap = argparse.ArgumentParser(description='整库 PDF 可用性检查（只读，零写入）')
    ap.add_argument('--zotero-dir', help='Zotero 数据目录；覆盖 settings.json 与 KB_ZOTERO_DIR')
    ap.add_argument('--base', help='文献系统根目录；解析 attachments: 相对路径时使用')
    ap.add_argument('--problems', action='store_true', help='仅列出问题项')
    ap.add_argument('--json', action='store_true', help='输出稳定 JSON（含摘要与问题项）')
    args = ap.parse_args(argv)
    paths = Paths.from_base(args.base)
    try:
        zdir = resolve_zotero_dir(args.zotero_dir, paths)
    except OSError:
        print('无法读取数据目录 state/settings.json，无法确定 Zotero 数据目录。'
              '请确认该文件可读（不是同名目录、未被其它进程占用），'
              '或改用 --zotero-dir / 环境变量 KB_ZOTERO_DIR 指定。', file=sys.stderr)
        return 2
    if not zdir:
        print('未找到 Zotero 数据目录，无法执行检查。请任选其一：', file=sys.stderr)
        print('  1. 运行时加 --zotero-dir <Zotero 数据目录>', file=sys.stderr)
        print('  2. 设置环境变量 KB_ZOTERO_DIR', file=sys.stderr)
        print('  3. 在数据目录 state/settings.json 写入 zotero_dir 键', file=sys.stderr)
        return 2
    try:
        report = collect_report(zdir, str(paths.base))
    except FileNotFoundError:
        print(f"找不到 {os.path.basename(zdir)}/zotero.sqlite（解析到的目录：{zdir}）。"
              '请确认 Zotero 数据目录后再运行。', file=sys.stderr)
        return 2
    except sqlite3.Error:
        print(f"无法读取 {os.path.basename(zdir)}/zotero.sqlite，库文件可能损坏或被占用。"
              '请关闭 Zotero（必要时校验库完整性）后再运行。', file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    elif args.problems:
        print_problems(report)
    else:
        print_summary(report)
    return 0
