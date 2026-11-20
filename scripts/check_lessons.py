"""错题本(docs/lessons.md)的格式、编号与隐私校验,以及到期周报。

只依赖标准库。格式与规矩的全文见 CONTRIBUTING.md 的「Lessons(错题本)」一节。
`--report` 由 .github/workflows/lessons-review.yml 每周调用一次,输出可直接
作为 issue 正文的 Markdown;它只报告,不修改文件,也不阻塞任何 PR。
"""

import argparse
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MAX_BYTES = 16 * 1024
# 同一条目在这么多个不同日期出现后,升级为 AGENTS.md / CONTRIBUTING.md / ADR 里的硬规则。
PROMOTE_AFTER = 3
# 未升级条目最近一次出现超过这么多天,进入删除候选。
STALE_DAYS = 60
CATEGORIES = {'别这样', '照这样'}
FIELDS = ('类', 'Seen', '为什么', '证据')
BEIJING = timezone(timedelta(hours=8))
REPO_ROOT = Path(__file__).resolve().parents[1]

ENTRY = re.compile(r'^### (L-\d{3,}) (.+)$')
FIELD = re.compile(r'^- (类|Seen|为什么|证据):\s*(.*)$')
PROMOTED = re.compile(r'^(.*?)\s+→\s+(\S+\.md)$')
ISSUE = re.compile(r'^#\d+$')
ISO_DATE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
# 任何「像条目」的标题都要走 ENTRY 校验,否则畸形标题会连同条目一起被静默忽略。
HEADING = re.compile(r'^\s*#{1,6}\s*[Ll]-\d')
# 仓库自己的 pluginID 形似邮箱,但它是公开标识,不是个人信息。
ALLOWED_EMAILS = {'littrace@kingside33.github.io'}
PRIVACY = (
    (re.compile(r'[A-Za-z]:\\'), '本机绝对路径'),
    (re.compile(r'/Users/'), '本机用户目录路径'),
    (re.compile(r'/home/'), '本机用户目录路径'),
    (re.compile(r'~/'), '本机用户目录路径'),
    (re.compile(r'[\w.+-]+@[\w-]+\.[\w.-]+'), '邮箱地址'),
    (re.compile(r'\b(?:f[cd][0-9a-f]{2}|fe80)::', re.IGNORECASE), 'IPv6 私网地址'),
    (re.compile(r'\b(?:10|127)\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'), '私网 IP'),
    (re.compile(r'\b192\.168\.\d{1,3}\.\d{1,3}\b'), '私网 IP'),
    (re.compile(r'\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b'), '私网 IP'),
    (re.compile(r'\b169\.254\.\d{1,3}\.\d{1,3}\b'), '私网 IP'),
    (re.compile(r'(?i)\b(?:api[_-]?key|secret|password|passwd|token)\s*[:=]\s*\S+'), '凭据'),
    (re.compile(r'docs/zotero-plugin-'), '本机排除的规划文档'),
)


def beijing_today():
    return datetime.now(BEIJING).date()


def split_seen(value, number, errors):
    parts = [part.strip() for part in value.split(',')]
    if not parts or any(not part for part in parts):
        errors.append((number, 'Seen 不能为空,也不能有空的日期项'))
        return []
    dates = []
    for part in parts:
        if not ISO_DATE.match(part):
            errors.append((number, f'Seen 日期格式应为 YYYY-MM-DD:{part!r}'))
            continue
        try:
            dates.append(date.fromisoformat(part))
        except ValueError:
            errors.append((number, f'Seen 不是合法日期:{part!r}'))
    if len(set(dates)) != len(dates):
        errors.append((number, 'Seen 日期不能重复'))
    if dates != sorted(dates):
        errors.append((number, 'Seen 日期必须升序'))
    today = beijing_today()
    for day in dates:
        if day > today:
            errors.append((number, f'Seen 日期 {day} 晚于今天 {today}(北京时区)'))
    return dates


