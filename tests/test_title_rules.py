"""题名方向归类规则：三态配置、双语义（卡片第一条命中/谱系汇总）、人工保护与缓存自动失效。"""
import json
import os
import sqlite3
import tempfile
import unittest
from kbapp.core.io import atomic, read_json
from kbapp.library import cards
from kbapp.library.sources import sync_library
from kbapp.research import graph, method_taxonomy as taxonomy
from kbapp.storage.store import Store


def _jsonl_first(path):
    with open(path, encoding='utf-8') as fh:
        return json.loads(fh.readline())


def _zotero_fixture(zdir):
    """最小 Zotero 库快照：一篇有标题与年份的期刊论文，供真实 sync_library 读取。"""
    db = sqlite3.connect(os.path.join(str(zdir), 'zotero.sqlite'))
    db.executescript("""
CREATE TABLE items(itemID INTEGER PRIMARY KEY, key TEXT, itemTypeID INTEGER, dateAdded TEXT, dateModified TEXT, libraryID INTEGER, version INTEGER, clientDateModified TEXT);
CREATE TABLE deletedItems(itemID INTEGER);
CREATE TABLE itemTypesCombined(itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
CREATE TABLE fieldsCombined(fieldID INTEGER PRIMARY KEY, fieldName TEXT);
CREATE TABLE itemDataValues(valueID INTEGER PRIMARY KEY, value TEXT);
CREATE TABLE itemData(itemID INTEGER, fieldID INTEGER, valueID INTEGER);
CREATE TABLE creators(creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT, fieldMode INTEGER);
CREATE TABLE creatorTypes(creatorTypeID INTEGER PRIMARY KEY, creatorType TEXT);
CREATE TABLE itemCreators(itemID INTEGER, orderIndex INTEGER, creatorTypeID INTEGER, creatorID INTEGER);
CREATE TABLE tags(tagID INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE itemTags(itemID INTEGER, tagID INTEGER, type INTEGER);
CREATE TABLE collections(collectionID INTEGER PRIMARY KEY, collectionName TEXT, parentCollectionID INTEGER, libraryID INTEGER, key TEXT, version INTEGER);
CREATE TABLE deletedCollections(collectionID INTEGER);
CREATE TABLE collectionItems(collectionID INTEGER, itemID INTEGER);
CREATE TABLE itemAttachments(itemID INTEGER PRIMARY KEY, parentItemID INTEGER, linkMode INTEGER, contentType TEXT, path TEXT);
""")
    db.execute("INSERT INTO itemTypesCombined VALUES(2,'journalArticle')")
    db.execute("INSERT INTO fieldsCombined VALUES(1,'title')")
    db.execute("INSERT INTO fieldsCombined VALUES(2,'date')")
    db.execute("INSERT INTO itemDataValues VALUES(1,'冷原子干涉仪的量子传感应用')")
    db.execute("INSERT INTO itemDataValues VALUES(2,'2021-05-01')")
    db.execute("INSERT INTO items VALUES(1,'AAAABBBBCCCCDDDD',2,'2021-05-02','2021-05-02',1,10,'2021-05-02')")
    db.execute("INSERT INTO itemData VALUES(1,1,1)")
    db.execute("INSERT INTO itemData VALUES(1,2,2)")
    db.execute("INSERT INTO creators VALUES(1,'San','Zhang',0)")
    db.execute("INSERT INTO creatorTypes VALUES(1,'author')")
    db.execute("INSERT INTO itemCreators VALUES(1,0,1,1)")
    db.commit()
    db.close()


