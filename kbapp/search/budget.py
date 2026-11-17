"""Shared corpus/live-source budget; IDs and model namespaces are independent.

The token count is an estimate, not the model tokenizer. Requests must also use
truncate=False; a server rejection is recorded per item, never as full coverage.
"""
from kbapp.core.chunking import split_long
from kbapp.core.io import digest
from kbapp.integrations.embedding import POLICY_VERSION, embedding_policy, within_budget
import kbapp.core.config as C


def split_source(source, legacy_ids=False):
    """Return deterministic search rows, preserving short existing source IDs.

    legacy_ids is ONLY for build_corpus's historical metadata/note/extract path.
    Callers holding existing corpus/document/OCR records leave it False. Every
    child retains source_id, document_id, parent_id, source_text_hash and pages;
    text_hash describes only the child's input, not the entire parent.
    """
    text = source.get('text') or ''
    if legacy_ids:
        text = text.strip()
    if not text.strip():
        return []
    pieces = []
    for piece in split_long(text, C.CHUNK_MAX_TOKENS):
        # split_long prefers a nearby space, occasionally one character beyond
        # its estimate. Enforce the actual boundary without dropping that tail.
        while not within_budget(piece):
            low, high = 1, len(piece)
            while low < high:
                middle = (low + high + 1) // 2
                if within_budget(piece[:middle]):
                    low = middle
                else:
                    high = middle - 1
            pieces.append(piece[:low])
            piece = piece[low:]
        if piece.strip():
            pieces.append(piece)
    result = []
    counts = {}
    for n, piece in enumerate(pieces):
        hashed = digest(piece)
        record = dict(source, text=piece, text_hash=hashed)
        record.setdefault('source_id', source['id'])
        if legacy_ids:
            identity = source['id'] + ':' + hashed[:16]
            count = counts.get(identity, 0)
            counts[identity] = count + 1
            record['id'] = identity + (f':repeat-{count}' if count else '')
        elif len(pieces) > 1:
            record['id'] = f"{source['id']}:{POLICY_VERSION}:{n}:{hashed[:16]}"
        if len(pieces) > 1:
            record.update(parent_id=source['id'], source_text_hash=digest(text),
                          part_index=n, part_count=len(pieces))
        record['chunk_policy'] = embedding_policy()
        result.append(record)
    return result


def split_sources(sources):
    """Public adapter for live/evidence refresh. Do not split IDs by punctuation."""
    return [part for source in sources for part in split_source(source)]