def check_privacy(lines, errors):
    for number, line in enumerate(lines, start=1):
        for pattern, label in PRIVACY:
            matches = [match.group(0) for match in pattern.finditer(line)]
            if label == '邮箱地址':
                matches = [match for match in matches if match not in ALLOWED_EMAILS]
            if matches:
                errors.append((number, f'疑似{label},公开仓库不得写入:{matches[0]!r}'))


def finalize(entry, root, errors):
    number = entry['line']
    fields = entry['fields']

    if entry['promoted'] is not None:
        title, target = entry['promoted']
        if not title:
            errors.append((number, f'{entry["id"]} 指针条目缺少标题'))
        if fields:
            errors.append((number, f'{entry["id"]} 已升级,不应再有字段'))
        if entry['unknown']:
            for line in entry['unknown']:
                errors.append((line, f'{entry["id"]} 有无法解析的行;续行必须缩进'))
        path = root / target
        if not path.is_file():
            errors.append((number, f'{entry["id"]} 指针目标不存在:{target}'))
        elif not re.search(rf'\b{re.escape(entry["id"])}\b', path.read_text(encoding='utf-8')):
            errors.append((number, f'{entry["id"]} 指针目标 {target} 里没有引用该编号'))
        return

    for name in FIELDS:
        if name not in fields:
            errors.append((number, f'{entry["id"]} 缺少字段「{name}」'))
    if entry['duplicate']:
        for name in entry['duplicate']:
            errors.append((number, f'{entry["id"]} 字段「{name}」重复'))
    if entry['order'] and entry['order'] != [name for name in FIELDS if name in entry['order']]:
        errors.append((number, f'{entry["id"]} 字段顺序应为 {" / ".join(FIELDS)}'))
    if entry['unknown']:
        for line in entry['unknown']:
            errors.append((line, f'{entry["id"]} 有无法解析的行;续行必须缩进'))

    title = entry['title']
    if len(title) > 60:
        errors.append((number, f'{entry["id"]} 标题 {len(title)} 字符,超过 60 字符上限'))
    if not title.strip():
        errors.append((number, f'{entry["id"]} 标题不能为空'))

    category = fields.get('类')
    if category is not None and category not in CATEGORIES:
        errors.append((number, f'{entry["id"]} 类只能是「别这样」或「照这样」:{category!r}'))

    seen = fields.get('Seen')
    if seen is not None:
        entry['seen'] = split_seen(seen, number, errors)

    why = fields.get('为什么')
    if why is not None and not why.strip():
        errors.append((number, f'{entry["id"]} 为什么不能为空'))

    evidence = fields.get('证据')
    if evidence is not None:
        if not evidence.strip():
            errors.append((number, f'{entry["id"]} 证据不能为空'))
        for part in (item.strip() for item in evidence.split(',')):
            if not ISSUE.match(part):
                errors.append((number, f'{entry["id"]} 证据应为 issue 或 PR 编号(#N):{part!r}'))