class TitleRuleValidation(unittest.TestCase):
    def setUp(self):
        # 环境变量与模块属性都做保存/恢复，测试互不泄漏（无论进程环境是否设置过该变量）
        self._saved_env = os.environ.pop('KB_TITLE_RULES', None)
        self.addCleanup(self._restore_env)
        self._saved_rules = cards.KEYWORD_RULES
        self.addCleanup(setattr, cards, 'KEYWORD_RULES', self._saved_rules)

    def _restore_env(self):
        if self._saved_env is None:
            os.environ.pop('KB_TITLE_RULES', None)
        else:
            os.environ['KB_TITLE_RULES'] = self._saved_env

    def _load_with_env(self, raw):
        """在受控环境变量下重新执行加载逻辑；规则在函数体内读 env，无需 importlib.reload。"""
        if raw is None:
            os.environ.pop('KB_TITLE_RULES', None)
        else:
            os.environ['KB_TITLE_RULES'] = raw
        return cards.load_title_rules()

    def test_unset_uses_builtin_vocabulary(self):
        self.assertEqual(self._load_with_env(None), cards._DEFAULT_TITLE_RULES)
        cards.KEYWORD_RULES = self._load_with_env(None)
        # 卡片语义：第一条命中规则即返回
        self.assertEqual(cards.directions_from_keywords('RRT path planning'), ['02'])
        self.assertEqual(cards.pick_directions({'title': 'RRT path planning', 'collections': []}, ''), ['02'])
        # 谱系语义：汇总全部命中，依据标记 title_keywords
        papers = {'auto': {'title': 'RRT path planning'}}
        taxonomy.annotate(papers, [])
        self.assertIn('02', papers['auto']['directions'])
        self.assertEqual(papers['auto']['direction_basis'], 'title_keywords')

    def test_explicit_empty_list_disables_title_inference(self):
        rules = self._load_with_env('[]')
        self.assertEqual(rules, [])
        cards.KEYWORD_RULES = rules
        # 生效规则为空：卡片不再走题名推断，落到磁盘规则或兜底 '90'
        self.assertEqual(cards.directions_from_keywords('RRT path planning'), [])
        self.assertEqual(cards.pick_directions({'title': 'RRT path planning', 'collections': []}, ''), ['90'])
        # 谱系：无方向的文献保持无方向；来源记为 fallback(落 90),以便规则变化后能重算
        papers = {'blank': {'title': 'RRT path planning'}}
        taxonomy.annotate(papers, [])
        self.assertEqual(papers['blank']['directions'], [])
        self.assertEqual(papers['blank']['primary_direction'], '90')
        self.assertEqual(papers['blank']['direction_basis'], 'fallback')

    def test_invalid_regex_error_names_the_rule(self):
        with self.assertRaises(ValueError) as ctx:
            cards.validate_title_rules([['正常', ['01']], ['正常', ['01']], ['[', ['01']]])
        message = str(ctx.exception)
        self.assertIn('第 3 条', message)
        self.assertIn('("[", ["01"])', message)
        self.assertIn('正则无效', message)

    def test_unknown_direction_error_names_the_rule(self):
        with self.assertRaises(ValueError) as ctx:
            cards.validate_title_rules([['冷原子', ['99']]])
        message = str(ctx.exception)
        self.assertIn('第 1 条', message)
        # 错误信息同时指出该条规则的内容（正则与目标方向）与坏编号
        self.assertIn('("冷原子", ["99"])', message)
        self.assertIn('"99"', message)
        self.assertIn('不在方向表', message)

    def test_direction_check_uses_effective_dir_order_not_hardcoded_ids(self):
        saved = cards.DIR_ORDER
        cards.DIR_ORDER = ['A1', 'A2']
        self.addCleanup(setattr, cards, 'DIR_ORDER', saved)
        self.assertEqual(cards.validate_title_rules([['quantum', ['A1']]]), [('quantum', ['A1'])])
        with self.assertRaises(ValueError) as ctx:
            cards.validate_title_rules([['quantum', ['02']]])
        message = str(ctx.exception)
        self.assertIn('("quantum", ["02"])', message)
        self.assertIn('"02"', message)
        self.assertIn('不在方向表', message)

    def test_builtin_vocabulary_converges_to_effective_direction_table(self):
        # PR #106 复核 P1:内置词表是默认值而非用户配置。方向表换成别的领域后,
        # 未设置 KB_TITLE_RULES 的库必须保持原有行为(能对上的编号照常推断,
        # 对不上的条目只是不参与),而不是 update / 图谱渲染整体不可用。
        saved = cards.DIR_ORDER
        cards.DIR_ORDER = ['A1', 'A2', '90']
        self.addCleanup(setattr, cards, 'DIR_ORDER', saved)
        os.environ.pop('KB_TITLE_RULES', None)
        rules = cards.load_title_rules()
        self.assertEqual(rules, [], '内置词表没有任何编号落在自定义方向表内')
        self.assertEqual(len(cards.dropped_title_rules()), len(cards._DEFAULT_TITLE_RULES))
        cards.KEYWORD_RULES = rules
        self.assertEqual(cards.directions_from_keywords('覆盖与巡检'), [])
        self.assertEqual(cards.pick_directions({'title': '覆盖与巡检', 'collections': []}, ''), ['90'])
        # 用户显式配置的表外编号仍属配置错误,报错指名该条规则(不是静默剔除)
        os.environ['KB_TITLE_RULES'] = '[["冷原子", ["04"]]]'
        with self.assertRaises(ValueError) as ctx:
            cards.load_title_rules()
        message = str(ctx.exception)
        self.assertIn('第 1 条', message)
        self.assertIn('("冷原子", ["04"])', message)
        self.assertIn('不在方向表', message)
        os.environ.pop('KB_TITLE_RULES', None)
        # 方向表保留部分内置编号时,对得上的条目照常生效
        cards.DIR_ORDER = ['04', 'A1', '90']
        cards.KEYWORD_RULES = cards.load_title_rules()
        self.assertEqual(cards.directions_from_keywords('覆盖与巡检'), ['04'])
        self.assertEqual(cards.directions_from_keywords('冷原子量子'), [])

    def test_structure_error_names_the_rule(self):
        # issue #100 要求 2「错误信息指出具体是哪条规则(序号与内容)」:结构错误
        # 同样要指名序号与内容,不能只给形状模板(PR #106 评审)。
        for row, fragment in ((['x'], '["x"]'), (['x', ['01'], 'extra'], '["x", ["01"], "extra"]')):
            with self.assertRaises(ValueError) as ctx:
                cards.validate_title_rules([['正常', ['01']], row])
            message = str(ctx.exception)
            self.assertIn('第 2 条', message)
            self.assertIn(fragment, message)
            self.assertIn('必须是 [正则, [方向编号]] 形式的数组', message)
        # 顶层不是数组仍按整体报错(没有「第几条」可指)
        with self.assertRaises(ValueError) as ctx:
            cards.validate_title_rules({'x': 1})
        self.assertNotIn('第 1 条', str(ctx.exception))

    def test_custom_rules_first_match_for_cards_and_union_for_lineage(self):
        cards.KEYWORD_RULES = [('路径', ['02']), ('规划', ['06'])]
        # 同一规则源：卡片只取第一条命中的规则
        self.assertEqual(cards.directions_from_keywords('低温路径规划'), ['02'])
        # 谱系：全部命中规则的方向并集（排序后）
        papers = {'dual': {'title': '低温路径规划'}}
        taxonomy.annotate(papers, [])
        self.assertEqual(papers['dual']['directions'], ['02', '06'])

    def test_manual_classification_protected_under_custom_rules(self):
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        papers = {
            'auto': {'title': '冷原子干涉仪'},
            'manual_flag': {'title': '冷原子干涉仪', 'directions': [], 'classification': {'manual': True}},
            'auto_off': {'title': '冷原子干涉仪', 'directions': [], 'classification': {'automatic': False}},
            'assignment': {'title': '冷原子干涉仪', 'directions': [], 'direction_assignment': 'manual'},
        }
        taxonomy.annotate(papers, [])
        self.assertEqual(papers['auto']['directions'], ['01'])
        self.assertEqual(papers['auto']['direction_basis'], 'title_keywords')
        for pid in ('manual_flag', 'auto_off', 'assignment'):
            self.assertEqual(papers[pid]['directions'], [])
            self.assertNotIn('direction_basis', papers[pid])


