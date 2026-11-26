"""题名方向归类规则：三态配置、双语义（卡片第一条命中/谱系汇总）、人工保护与缓存自动失效。"""
import os
import tempfile
import unittest
from kbapp.core.io import read_json
from kbapp.library import cards
from kbapp.research import graph, method_taxonomy as taxonomy
from kbapp.storage.store import Store


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
        # 谱系：无方向的文献保持无方向、不置 direction_basis
        papers = {'blank': {'title': 'RRT path planning'}}
        taxonomy.annotate(papers, [])
        self.assertEqual(papers['blank']['directions'], [])
        self.assertNotIn('direction_basis', papers['blank'])

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

    def test_builtin_vocabulary_check_deferred_to_title_inference_use_points(self):
        # PR #106 评审:内置词表是默认值而非用户配置。方向表改掉内置编号且未设置
        # KB_TITLE_RULES 时,导入只验结构与正则,编号不配套在真正使用题名推断的地方
        # 才报出来——不涉及题名推断的入口不能因为这条默认值整体起不来。
        saved = cards.DIR_ORDER
        cards.DIR_ORDER = ['A1', 'A2', '90']
        self.addCleanup(setattr, cards, 'DIR_ORDER', saved)
        for raw in (None, '[]'):
            if raw is None:
                os.environ.pop('KB_TITLE_RULES', None)
            else:
                os.environ['KB_TITLE_RULES'] = raw
            cards.KEYWORD_RULES = cards.load_title_rules()  # 导入路径:不抛错
        os.environ.pop('KB_TITLE_RULES', None)
        cards.KEYWORD_RULES = cards.load_title_rules()
        # 题名推断使用点:报错指明默认值身份、具体规则与两条出路
        with self.assertRaises(ValueError) as ctx:
            cards.directions_from_keywords('覆盖与巡检')
        message = str(ctx.exception)
        self.assertIn('内置题名词表', message)
        self.assertIn('未设置 KB_TITLE_RULES 时的默认值', message)
        self.assertIn('("覆盖|coverage|CPP|inspection|巡检|检测", ["04"])', message)
        self.assertIn('"04"', message)
        self.assertIn('不在方向表', message)
        self.assertIn('设置 KB_TITLE_RULES', message)
        self.assertIn('KB_DIRECTIONS', message)
        # 用户显式配了表外编号:报错同样指名该条规则,并给出关闭题名推断的出路
        os.environ['KB_TITLE_RULES'] = '[["冷原子", ["04"]]]'
        cards.KEYWORD_RULES = cards.load_title_rules()
        with self.assertRaises(ValueError) as ctx:
            cards.directions_from_keywords('冷原子干涉仪')
        message = str(ctx.exception)
        self.assertIn('("冷原子", ["04"])', message)
        self.assertIn('KB_TITLE_RULES=[]', message)

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
        # 旧规则曾由题名推断出方向；换成不命中该题名的新规则后，推断结果必须清空，
        # 且不再有 direction_basis（issue #100：相同论文按新规则重新得到展示结果）。
        cards.KEYWORD_RULES = [('冷原子', ['01'])]
        self.assertEqual(graph.graph_papers(self.store)['cold']['directions'], ['01'])
        self.assertEqual(graph.graph_papers(self.store)['cold']['direction_basis'], 'title_keywords')
        path = self.store.paths.cache / 'graph-taxonomy.json'
        self.assertTrue(path.exists())

        # 内存路径：同一 Store 换成不命中的规则 → directions 清空、无 direction_basis
        cards.KEYWORD_RULES = [('湍流', ['09'])]
        paper = graph.graph_papers(self.store)['cold']
        self.assertEqual(paper['directions'], [])
        self.assertNotIn('direction_basis', paper)

        # 磁盘路径：重启复用（第二个 Store 从磁盘 records 恢复）同样不命中旧结果
        prior = read_json(path, {})
        self.assertTrue(prior.get('records'))
        restart = Store(self.temp.name)
        paper = graph.graph_papers(restart)['cold']
        self.assertEqual(paper['directions'], [])
        self.assertNotIn('direction_basis', paper)

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


if __name__ == '__main__':
    unittest.main()
