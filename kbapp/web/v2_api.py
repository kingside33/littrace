"""HTTP 路由：v2_api；通过 AppContext 使用业务能力（H01/H02）。

/api/v2 返回 Pydantic 契约；旧 /api/* 保持不变（适配层），
两个版本共用同一业务实现，避免逻辑分叉。
"""
from __future__ import annotations
from fastapi import HTTPException, Query

from kbapp.web.schemas import (ApiMeta, PaperResponse, SearchLevel, SearchQuery, SearchResponse,
                               SourceKind, StatusResponse, TaskListResponse)


def install(app, context, token):
    store = context.store
    search_engine = context.search

    @app.get('/api/v2/meta', response_model=ApiMeta)
    def api_meta():
        from kbapp.web.schemas import RelationGrade, TaskState, VerificationState
        return {'api_version': 'v2',
                'source_kinds': [item.value for item in SourceKind],
                'relation_grades': [item.value for item in RelationGrade],
                'verification_states': [item.value for item in VerificationState],
                'task_states': [item.value for item in TaskState],
                'search_levels': [item.value for item in SearchLevel],
                'nullable_semantics': {'null': '未记录或不适用', 'empty_list': '已检查且无内容', 'missing_field': '该接口不返回此字段'}}

    @app.get('/api/v2/search', response_model=SearchResponse)
    def search_v2(q: str = Query(min_length=1, max_length=2000), k: int = Query(12, ge=1, le=50),
                  kind: SourceKind | None = None, direction: str = '', year: str = '', project: bool = False,
                  paper: str = '', section: str = '', collection: str = '', tag: str = '', document: str = '',
                  level: SearchLevel = SearchLevel.passage, vectors: bool = True, wait_vectors: bool = True):
        query = SearchQuery(q=q, k=k, kind=kind, direction=direction, year=year, project=project, paper=paper,
                            section=section, collection=collection, tag=tag, document=document,
                            level=level, vectors=vectors, wait_vectors=wait_vectors)
        from kbapp.workflows.references import attach_citations
        data = search_engine.search(query.q, query.k, query.kind.value if query.kind else '', query.direction,
                                    query.year, query.project, query.paper, query.section, query.vectors,
                                    wait_vectors=query.wait_vectors, collection=query.collection, tag=query.tag,
                                    document=query.document, level=query.level.value)
        return attach_citations(store, data)

    @app.get('/api/v2/status', response_model=StatusResponse)
    def status_v2():
        from kbapp.workflows.status import status
        return status(store)

    @app.get('/api/v2/tasks', response_model=TaskListResponse)
    def tasks_v2(task_id: str = '', after_event_id: int = 0, limit: int = Query(100, ge=1, le=200)):
        from kbapp.workflows.dashboard import dashboard
        return dashboard(store, task_id, after_event_id, limit)

    @app.get('/api/v2/paper', response_model=PaperResponse)
    def paper_v2(paper: str):
        from kbapp.web.library_api import paper_view
        try:
            return paper_view(store, paper)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
