"""kbapp.web.schemas：请求与响应的 Pydantic 契约（H01）。

约定：
- 枚举集中定义（来源类型、证据等级、核验状态、任务状态、检索层级）；
- 可空字段语义：None 表示“未记录/不适用”，空列表表示“已检查且无内容”；
- 列表与详情分离：检索命中只含展示与定位字段，完整对象按需读取；
- 新接口（/api/v2）返回这些模型；旧接口保留一个迁移周期。
"""
from __future__ import annotations
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class SourceKind(str, Enum):
    paper_text = 'paper_text'
    metadata = 'metadata'
    figure = 'figure'
    formula = 'formula'
    zotero_note = 'zotero_note'
    zotero_annotation = 'zotero_annotation'
    obsidian_note = 'obsidian_note'
    ai_analysis = 'ai_analysis'
    external_abstract = 'external_abstract'


class RelationGrade(str, Enum):
    fulltext = 'fulltext'
    abstract = 'abstract'
    title = 'title'
    citation = 'citation'


class VerificationState(str, Enum):
    supported = 'supported'
    contradicted = 'contradicted'
    insufficient = 'insufficient'
    partial = 'partial'
    conflict = 'conflict'
    execution_error = 'execution_error'
    not_requested = 'not_requested'


class TaskState(str, Enum):
    queued = 'queued'
    running = 'running'
    stopping = 'stopping'
    complete = 'complete'
    partial = 'partial'
    blocked = 'blocked'
    failed = 'failed'
    interrupted = 'interrupted'


class SearchLevel(str, Enum):
    passage = 'passage'
    paper = 'paper'


class SearchQuery(BaseModel):
    model_config = ConfigDict(extra='forbid')
    q: str = Field(min_length=1, max_length=2000)
    k: int = Field(12, ge=1, le=50)
    kind: SourceKind | None = None
    direction: str = ''
    year: str = Field('', pattern=r'^[\d,\-，\s]*$')
    project: bool = False
    paper: str = ''
    section: str = ''
    collection: str = ''
    tag: str = ''
    document: str = ''
    level: SearchLevel = SearchLevel.passage
    vectors: bool = True
    wait_vectors: bool = True


class RetrievalInfo(BaseModel):
    model_config = ConfigDict(extra='allow')
    keyword_rank: int | None = None
    vector_rank: int | None = None
    vector_similarity: float | None = None


class SearchHit(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    source_id: str | None = None
    paper_id: str | None = None
    kind: SourceKind
    title: str | None = None
    text: str
    snippet: str | None = None
    score: float | None = None
    url: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    section: str | None = None
    content_group: str | None = None
    equivalent_count: int | None = None
    primary: bool | None = None
    retrieval: RetrievalInfo | None = None


class PaperRef(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    title: str | None = None
    year: int | None = None
    kind: str | None = None
    score: float | None = None


class Coverage(BaseModel):
    model_config = ConfigDict(extra='allow')
    sources: int | None = None
    published_sources: int | None = None
    vectors: int | None = None
    pending: int | None = None
    live_vector_pending: int | None = None


class SearchResponse(BaseModel):
    model_config = ConfigDict(extra='allow')
    query: str
    mode: str
    version: str | None = None
    warning: str = ''
    results: list[SearchHit] = []
    vector_status: str = 'unavailable'
    papers: list[PaperRef] = []
    coverage: Coverage = Coverage()


class ParseIssues(BaseModel):
    model_config = ConfigDict(extra='allow')
    pending: int = 0
    failed: int = 0
    repaired: int = 0
    archived: int = 0
    current_warnings: int = 0


class Invalidation(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    kind: str
    subject: str
    reason: str
    status: str = 'recorded'
    created: str | None = None
    affected: list[str] = []


class StatusResponse(BaseModel):
    model_config = ConfigDict(extra='allow')
    active_version: str | None = None
    papers: int = 0
    external: int = 0
    coverage: dict = {}
    parse_issues: ParseIssues = ParseIssues()
    invalidations: list[Invalidation] = []
    index_health: dict = {}
    pending_approvals: int = 0


class TaskItem(BaseModel):
    model_config = ConfigDict(extra='allow')
    id: str
    source: str
    kind: str
    title: str
    status: TaskState
    created: str | None = None
    updated: str | None = None
    summary: str | None = None
    skipped: list[str] = []
    gaps: list[str] = []


class TaskListResponse(BaseModel):
    model_config = ConfigDict(extra='allow')
    items: list[TaskItem] = []
    selected: TaskItem | None = None
    events: list[dict] = []
    cursor: int = 0
    has_more: bool = False
    active: bool = False


class PaperResponse(BaseModel):
    model_config = ConfigDict(extra='allow')
    paper: dict
    relations: list[dict] = []
    approvals: list[dict] = []
    understanding: dict | None = None
    references: dict | None = None


class ApiMeta(BaseModel):
    api_version: str = 'v2'
    source_kinds: list[str]
    relation_grades: list[str]
    verification_states: list[str]
    task_states: list[str]
    search_levels: list[str]
    nullable_semantics: dict[str, str]
