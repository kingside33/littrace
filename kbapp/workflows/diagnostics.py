"""架构约束和现有验证工具的统一入口。"""
import ast
import graphlib
import importlib
import json
from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

def architecture(root=None):
    root = Path(root or Path(__file__).resolve().parents[1])
    graph = {}
    missing = []
    forbidden = []
    levels = {'core': 0, 'storage': 1, 'integrations': 2, 'library': 3, 'search': 4, 'research': 5, 'workflows': 6, 'web': 7}
    for p in sorted(root.rglob('*.py')):
        name = 'kbapp.' + '.'.join(p.relative_to(root).with_suffix('').parts)
        tree = ast.parse(p.read_text(encoding='utf-8'))
        deps = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith('kbapp.'):
                deps.add(node.module)
            if isinstance(node, ast.Import):
                deps.update((a.name for a in node.names if a.name.startswith('kbapp.')))
        graph[name] = deps
        for dep in deps:
            target = root.parent.joinpath(*dep.split('.'))
            if not target.with_suffix('.py').exists() and (not target.is_dir()):
                missing.append([name, dep])
            source_level = levels.get(name.split('.')[1], 99)
            target_level = levels.get(dep.split('.')[1], 99)
            if source_level < target_level and name != 'kbapp.workflows.cli':
                forbidden.append([name, dep])
    try:
        list(graphlib.TopologicalSorter(graph).static_order())
        cycle = []
    except graphlib.CycleError as exc:
        cycle = exc.args[1]
    return {'modules': len(graph), 'cycle': cycle, 'missing': missing, 'upward_dependencies': forbidden, 'ok': not (cycle or missing or forbidden)}

def run(action, options, paths):
    tools = Path(__file__).resolve().parents[2]
    if action == 'architecture':
        report = architecture()
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['ok'] else 1
    if action == 'tests':
        with tempfile.TemporaryDirectory(prefix='kb-tests-') as temp:
            previous = {key: os.environ.get(key) for key in ('KB_BASE', 'KB_DATA_DIR', 'KB_NO_STARTUP_JOBS', 'KB_NO_REFERENCE_WORKER')}
            os.environ.update(KB_BASE=temp, KB_DATA_DIR=str(Path(temp) / '_index'), KB_NO_STARTUP_JOBS='1', KB_NO_REFERENCE_WORKER='1')
            try:
                sys.path.insert(0, str(tools / 'tests'))
                result = unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(str(tools / 'tests')))
                js = subprocess.run(['node', '--test', *[str(p) for p in sorted((tools / 'tests').glob('test_*.cjs'))]], check=False)
                return 0 if result.wasSuccessful() and js.returncode == 0 else 1
            finally:
                for key, value in previous.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value
    names = {'integrity': 'validate_integrity', 'relations': 'validate_relation_review', 'retrieval': 'kb_evaluate', 'attachments': 'doctor_attachments'}
    module = importlib.import_module('kbapp.workflows.tools.' + names[action])
    return module.main(options) or 0