def parse(path, root):
    errors = []
    payload = path.read_bytes()
    size = len(payload)
    if size > MAX_BYTES:
        errors.append((0, f'文件 {size} 字节,超过 {MAX_BYTES} 字节上限'))
    try:
        text = payload.decode('utf-8')
    except UnicodeDecodeError as exc:
        errors.append((0, f'文件不是合法 UTF-8:{exc}'))
        return [], errors
    lines = text.splitlines()
    check_privacy(lines, errors)

    entries = []
    current = None
    in_fence = False
    fence_line = 0
    for number, raw in enumerate(lines, start=1):
        if raw.strip().startswith('```'):
            if in_fence:
                in_fence = False
            else:
                in_fence = True
                fence_line = number
            continue
        if in_fence:
            continue
        if HEADING.match(raw):
            if current is not None:
                finalize(current, root, errors)
                entries.append(current)
                current = None
            match = ENTRY.match(raw)
            if not match:
                errors.append(
                    (
                        number,
                        '条目标题必须写成 `### L-001 <标题>`:'
                        '三级标题、大写 L、至少三位数字、标题非空',
                    )
                )
                continue
            entry_id, rest = match.groups()
            promoted = None
            promoted_match = PROMOTED.match(rest)
            if promoted_match:
                promoted = (promoted_match.group(1).strip(), promoted_match.group(2))
            current = {
                'id': entry_id,
                'title': rest,
                'line': number,
                'fields': {},
                'order': [],
                'duplicate': [],
                'unknown': [],
                'promoted': promoted,
                'seen': [],
            }
            continue
        if raw.startswith('#'):
            if current is not None:
                finalize(current, root, errors)
                entries.append(current)
                current = None
            continue
        if current is None:
            continue
        match = FIELD.match(raw)
        if match:
            name, value = match.groups()
            if name in current['fields']:
                current['duplicate'].append(name)
            else:
                current['fields'][name] = value
                current['order'].append(name)
            continue
        if not raw.strip():
            continue
        if raw[:1].isspace():
            last = current['order'][-1] if current['order'] else None
            if last is None:
                current['unknown'].append(number)
            else:
                current['fields'][last] += '\n' + raw.strip()
            continue
        current['unknown'].append(number)

    if in_fence:
        errors.append((fence_line, '代码围栏未闭合,其后的条目不会被校验'))

    if current is not None:
        finalize(current, root, errors)
        entries.append(current)

    previous = None
    for entry in entries:
        value = int(entry['id'].split('-')[1])
        if previous is not None and value <= previous:
            errors.append((entry['line'], f'{entry["id"]} 编号必须严格升序'))
        previous = value

    return entries, errors


def load(path, root):
    entries, errors = parse(path, root)
    return entries, errors


def is_due(entry):
    """升级条件:未升级,且 Seen 达到 PROMOTE_AFTER 个不同日期。"""
    return not entry['promoted'] and len(entry['seen']) >= PROMOTE_AFTER


def is_stale(entry, today):
    """删除候选:未升级,且最近一次出现已超过 STALE_DAYS 天。"""
    if entry['promoted'] or not entry['seen']:
        return False
    return today - max(entry['seen']) > timedelta(days=STALE_DAYS)


def table_cell(value):
    """把多行字段压成一行并转义竖线,否则会撑破 Markdown 表格。"""
    return ' '.join(str(value).split()).replace('|', '\\|')


def build_report(entries, today, size):
    """生成周报 Markdown。纯函数:日期与文件体积由调用方给出。"""
    due = [entry for entry in entries if is_due(entry)]
    stale = [entry for entry in entries if is_stale(entry, today)]
    next_number = max((int(entry['id'].split('-')[1]) for entry in entries), default=0) + 1
    lines = [
        '<!-- generated by scripts/check_lessons.py --report -->',
        '',
        '# 错题本周报',
        '',
        f'- 日期:{today}(北京时区)',
        (
            f'- 文件:`docs/lessons.md`,{len(entries)} 条,'
            f'{size} / {MAX_BYTES} 字节,下一个编号 L-{next_number:03d}'
        ),
        '',
    ]

    if not due and not stale:
        lines += [
            '<!-- no-lessons-due -->',
            '',
            '没有到期条目:没有条目达到 3 个不同日期,也没有条目 60 天未复发。',
            '',
            '下一轮周报会自动重算;这个 issue 可以关闭。',
            '',
        ]
        return '\n'.join(lines)

    lines += ['## 到期升级:Seen 达到 3 个不同日期', '']
    if due:
        lines += ['| 编号 | 标题 | Seen | 证据 |', '| --- | --- | --- | --- |']
        for entry in due:
            lines.append(
                f'| {entry["id"]} | {table_cell(entry["title"])} '
                f'| {len(entry["seen"])} | {table_cell(entry["fields"]["证据"])} |'
            )
        lines += [
            '',
            '把规则写进 `AGENTS.md`、`CONTRIBUTING.md` 或新 ADR(规则 + 原因 + 守护用例),',
            '再把条目压成一行指针 `### L-001 <标题> → <目标文件>`;目标文件必须引用该编号。',
            '',
        ]
    else:
        lines += ['无。', '']

    lines += ['## 可删除候选:60 天未复发', '']
    if stale:
        lines += ['| 编号 | 标题 | 最近 Seen | 证据 |', '| --- | --- | --- | --- |']
        for entry in stale:
            lines.append(
                f'| {entry["id"]} | {table_cell(entry["title"])} '
                f'| {max(entry["seen"])} | {table_cell(entry["fields"]["证据"])} |'
            )
        lines += [
            '',
            '删除前确认:所指代码已不存在,或已有门禁挡住,或硬规则已覆盖。',
            '删除条目并把理由写进 `docs(lessons)` PR 正文;原文留在 git 历史。',
            '',
        ]
    else:
        lines += ['无。', '']

    lines += [
        '## 下一步',
        '',
        '1. 开一个独立的 `docs(lessons)` PR,逐条升级或删除;',
        '2. 条目处理完、周报为空后,下一轮会自动关闭这个 issue。',
        '',
    ]
    return '\n'.join(lines)


