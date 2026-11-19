"""kbapp.core.config：由职责边界组织的知识库能力。"""
from __future__ import annotations
from kbapp.core.paths import Paths
import json
import os
BASE = str(Paths.from_base().base)
KINDS = {'cites', 'inherits', 'improves', 'combines', 'transfers', 'compares', 'similar', 'parallel', 'contradicts'}
DEFAULTS = {'api_base': '', 'cloud_proxy': '', 'model': '', 'api_key': '', 'openalex_key': '', 'input_price': 0.0, 'output_price': 0.0, 'daily_budget': 0.0, 'cloud_enabled': False, 'daily_time': '09:00', 'network_depth': 2, 'budget_mode': 'limited', 'external_requests_per_run': 100, 'cloud_requests_per_run': 12, 'ocr_pages_per_run': 30, 'ocr_regions_per_run': 30, 'local_text_model': 'gemma4:e4b', 'local_text_context': 4096, 'relation_extractor': 'cloud', 'zotero_dir': os.environ.get('KB_ZOTERO_DIR', ''), 'cache_dir': os.environ.get('KB_CACHE_DIR', ''), 'port': 8765}
BASE = str(Paths.from_base().base)
LIT_DIR = '_知识库/10_文献'
DIR_DIR = '_知识库/20_方向'
TEMPLATE_REL = '_知识库/笔记模板/文献卡片模板.md'
INDEX_DIR = '_index'
ATTACH_DIR = '_知识库/_attachments'
CHUNKER_VERSION = 5
CHUNK_TARGET_TOKENS = 512
CHUNK_MAX_TOKENS = 768
CHUNK_OVERLAP_TOKENS = 64
EMBED_MODEL = 'qwen3-embedding:8b'
EMBED_DIMS = 1024
OLLAMA = 'http://127.0.0.1:11434'
GLM_OCR_URL = os.environ.get('GLM_OCR_URL', 'http://127.0.0.1:11434')
GLM_OCR_MODEL = os.environ.get('GLM_OCR_MODEL', 'glm-ocr:latest')
GLM_OCR_API = os.environ.get('GLM_OCR_API', 'ollama')
FIGURE_VERSION = 1
FIGURE_MAX = 12
FORMULA_VERSION = 1
FORMULA_MAX = 30
REF_VERSION = 1
CITE_GRAPH_VERSION = 1
EVOLUTION_MIN_LIB_CITES = 2
OPENALEX_MAILTO = os.environ.get('OPENALEX_MAILTO', '')
OPENALEX_API_KEY = os.environ.get('OPENALEX_API_KEY', '')


def env_json(name, default):
    """读取 JSON 环境变量；未设置或为空时返回默认值。"""
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return json.loads(raw)
    except ValueError as exc:
        raise ValueError(f'{name} 必须是合法 JSON: {exc}') from exc


# 方向编号是稳定的契约，文件夹名与显示名只是占位符。个人课题的目录结构、
# 章节名与集合树通过环境变量注入，不进入公开仓库的默认值：
#   KB_DIRECTIONS=[[编号, 文件夹名, 显示名, 说明], ...]
DIRECTIONS = [
    ('00', '00_背景', '背景与综述', '领域背景、综述、发展脉络与需求来源'),
    ('01', '01_研究方向一', '研究方向一', '研究方向一的范围、问题与相关工作'),
    ('02', '02_研究方向二', '研究方向二', '研究方向二的范围、问题与相关工作'),
    ('03', '03_研究方向三', '研究方向三', '研究方向三的范围、问题与相关工作'),
    ('04', '04_研究方向四', '研究方向四', '研究方向四的范围、问题与相关工作'),
    ('05', '05_研究方向五', '研究方向五', '研究方向五的范围、问题与相关工作'),
    ('06', '06_方法与理论', '方法与理论', '方法、算法、理论与优化基础'),
    ('07', '07_仿真与实验', '仿真与实验', '仿真工具、实验设计与验证'),
    ('08', '08_数据与感知', '数据与感知', '数据来源、感知与信息处理'),
    ('09', '09_其他与交叉', '其他与交叉', '外围支撑、交叉方向与其他内容'),
    ('90', '90_待分类', '待分类', '元数据不足或方向待人工确认'),
]
_direction_rows = env_json('KB_DIRECTIONS', None)
if _direction_rows is not None:
    if not isinstance(_direction_rows, list) or any((not isinstance(row, (list, tuple)) or len(row) != 4 for row in _direction_rows)):
        raise ValueError('KB_DIRECTIONS 必须是 [[编号, 文件夹名, 显示名, 说明], ...] 形式的 JSON 数组')
    DIRECTIONS = [tuple((str(value) for value in row)) for row in _direction_rows]
DIR_ORDER = [row[0] for row in DIRECTIONS]
DIR_NAME = {row[0]: row[1] for row in DIRECTIONS}
DIR_TITLE = {row[0]: row[2] for row in DIRECTIONS}
DIR_DESC = {row[0]: row[3] for row in DIRECTIONS}
EVOLUTION_DIRECTIONS = env_json('KB_EVOLUTION_DIRECTIONS', [DIR_NAME[d] for d in DIR_ORDER if d not in ('00', '90')])
if not isinstance(EVOLUTION_DIRECTIONS, list):
    raise ValueError('KB_EVOLUTION_DIRECTIONS 必须是 JSON 数组')
QUERY_INSTRUCTION = os.environ.get('KB_QUERY_INSTRUCTION', 'Instruct: Given a research question, retrieve passages from academic papers that answer it.\nQuery: ')
FIG_QUERY_INSTRUCTION = os.environ.get('KB_FIG_QUERY_INSTRUCTION', 'Instruct: Given a research question, retrieve figure or table captions from academic papers that answer it.\nQuery: ')
FORMULA_QUERY_INSTRUCTION = os.environ.get('KB_FORMULA_QUERY_INSTRUCTION', 'Instruct: Given a research question, retrieve equations and the surrounding formalization that answer it.\nQuery: ')

def server_base(port=None):
    """L08：服务地址取自配置端口，避免写死 8765。"""
    return 'http://127.0.0.1:' + str(port or DEFAULTS['port'])

def index_dir(base=BASE):
    return os.environ.get('KB_INDEX_DIR') or str(Paths.from_base(base).active_index())

def vec_dir(base=BASE):
    return os.path.join(index_dir(base), 'vec')

def figures_jsonl(base=BASE):
    return os.path.join(index_dir(base), 'figures.jsonl')

def formulas_jsonl(base=BASE):
    return os.path.join(index_dir(base), 'formulas.jsonl')

def references_jsonl(base=BASE):
    return os.path.join(index_dir(base), 'references.jsonl')

def citation_dir(base=BASE):
    return str(Paths.from_base(base).exports / 'citation')

def openalex_dir(base=BASE):
    return str(Paths.from_base(base).cache / 'openalex')

def figures_root(base=BASE):
    return os.path.join(base, ATTACH_DIR, 'figures')

def equations_root(base=BASE):
    return os.path.join(base, ATTACH_DIR, 'equations')
