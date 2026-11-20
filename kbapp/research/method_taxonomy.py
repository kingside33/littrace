"""谱系展示的稳定方法大类；只归并展示，不改写方法实体、人工分类或关系证据。"""
from __future__ import annotations
from collections import Counter
import json
import re
import unicodedata

VERSION = 'broad-methods-v1'
# 同类算法、变体和中英文别名共享一个入口；组件不再单独生成分支。
DEFINITIONS = [
    ('sampling', '基于采样', r'(?<![a-z0-9])(?:rrt|prm|bit|fmt)(?![a-z])|rapidly.?explor|probabilistic roadmap|batch informed|sampling|采样|随机树|概率路线图'),
    ('metaheuristic', '元启发式', r'meta.?heuristic|swarm|evolution|genetic|particle|ant colony|annealing|grey wolf|gray wolf|whale|cuckoo|harris hawk|sparrow|kepler|spider wasp|secretary bird|artificial plant|lightning search|\b(?:pso|aco|gwo|woa|mpa|koa)\b|元启发|群智|群体智能|粒子群|蚁群|遗传|进化|退火|灰狼|鲸鱼|布谷鸟|麻雀|哈里斯鹰|开普勒|仿生智能'),
    ('graph-search', '图搜索', r'graph.?search|(?<![a-z0-9])a(?:\*|[\s-]?star\b)|(?<![a-z0-9])d(?:\*|[\s-]?star\b)|dijkstra|shortest path|visibility graph|图搜索|启发式图|栅格搜索|最短路|可视图|标号法'),
    ('learning', '深度学习与强化学习', r'deep learning|reinforcement|neural|transformer|convolution|\b(?:cnn|rnn|lstm|ddpg|dqn|td3|ppo|sac|gan)\b|actor.?critic|attention|distill|contrastive|深度学习|强化学习|神经网络|神经元|长短.{0,2}记忆|注意力|蒸馏|对比学习'),
    ('optimization', '数值优化与最优控制', r'convex|quadratic programming|nonlinear programming|optimal control|model predictive|trajectory optim|numerical optim|\bmpc\b|凸优化|二次规划|非线性规划|最优控制|模型预测|轨迹优化|数值优化|梯度'),
    ('reactive', '势场与反应式规划', r'potential field|dynamic window|velocity obstacle|reactive|\b(?:apf|dwa)\b|势场|动态窗口|速度障碍|反应式'),
    ('coverage', '覆盖与任务规划', r'coverage|boustrophedon|viewpoint|task (?:allocation|planning)|multi.?agent|multi.?robot|覆盖|巡检|视点|任务分配|任务规划|协同规划|交接'),
    ('modeling', '机器人建模与构型', r'kinematic|dynamic model|reconfigur|modular robot|snake robot|soft robot|mechanical design|运动学|动力学建模|变构|构型|蛇形机器人|软体机器人|机构设计'),
    ('control', '运动控制与导航', r'control|tracking|guidance|navigation|控制|跟踪|制导|导航'),
    ('perception', '感知、定位与信号处理', r'perception|locali[sz]|mapping|\bslam\b|sensor|sonar|signal|image|vision|kalman|fusion|odometry|感知|定位|建图|传感|声呐|信号|图像|视觉|滤波|融合|里程计'),
    ('environment', '环境与约束建模', r'ocean|current field|hydrodynamic|environment|collision|obstacle|constraint|海洋|海流|水动力|环境建模|碰撞|障碍|约束建模'),
    ('simulation', '仿真与实验', r'simulat|experiment|benchmark|test.?bed|water.?tank|仿真|实验|试验|测试平台'),
    ('theory', '理论分析与综述', r'\breview\b|\bsurvey\b|convergence|completeness|statistic|theor|综述|收敛|完备性|统计|理论分析'),
]
FAMILIES = {f'family:{key}': name for key, name, _ in DEFINITIONS}
OTHER = 'family:other'
FAMILIES[OTHER] = '其他研究与待分类'
PATTERNS = [(f'family:{key}', re.compile(pattern, re.I)) for key, _, pattern in DEFINITIONS]
# 方向入口只呈现该方向采用的粗分类；不能归入这些方法的资料仍留在“其他”中。
DIRECTION_FAMILIES = {
    '00': ('theory', 'simulation'),
    '01': ('modeling', 'control', 'optimization', 'learning', 'environment', 'simulation'),
    '02': ('sampling', 'metaheuristic', 'graph-search', 'learning', 'optimization', 'reactive'),
    '03': ('coverage', 'control', 'perception', 'learning', 'optimization'),
    '04': ('coverage', 'sampling', 'metaheuristic', 'graph-search', 'learning', 'optimization', 'reactive'),
    '05': ('modeling', 'coverage', 'control', 'optimization', 'learning'),
    '06': ('metaheuristic', 'optimization', 'graph-search', 'sampling', 'learning', 'theory'),
    '07': ('simulation', 'modeling', 'environment'),
    '08': ('perception', 'learning', 'optimization', 'environment'),
    '09': ('environment', 'perception', 'modeling', 'simulation', 'theory'),
}


