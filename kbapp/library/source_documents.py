"""Resolve evidence against its original attachment, never a different PDF."""
import json
from pathlib import Path
from kbapp.core.files import source_hash

PROVENANCE_FIELDS = ('document_id', 'sha256', 'document_version', 'pdf_hash')


def resolve_source(store, source, paper=None):
    """Return (path, provenance); an explicit document identity fails closed.

    document_version identifies both PDF bytes and parser version. pdf_hash keeps
    its legacy stat-fingerprint meaning; it is not a content SHA256.
    """
    from kbapp.library.documents import get_document
    from kbapp.library.recognition import pdf_path, pdf_fingerprint
    identity = source.get('document_id')
    if identity:
        document = get_document(store, identity)
        if document['paper_id'] != source.get('paper_id'):
            raise ValueError('证据附件所属论文不一致')
        path = Path(document['path'])
        expected = document['sha256']
        parser = json.loads(document.get('payload') or '{}').get('parser_version')
        version = expected + ':parser:' + str(parser)
        if source.get('document_version') and source['document_version'] != version:
            raise ValueError('证据附件解析版本已变化')
    else:
        paper = paper if paper is not None else store.papers().get(source.get('paper_id'), {})
        path = pdf_path(store, paper) if paper else None
        expected = None
        version = None
    if not path or not path.is_file():
        raise ValueError('原始PDF或证据附件不可回查')
    sha = source_hash(path)
    if expected and sha != expected:
        raise ValueError('证据附件内容版本已变化')
    if any(source.get(k) and source[k] != sha for k in ('sha256', 'content_sha256')):
        raise ValueError('证据附件SHA256不一致')
    stamp = pdf_fingerprint(path)
    legacy_hash = source.get('pdf_hash') or (source.get('ocr_provenance') or {}).get('pdf_hash')
    if legacy_hash and legacy_hash != stamp:
        raise ValueError('证据PDF版本已变化')
    return path, dict(document_id=identity, sha256=sha, document_version=version or sha, pdf_hash=stamp)