def report(path, errors):
    for number, message in errors:
        print(f'{path}:{number}: {message}', file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(description='校验错题本的格式、编号与隐私')
    parser.add_argument('--dir', help='包含 lessons.md 的目录,默认 docs/')
    parser.add_argument('--root', help='仓库根,用于解析指针目标')
    parser.add_argument('--due', action='store_true', help='列出 Seen 达到 3 个日期的条目')
    parser.add_argument('--stats', action='store_true', help='输出体积与下一个编号')
    parser.add_argument('--json', action='store_true', help='以 JSON 输出条目')
    parser.add_argument('--report', action='store_true', help='输出周报 Markdown,供定时工作流开 issue')
    parser.add_argument('--today', help='按指定日期计算到期(YYYY-MM-DD),默认北京今天')
    args = parser.parse_args(argv)

    today = beijing_today()
    if args.today:
        try:
            today = date.fromisoformat(args.today)
        except ValueError:
            print(f'--today 不是合法日期:{args.today!r}', file=sys.stderr)
            return 2

    directory = Path(args.dir) if args.dir else REPO_ROOT / 'docs'
    root = Path(args.root) if args.root else (directory.parent if args.dir else REPO_ROOT)
    path = directory / 'lessons.md'
    if not path.is_file():
        print(f'{path}: 文件不存在', file=sys.stderr)
        return 1

    entries, errors = load(path, root)
    if errors:
        report(path, errors)
        return 1

    if args.report:
        size = len(path.read_text(encoding='utf-8').encode('utf-8'))
        print(build_report(entries, today, size))
        return 0

    if args.stats:
        size = len(path.read_text(encoding='utf-8').encode('utf-8'))
        next_number = max((int(entry['id'].split('-')[1]) for entry in entries), default=0) + 1
        print(f'文件 {size} 字节,剩余 {MAX_BYTES - size} 字节')
        print(f'下一个编号 L-{next_number:03d}')
        return 0

    if args.due:
        for entry in entries:
            if is_due(entry):
                print(f'{entry["id"]}\t{len(entry["seen"])}\t{entry["title"]}')
        return 0

    if args.json:
        records = [
            {
                'id': entry['id'],
                'title': entry['title'],
                'category': entry['fields'].get('类'),
                'seen': entry['fields'].get('Seen'),
                'why': entry['fields'].get('为什么'),
                'evidence': entry['fields'].get('证据'),
                'promoted': entry['promoted'][1] if entry['promoted'] else None,
                'line': entry['line'],
            }
            for entry in entries
        ]
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return 0

    print(f'{path}: {len(entries)} 条条目,格式校验通过')
    return 0


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    sys.exit(main())