def allowed_families(direction):
    return {f'family:{key}' for key in DIRECTION_FAMILIES[direction]} | {OTHER} if direction in DIRECTION_FAMILIES else set(FAMILIES)


def matched(text):
    text = unicodedata.normalize('NFKC', text or '').replace('∗', '*')
    return [mid for mid, pattern in PATTERNS if pattern.search(text)]


def concept_families(row):
    payload = json.loads(row.get('payload') or '{}') if isinstance(row.get('payload'), str) else row.get('payload') or {}
    aliases = json.loads(row.get('aliases') or '[]') if isinstance(row.get('aliases'), str) else row.get('aliases') or []
    direct = matched(' '.join([row.get('name') or payload.get('name') or ''] + aliases))
    return direct or matched(payload.get('family', ''))


def annotate(papers, concepts, direction='', prior=None, records=None):
    """为全部文献计算展示归类，具体算法ID仍可用于旧链接和精确筛选。"""
    by_method = {row['id']: concept_families(row) for row in concepts}
    from kbapp.library.cards import KEYWORD_RULES, title_rules_digest
    from kbapp.core.io import digest
    rules_digest = title_rules_digest()
    for paper in papers.values():
        raw = list(dict.fromkeys(paper.get('methods') or []))
        classification = paper.get('classification') or {}
        manual = classification.get('manual') or classification.get('automatic') is False or paper.get('direction_assignment') == 'manual'
        fingerprint = digest(json.dumps([VERSION, rules_digest, direction, paper.get('title'), [(mid, by_method.get(mid)) for mid in raw],
                                         paper.get('directions'), bool(manual)], ensure_ascii=False))
        saved = (prior or {}).get(paper.get('id'))
        if saved and saved.get('fingerprint') == fingerprint:
            paper.update(saved['fields'])
            if records is not None:
                records[paper['id']] = saved
            continue
        votes = Counter(mid for method in raw for mid in by_method.get(method, []))
        title_matches = matched(paper.get('title', ''))
        votes.update({mid: 4 for mid in title_matches})
        allowed = allowed_families(direction)
        votes = Counter({mid: count for mid, count in votes.items() if mid in allowed})
        primary = max(votes, key=lambda mid: (votes[mid], -list(FAMILIES).index(mid))) if votes else OTHER
        memberships = [mid for mid in FAMILIES if mid in votes] or [OTHER]
        paper['method_families'] = memberships
        paper['method_family_scores'] = dict(votes)
        paper['primary_method_family'] = primary
        paper['methods'] = list(dict.fromkeys(raw + memberships))
        # 外部文献通常没有已保存的方向：题名规则只补充展示，明确保留依据。
        if not paper.get('directions') and not manual:
            inferred = sorted({d for pattern, directions in KEYWORD_RULES
                               if re.search(pattern, paper.get('title', ''), re.I) for d in directions})
            paper['directions'] = inferred
            if inferred:
                paper['direction_basis'] = 'title_keywords'
        if records is not None:
            fields = ('method_families', 'method_family_scores', 'primary_method_family', 'methods', 'directions', 'direction_basis')
            records[paper['id']] = {'fingerprint': fingerprint, 'fields': {k: paper[k] for k in fields if k in paper}}
    return papers


def scope(papers, direction=''):
    """方向仅裁剪公共分类得分，不再执行题名与方法正则识别。"""
    if not direction:
        return papers
    allowed = allowed_families(direction)
    result = {}
    for pid, paper in papers.items():
        votes = {mid: count for mid, count in paper['method_family_scores'].items() if mid in allowed}
        memberships = [mid for mid in FAMILIES if mid in votes] or [OTHER]
        primary = max(votes, key=lambda mid: (votes[mid], -list(FAMILIES).index(mid))) if votes else OTHER
        raw = [mid for mid in paper['methods'] if not mid.startswith('family:')]
        result[pid] = dict(paper, method_family_scores=votes, method_families=memberships,
                           primary_method_family=primary, methods=raw + memberships)
    return result


def method_options(papers, direction='', project=False, search=''):
    counts = Counter()
    for paper in papers.values():
        if direction and direction not in (paper.get('directions') or []):
            continue
        if project and not paper.get('project'):
            continue
        if search and search.casefold() not in ' '.join(str(paper.get(k) or '') for k in ('title', 'citekey', 'authors')).casefold():
            continue
        selected = [mid for mid in paper.get('method_families') or [] if mid in allowed_families(direction)]
        counts.update(selected or [OTHER])
    return [{'id': mid, 'name': name, 'papers': counts[mid], 'aliases': [], 'payload': {'family': name}}
            for mid, name in FAMILIES.items() if counts[mid]]
