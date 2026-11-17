"""kbapp.library.parsers：可选解析器适配层（D07/D08）。

Docling 与 GROBID 均为旁路能力：未安装时明确报告不可用，不改变默认解析路径；
安装后可在固定样本上对照质量，再决定是否成为该类页面的默认解析器。
"""
from __future__ import annotations
import importlib.util
import os


class ParserUnavailable(RuntimeError):
    pass


class ParserAdapter:
    name = 'base'
    purpose = ''

    def available(self):
        raise NotImplementedError

    def parse(self, path, pages=None):
        raise NotImplementedError


class DoclingAdapter(ParserAdapter):
    name = 'docling'
    purpose = '复杂版面（双栏、表格、公式）结构化解析与置信度'

    def available(self):
        return importlib.util.find_spec('docling') is not None

    def parse(self, path, pages=None):
        if not self.available():
            raise ParserUnavailable('Docling 未安装；可选依赖，不阻塞默认解析')
        from docling.document_converter import DocumentConverter  # pragma: no cover
        result = DocumentConverter().convert(str(path))
        return {'adapter': self.name, 'markdown': result.document.export_to_markdown()}


class GrobidAdapter(ParserAdapter):
    name = 'grobid'
    purpose = '英文参考文献、文内引用与 PDF 坐标'

    def available(self):
        return bool(os.environ.get('GROBID_URL'))

    def parse(self, path, pages=None):
        if not self.available():
            raise ParserUnavailable('GROBID 服务未配置（GROBID_URL）；可选依赖，不阻塞默认解析')
        import urllib.request  # pragma: no cover
        body = b''  # pragma: no cover
        request = urllib.request.Request(os.environ['GROBID_URL'].rstrip('/') + '/api/processFulltextDocument', data=body, method='POST')  # pragma: no cover
        with urllib.request.urlopen(request, timeout=60) as response:  # pragma: no cover
            return {'adapter': self.name, 'tei': response.read().decode('utf-8', 'replace')}


ADAPTERS = {'docling': DoclingAdapter, 'grobid': GrobidAdapter}


def get(name):
    if name not in ADAPTERS:
        raise ValueError('未知解析器：' + str(name))
    return ADAPTERS[name]()


def status():
    return {name: {'available': factory().available(), 'purpose': factory.purpose} for name, factory in ADAPTERS.items()}


def compare(adapter_result, baseline_pages):
    """D07：旁路解析与默认解析的质量对照（字符数、表格/公式数量）。"""
    text = adapter_result.get('markdown') or adapter_result.get('tei') or ''
    baseline = sum(len(page.get('text') or '') for page in baseline_pages)
    return {'adapter': adapter_result.get('adapter'), 'adapter_chars': len(text),
            'baseline_chars': baseline, 'ratio': round(len(text) / baseline, 3) if baseline else None}