class RuleChangeCacheInvalidation(unittest.TestCase):
    """规则摘要同时进单篇指纹与 _catalog_key：规则变化后内存与磁盘缓存都按新规则重新归类。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(self.temp.name)
        self.store.put_papers([{'id': 'cold', 'kind': 'library', 'title': '冷原子干涉仪与量子传感'}])
        self.original = cards.KEYWORD_RULES
        self.addCleanup(setattr, cards, 'KEYWORD_RULES', self.original)

    def test_rule_change_invalidates_in_memory_and_disk_cache(self):
        revision = self.store.state('paper_revision', 0)
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        self.assertEqual(graph.graph_papers(self.store)['cold']['directions'], ['01'])
        self.assertEqual(self.store.state('paper_revision', 0), revision, 'paper_revision 不变也要按新规则重算')
        path = self.store.paths.cache / 'graph-taxonomy.json'
        self.assertTrue(path.exists())

        # 内存缓存路径：同一 Store，仅规则变化 → _catalog_key 含规则摘要 → 整体重算
        cards.KEYWORD_RULES = [('量子', ['02'])]
        self.assertEqual(graph.graph_papers(self.store)['cold']['directions'], ['02'])

        # 磁盘缓存路径：在同一目录新建第二个 Store（等价于进程重启复用），
        # 从磁盘旧 records 恢复也必须按新指纹重新归类
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        prior = read_json(path, {})
        self.assertTrue(prior.get('records'))
        restart = Store(self.temp.name)
        self.assertEqual(graph.graph_papers(restart)['cold']['directions'], ['01'])

    def test_rule_change_to_non_matching_rule_clears_inferred_directions(self):
        # 旧规则曾由题名推断出方向；换成不命中该题名的新规则后，推断结果必须清空
        # (issue #100：相同论文按新规则重新得到展示结果)。来源改记 fallback，
        # 这样之后再加回命中规则时还能重算，而不是永久停在空方向。
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        self.assertEqual(graph.graph_papers(self.store)['cold']['directions'], ['01'])
        self.assertEqual(graph.graph_papers(self.store)['cold']['direction_basis'], 'title_keywords')
        path = self.store.paths.cache / 'graph-taxonomy.json'
        self.assertTrue(path.exists())

        # 内存路径：同一 Store 换成不命中的规则 → directions 清空、来源降为 fallback
        cards.KEYWORD_RULES = [('湍流', ['09'])]
        paper = graph.graph_papers(self.store)['cold']
        self.assertEqual(paper['directions'], [])
        self.assertEqual(paper['primary_direction'], '90')
        self.assertEqual(paper['direction_basis'], 'fallback')

        # 磁盘路径：重启复用（第二个 Store 从磁盘 records 恢复）同样不命中旧结果
        prior = read_json(path, {})
        self.assertTrue(prior.get('records'))
        restart = Store(self.temp.name)
        paper = graph.graph_papers(restart)['cold']
        self.assertEqual(paper['directions'], [])
        self.assertEqual(paper['direction_basis'], 'fallback')

    def test_lineage_direction_basis_refreshed_after_rule_change(self):
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        self.assertEqual(graph.graph_papers(self.store)['cold']['direction_basis'], 'title_keywords')
        cards.KEYWORD_RULES = [('量子', ['02'])]
        paper = graph.graph_papers(self.store)['cold']
        self.assertEqual(paper['directions'], ['02'])
        self.assertEqual(paper['direction_basis'], 'title_keywords')

    def test_persisted_title_inferred_directions_recompute_after_rule_change(self):
        # 真实入库形态(sources.py 在入库时把 pick_directions 的结果连同来源与规则摘要
        # 写进 papers):规则 A 推断出的方向已持久化,换成规则 B 后展示侧必须按 B 重算,
        # 而不是因为 directions 非空就继续沿用旧值(PR #106 评审)。
        self.store.put_papers([{'id': 'ingested', 'kind': 'library', 'title': '冷原子干涉仪',
                                 'directions': ['04'], 'direction_basis': 'title_keywords',
                                 'direction_rules_digest': cards.title_rules_digest()}])
        cards.KEYWORD_RULES = [('冷原子', ['02'])]
        self.assertEqual(graph.graph_papers(self.store)['ingested']['directions'], ['02'])
        restart = Store(self.temp.name)
        self.assertEqual(graph.graph_papers(restart)['ingested']['directions'], ['02'], '磁盘复用同样重算')

    def test_persisted_manual_and_collection_directions_untouched_after_rule_change(self):
        # 人工归类与合集/磁盘来源的方向不是题名推断产物:规则变化后保持原值(要求 2 的语义保护)。
        self.store.put_papers([
            {'id': 'manual', 'kind': 'library', 'title': '冷原子干涉仪', 'directions': ['08'],
             'classification': {'manual': True}, 'direction_basis': 'title_keywords', 'direction_rules_digest': 'old'},
            {'id': 'collection', 'kind': 'library', 'title': '冷原子干涉仪', 'directions': ['03'],
             'direction_basis': 'collections', 'direction_rules_digest': 'old'},
        ])
        cards.KEYWORD_RULES = [('冷原子', ['02'])]
        papers = graph.graph_papers(self.store)
        self.assertEqual(papers['manual']['directions'], ['08'])
        self.assertEqual(papers['collection']['directions'], ['03'])

    def test_ingestion_records_direction_source_and_rule_digest(self):
        # 入库写入点(sources.sync_library)必须留下来源,展示侧才能区分「题名推断」
        # 与其他来源;pick_directions 的取值与优先次序保持不变。
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        self.assertEqual(cards.pick_directions_with_basis({'title': '冷原子干涉仪', 'collections': []}, ''),
                         (['01'], 'title_keywords'))
        self.assertEqual(cards.pick_directions_with_basis({'title': '无关键词题名', 'collections': []}, ''),
                         (['90'], 'fallback'))
        self.assertEqual(cards.pick_directions({'title': '冷原子干涉仪', 'collections': []}, ''), ['01'])
        self.assertEqual(cards.pick_directions({'title': '无关键词题名', 'collections': []}, ''), ['90'])

    def test_sync_library_ingests_new_paper_without_prior_record(self):
        # 端到端锚定真实入库路径(PR #106 复核 P0):新文献 prev 为空时 r 里没有 directions,
        # 赋值顺序颠倒会在 sources.sync_library 抛 KeyError,update 整体中断。
        store = Store(self.temp.name)
        zdir = os.path.join(self.temp.name, 'zotero')
        os.makedirs(zdir)
        _zotero_fixture(zdir)
        base = os.path.join(self.temp.name, 'base')
        os.makedirs(base)
        atomic(store.paths.state / 'settings.json', {'zotero_dir': zdir})
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        papers, _, _ = sync_library(store, os.path.join(base, '_index'))
        self.assertEqual(len(papers), 1)
        paper = papers[0]
        self.assertEqual(paper['directions'], ['01'])
        self.assertEqual(paper['primary_direction'], '01')
        self.assertEqual(paper['direction_basis'], 'title_keywords')
        self.assertEqual(paper['direction_rules_digest'], cards.title_rules_digest())
        # 增量入库同一篇:不抛错且沿用同一来源标记
        again, _, _ = sync_library(store, os.path.join(base, '_index'))
        self.assertEqual(again[0]['directions'], ['01'])
        self.assertEqual(again[0]['primary_direction'], '01')

    def test_sync_library_records_fallback_then_recomputes_after_rule_change(self):
        # PR #106 复核 P1:落 90 兜底的入库文献在规则变化后必须重算——这正是
        # 「非机器人领域先按默认配置跑过、再设 KB_TITLE_RULES」的主路径。
        store = Store(self.temp.name)
        zdir = os.path.join(self.temp.name, 'zotero')
        os.makedirs(zdir)
        _zotero_fixture(zdir)
        base = os.path.join(self.temp.name, 'base')
        os.makedirs(base)
        atomic(store.paths.state / 'settings.json', {'zotero_dir': zdir})
        cards.KEYWORD_RULES = [('湍流', ['09'])]  # 不命中
        papers, _, _ = sync_library(store, os.path.join(base, '_index'))
        pid = papers[0]['id']
        self.assertEqual((papers[0]['directions'], papers[0]['direction_basis']), (['90'], 'fallback'))
        self.assertTrue(papers[0].get('direction_rules_digest'), '兜底也要写摘要,否则永不重算')
        store.put_papers(papers)
        cards.KEYWORD_RULES = [('冷原子', ['06'])]  # 换成命中
        paper = graph.graph_papers(store)[pid]
        self.assertEqual(paper['directions'], ['06'])
        self.assertEqual(paper['direction_basis'], 'title_keywords')
        restart = Store(self.temp.name)
        self.assertEqual(graph.graph_papers(restart)[pid]['directions'], ['06'], '磁盘复用同样重算')

    def test_recompute_syncs_fields_derived_from_directions(self):
        # PR #106 复核 P2:展示与筛选读 primary_direction(图谱配色、--direction 过滤),
        # 重算 directions 时必须一并刷新它和 project/project_stages,否则同一条文献自相矛盾。
        self.store.put_papers([{'id': 'p', 'kind': 'library', 'title': '冷原子干涉仪',
                                 'directions': ['06'], 'primary_direction': '06',
                                 'direction_basis': 'title_keywords', 'direction_rules_digest': 'old'}])
        cards.KEYWORD_RULES = [('冷原子', ['04'])]  # 04 在默认 STAGES 里
        paper = graph.graph_papers(self.store)['p']
        self.assertEqual(paper['directions'], ['04'])
        self.assertEqual(paper['primary_direction'], '04', '图谱配色 / --direction 过滤读它')
        self.assertEqual(paper['project_stages'], ['阶段四'])
        self.assertNotEqual(paper['project'], '', '落到课题阶段时必须同时置 project')
        # 反向:换回不属于课题阶段的方向时,派生字段必须一起清空
        cards.KEYWORD_RULES = [('冷原子', ['06'])]
        paper = graph.graph_papers(self.store)['p']
        self.assertEqual(paper['primary_direction'], '06')
        self.assertEqual(paper['project_stages'], [])
        self.assertEqual(paper['project'], '')

    def test_write_papers_all_keeps_direction_source(self):
        # PR #106 复核 P2:build_cards 计算的 direction_basis 不能在落盘时丢掉,
        # 否则 annotate 的 stale 判据对卡片重建产出的文献永远为 False。
        with tempfile.TemporaryDirectory() as out:
            cards.write_papers_all([{'citekey': 'K', 'zotero_key': None, 'title': 'T', 'authors': [],
                                     'year': 2020, 'venue': '', 'doi': '', 'abstract': '', 'tldr': '',
                                     'directions': ['06'], 'primary_direction': '06',
                                     'direction_basis': 'title_keywords', 'direction_rules_digest': 'abc',
                                     'tagfields': {}, 'other_tags': [], 'metadata_source': 'filename',
                                     'pdf': None, 'pdf_exists': False, 'collections': []}], out)
            row = _jsonl_first(os.path.join(out, 'papers_all.jsonl'))
        self.assertEqual(row['direction_basis'], 'title_keywords')
        self.assertEqual(row['direction_rules_digest'], 'abc')

    def test_sync_library_keeps_manual_direction_assignment(self):
        # 人工指定的方向不被入库覆盖;即使没有 directions 键也不能在 primary_direction 上抛错。
        store = Store(self.temp.name)
        zdir = os.path.join(self.temp.name, 'zotero')
        os.makedirs(zdir)
        _zotero_fixture(zdir)
        base = os.path.join(self.temp.name, 'base')
        os.makedirs(base)
        atomic(store.paths.state / 'settings.json', {'zotero_dir': zdir})
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        # 先入库一次,再标记为人工改向,再增量入库
        papers, _, _ = sync_library(store, os.path.join(base, '_index'))
        manual = dict(papers[0], direction_assignment='manual', directions=['08'], primary_direction='08')
        store.put_papers([dict(manual, kind='library')])
        again, _, _ = sync_library(store, os.path.join(base, '_index'))
        self.assertEqual(again[0]['directions'], ['08'])
        self.assertEqual(again[0]['primary_direction'], '08')


if __name__ == '__main__':
    unittest.main()
