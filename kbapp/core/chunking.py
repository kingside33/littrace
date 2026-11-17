"""kbapp.core.chunking：由职责边界组织的知识库能力。"""
import re
import kbapp.core.config as C

def est_tokens(s):
    cjk = sum((1 for ch in s if '一' <= ch <= '鿿'))
    return max(1, int(cjk * 1.5 + (len(s) - cjk) / 3))

def split_long(text, limit=None):
    limit = limit or C.CHUNK_MAX_TOKENS
    if est_tokens(text) <= limit:
        return [text]
    pieces = re.split('(?<=[。！？；])|(?<=[.!?;])\\s+(?=[A-Z\\u4e00-\\u9fff])|\\n\\s*\\n', text)
    result, buf = ([], '')
    for piece in pieces:
        if not piece.strip():
            continue
        while est_tokens(piece) > limit:
            if buf:
                result.append(buf)
                buf = ''
            lo, hi = (1, len(piece))
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if est_tokens(piece[:mid]) <= limit:
                    lo = mid
                else:
                    hi = mid - 1
            space = piece.rfind(' ', 0, lo + 1)
            if space > lo * 0.7:
                lo = space + 1
            result.append(piece[:lo])
            piece = piece[lo:]
        candidate = (buf + '\n' + piece).strip()
        if buf and est_tokens(candidate) > limit:
            result.append(buf)
            buf = piece
        else:
            buf = candidate
    if buf:
        result.append(buf)
    return result

def chunk_paras(items, target, overlap):
    """items: [(page, text)] -> [(text, tokens, page_start, page_end)]"""
    chunks = []
    buf, buf_tok = ([], 0)
    items = [(page, part) for page, text in items for part in split_long(text, target)]
    for page, p in items:
        pt = est_tokens(p)
        if buf and buf_tok + pt > target:
            pages = [pg for pg, _ in buf]
            joined = '\n\n'.join((t for _, t in buf))
            chunks.append((joined, est_tokens(joined), min(pages), max(pages)))
            tail, tail_tok = ([], 0)
            for pg, q in reversed(buf):
                qt = est_tokens(q)
                if tail_tok + qt > overlap:
                    budget = overlap - tail_tok
                    lo, hi = (0, len(q))
                    while lo < hi:
                        mid = (lo + hi + 1) // 2
                        if est_tokens(q[-mid:]) <= budget:
                            lo = mid
                        else:
                            hi = mid - 1
                    tail_text = q[-lo:] if lo else ''
                    if lo < len(q) and tail_text and re.match('[A-Za-z0-9]', tail_text) and re.match('[A-Za-z0-9]', q[-lo - 1]):
                        boundary = re.search('\\s', tail_text)
                        if boundary:
                            tail_text = tail_text[boundary.end():]
                    tail_text = tail_text.lstrip()
                    if tail_text:
                        tail.insert(0, (pg, tail_text))
                        tail_tok += est_tokens(tail_text)
                    break
                tail.insert(0, (pg, q))
                tail_tok += qt
            buf, buf_tok = (tail, tail_tok)
        buf.append((page, p))
        buf_tok = est_tokens('\n\n'.join((t for _, t in buf)))
    if buf:
        joined = '\n\n'.join((t for _, t in buf))
        tok = est_tokens(joined)
        if joined.strip():
            pages = [pg for pg, _ in buf]
            chunks.append((joined, tok, min(pages), max(pages)))
    return chunks
