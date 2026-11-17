"""kbapp.library.works：论文作品关联层（C01/C02）。

同一作品可以有多个 Zotero 条目/版本（预印本、会议版、期刊版、重复导入）。
作品层不替换条目身份：papers 表与既有 ID、别名、深链接保持不变，
paper_works 记录条目到作品的归属、匹配依据与置信度，冲突进入复核列表。
"""
from __future__ import annotations
import json
import re
from kbapp.core.io import digest, now
from kbapp.core.metadata import normalize_doi


def normalize_title(title):
    return re.sub(r'[^0-9a-z\u4e00-\u9fff]+', '', (title or '').lower())


def title_work_id(title, year):
    return 'work:' + digest(normalize_title(title) + '|' + str(year or ''))[:20]


def paper_identity(paper):
    """返回 (work_id, basis, confidence, evidence)。"""
    doi = normalize_doi(paper.get('doi'))
    if doi:
        return 'doi:' + doi, 'doi', 'high', {'doi': doi}
    title = normalize_title(paper.get('title'))
    if title:
        evidence = {'title': paper.get('title'), 'year': paper.get('year')}
        authors = [a for a in (paper.get('authors') or []) if a][:3]
        if authors:
            evidence['authors'] = authors
        return title_work_id(paper.get('title'), paper.get('year')), 'title+year', 'medium', evidence
    return 'paper:' + paper['id'], 'paper_id', 'low', {}


def classify_version(paper):
    """C03：按题名与 venue 判定版本类型；证据一并保存，无法判断时为 unknown。"""
    title = (paper.get('title') or '').lower()
    venue = (paper.get('venue') or '').strip()
    lowered = venue.lower()
    if 'preprint' in title or re.search(r'arxiv|biorxiv|medrxiv|ssrn|research square|preprints?\.org', lowered):
        return 'preprint', venue, 'high', {'title': paper.get('title'), 'venue': venue}
    if re.search(r'conference|proceedings|symposium|workshop|congress|meeting|会议', lowered):
        return 'conference', venue, 'high', {'venue': venue}
    if venue:
        return 'journal', venue, 'low', {'venue': venue}
    return 'unknown', venue, 'low', {}


def rebuild(store):
    """重建作品层；幂等。返回统计与需要人工复核的歧义。"""
    papers = store.papers()
    works = {}
    claims = {}
    for pid, paper in papers.items():
        wid, basis, confidence, evidence = paper_identity(paper)
        claims[pid] = (wid, basis, confidence, evidence)
        work = works.setdefault(wid, {'id': wid, 'title': paper.get('title', ''), 'year': paper.get('year'),
                                      'doi': normalize_doi(paper.get('doi')), 'papers': [], 'title_key': normalize_title(paper.get('title'))})
        work['papers'].append(pid)
    # 无 DOI 条目：按规范化标题+年份归入唯一 DOI 作品；标题相同但年份不同不合并
    by_title_year = {}
    for wid, work in works.items():
        if work['doi'] and work['title_key']:
            by_title_year.setdefault((work['title_key'], str(work['year'] or '')), []).append(wid)
    conflicts = []
    for pid, (wid, basis, confidence, evidence) in list(claims.items()):
        if basis != 'title+year':
            continue
        candidates = by_title_year.get((normalize_title(papers[pid].get('title')), str(papers[pid].get('year') or '')), [])
        candidates = [c for c in candidates if c != wid]
        if len(candidates) == 1:
            claims[pid] = (candidates[0], 'title+year', 'medium', dict(evidence, matched_work=candidates[0]))
            works[candidates[0]]['papers'].append(pid)
            works.pop(wid, None)
        elif len(candidates) > 1:
            conflicts.append({'paper_id': pid, 'field': 'work', 'authoritative': json.dumps(candidates),
                              'proposed': json.dumps(wid), 'source': 'work_title_match'})
    stamp = now()
    with store.db() as db:
        known = {r[0] for r in db.execute('SELECT id FROM papers')}
        for wid in list(works):
            works[wid]['papers'] = sorted(set(works[wid]['papers']))
        db.execute('DELETE FROM works')
        for wid, work in works.items():
            db.execute('INSERT INTO works VALUES(?,?,?)', (wid, json.dumps({k: work[k] for k in ('id', 'title', 'year', 'doi')} | {'papers': work['papers']}, ensure_ascii=False), stamp))
        db.execute('DELETE FROM paper_works')
        for pid, (wid, basis, confidence, evidence) in claims.items():
            if pid not in known:
                continue
            db.execute('INSERT INTO paper_works VALUES(?,?,?,?,?,?)', (pid, wid, basis, confidence, json.dumps(evidence, ensure_ascii=False), stamp))
        db.execute('DELETE FROM work_versions')
        for pid, (wid, _basis, _confidence, _evidence) in claims.items():
            if pid not in known:
                continue
            version, venue, vconfidence, vevidence = classify_version(papers[pid])
            db.execute('INSERT INTO work_versions VALUES(?,?,?,?,?,?)',
                       (pid, wid, version, venue, json.dumps(dict(vevidence, confidence=vconfidence), ensure_ascii=False), stamp))
        for conflict in conflicts:
            cid = digest('work|' + conflict['paper_id'])
            db.execute('INSERT OR IGNORE INTO metadata_conflicts(id,paper_id,field,authoritative,proposed,source,status,created) VALUES(?,?,?,?,?,?,?,?)',
                       (cid, conflict['paper_id'], conflict['field'], conflict['authoritative'], conflict['proposed'], conflict['source'], 'pending', stamp))
    multi = sum(1 for w in works.values() if len(w['papers']) > 1)
    return {'works': len(works), 'papers': len(claims), 'multi_paper_works': multi, 'conflicts': len(conflicts)}


def work_of(store, paper_id):
    with store.db() as db:
        row = db.execute('SELECT work_id,basis,confidence,evidence FROM paper_works WHERE paper_id=?', (store.resolve(paper_id),)).fetchone()
    if not row:
        return None
    return {'work_id': row[0], 'basis': row[1], 'confidence': row[2], 'evidence': json.loads(row[3])}


def work_papers(store, work_id):
    with store.db() as db:
        rows = db.execute('SELECT paper_id FROM paper_works WHERE work_id=? ORDER BY paper_id', (work_id,)).fetchall()
    return [r[0] for r in rows]


def versions_of(store, work_id):
    """C03：同一作品的版本列表；预印本在前，同类型按年份排序。"""
    order = {'preprint': 0, 'conference': 1, 'journal': 2, 'unknown': 3}
    with store.db() as db:
        rows = db.execute('SELECT paper_id,version,venue,evidence FROM work_versions WHERE work_id=?', (work_id,)).fetchall()
        years = {r[0]: r[1] for r in db.execute('SELECT id,json_extract(payload,\'$.year\') FROM papers WHERE id IN (%s)' % ','.join('?' * len(rows)), [r[0] for r in rows])} if rows else {}
    result = [{'paper_id': r[0], 'version': r[1], 'venue': r[2], 'evidence': json.loads(r[3]), 'year': years.get(r[0])} for r in rows]
    return sorted(result, key=lambda v: (order.get(v['version'], 9), v['year'] or 9999))
