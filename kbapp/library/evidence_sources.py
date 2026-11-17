"""kbapp.library.evidence_sources：由职责边界组织的知识库能力。"""
from kbapp.core.io import digest

def refresh_sources(store, candidates, allow_personal=False, snapshot=None):
    from kbapp.library.chunk import page_text
    from kbapp.library.recognition import active_sources
    import pymupdf
    papers = snapshot['papers'] if snapshot else store.papers()
    ocr = {s['id']: s for s in (snapshot['live_sources'].values() if snapshot else active_sources(store)) if s.get('ocr_provenance')}
    result = []
    gaps = []
    from kbapp.library.documents import sources as document_sources
    current_documents = {s['id']: s for s in (snapshot['live_sources'].values() if snapshot else document_sources(store)) if s.get('document_id')}
    docs = {}
    try:
        for source in candidates:
            src = dict(source)
            paper = papers.get(src.get('paper_id'), {})
            personal = src.get('kind') in {'obsidian_note', 'zotero_note', 'zotero_annotation'} and allow_personal
            if src.get('derived') or (src.get('kind') not in {'paper_text', 'metadata', 'external_abstract', 'figure', 'formula'} and (not personal)):
                continue
            if src.get('kind') in {'paper_text', 'figure', 'formula'}:
                from kbapp.library.source_documents import resolve_source
                try:
                    source_path, provenance = resolve_source(store, src, paper)
                except (ValueError, OSError) as exc:
                    gaps.append(str(exc) + '：' + src.get('title', ''))
                    continue
                src.update(provenance)
            if personal:
                src['evidence_level'] = 'legacy_mixed' if src.get('origin') == 'legacy_mixed' else 'personal'
            elif src['kind'] in {'metadata', 'external_abstract'}:
                src['text'] = '\n'.join((x for x in [paper.get('title'), paper.get('abstract')] if x))
                src['evidence_level'] = 'abstract' if paper.get('abstract') else 'title'
                if not paper.get('abstract'):
                    continue
            elif src.get('document_id') or (src.get('parent_id') or src.get('retrieved_source_id') or src['id']) in ocr:
                parent_id = src.get('parent_id')
                lookup_id = parent_id or src.get('retrieved_source_id') or src['id']
                current = ocr.get(lookup_id) or current_documents.get(lookup_id)
                if not current or current.get('document_id') != src.get('document_id'):
                    gaps.append('附件版本或页面内容已变化：' + src.get('title', ''))
                    continue
                if current.get('kind') in {'figure', 'formula'} and current.get('review_status') != 'confirmed':
                    gaps.append('公式或表格识别待人工复核：' + src.get('title', ''))
                    continue
                if parent_id:
                    from kbapp.core.text import locate_quote
                    piece = locate_quote(current.get('text', ''), src.get('text', ''))
                    if src.get('source_text_hash') != digest(current.get('text', '')) or not piece:
                        gaps.append('来源分片的父文本已变化：' + src.get('title', ''))
                        continue
                    child = {k: src[k] for k in ('id', 'source_id', 'text', 'text_hash', 'parent_id', 'source_text_hash', 'part_index', 'part_count', 'chunk_policy', 'page_start', 'page_end', 'page') if k in src}
                    src.update(current)
                    src.update(child)
                else:
                    src.update(current)
                src.update(provenance)
                src['evidence_level'] = 'fulltext'
            else:
                path = source_path
                if not path or not path.is_file():
                    gaps.append('原始PDF不可回查：' + src.get('title', ''))
                    continue
                pid = paper['id']
                if pid not in docs:
                    docs[pid] = pymupdf.open(path)
                doc = docs[pid]
                start = src.get('page_start', src.get('page'))
                end = src.get('page_end', start)
                if not isinstance(start, int) or not isinstance(end, int) or (not 1 <= start <= end <= len(doc)):
                    gaps.append('证据页码不可回查：' + src.get('title', ''))
                    continue
                src['page_texts'] = [{'page': n, 'text': page_text(doc[n - 1])} for n in range(start, min(end, start + 2) + 1)]
                original = '\n\n'.join((page['text'] for page in src['page_texts']))
                from kbapp.core.text import readable_text
                if not readable_text(original):
                    gaps.append('原页面文字质量不足，需GLM-OCR识别：' + src.get('title', ''))
                    continue
                from kbapp.core.text import locate_quote
                located = locate_quote(original, src.get('text', ''))
                if src.get('kind') == 'paper_text' and located:
                    src['text'] = located
                elif src.get('kind') == 'paper_text':
                    src['text'] = original
                elif not located:
                    gaps.append('图表/公式识别尚未独立回查：' + src.get('title', ''))
                    continue
                src['evidence_level'] = 'fulltext'
                src['page_start'] = start
                src['page_end'] = min(end, start + 2)
            if not src.get('text', '').strip():
                continue
            src['retrieved_source_id'] = src['id']
            src['id'] = src['id'] + ':live:' + digest(src['text'])[:12]
            result.append(src)
    finally:
        for doc in docs.values():
            doc.close()
    return (result, gaps)
