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

    def test_builtin_vocabulary_fail_fast_when_builtin_ids_removed(self):
        # DR-1 锚定：KB_DIRECTIONS 改掉内置编号且未设置 KB_TITLE_RULES 时，
        # 内置词表过同一校验，加载即 fail-fast；报错指明默认值身份并给出两条出路。
        saved = cards.DIR_ORDER
        cards.DIR_ORDER = ['A1', 'A2', '90']
        self.addCleanup(setattr, cards, 'DIR_ORDER', saved)
        with self.assertRaises(ValueError) as ctx:
            cards.load_title_rules()
        message = str(ctx.exception)
        self.assertIn('内置题名词表', message)
        self.assertIn('未设置 KB_TITLE_RULES 时的默认值', message)
        self.assertIn('"04"', message)
        self.assertIn('不在方向表', message)
        self.assertIn('设置 KB_TITLE_RULES', message)
        self.assertIn('KB_DIRECTIONS', message)

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


if __name__ == '__main__':
    unittest.main()
