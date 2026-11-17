"""kbapp.core.text：由职责边界组织的知识库能力。"""
from kbapp.core.files import file_hash

def readable_text(text):
    import unicodedata
    if len(text.strip()) < 30:
        return False
    corrupt = sum((c == '�' or (unicodedata.category(c).startswith('C') and c not in '\n\t\r') for c in text))
    return corrupt / max(1, len(text)) < 0.02

def locate_quote(text, quote, *, join_pdf_words=False):
    """只容许空白及Unicode排版等价；返回来源中的实际字符，绝不补写改述。"""
    import unicodedata
    import re
    if quote in text:
        return quote
    normalized = []
    positions = []
    hyphen_pattern = r'(?<=[a-z]{2})-\s+(?=[a-z]{2})' if join_pdf_words else r'(?<=[a-z]{2})-\s*\n\s*(?=[a-z]{2})'
    line_hyphens = {m.start() for m in re.finditer(hyphen_pattern,text)}
    for index, char in enumerate(text):
        if index in line_hyphens or char == '\xad':
            continue
        for c in unicodedata.normalize('NFKC', char):
            if not c.isspace():
                normalized.append(c)
                positions.append(index)
    needle = re.sub(hyphen_pattern, '', quote).replace('\xad', '')
    needle = ''.join((c for c in unicodedata.normalize('NFKC', needle) if not c.isspace()))
    if not needle:
        return None
    start = ''.join(normalized).find(needle)
    return text[positions[start]:positions[start + len(needle) - 1] + 1] if start >= 0 else None
def sentence_spans(text):
    """按句末标点与换行切句，返回 [(id, start, end)]。"""
    import re
    spans = []
    for match in re.finditer(r'[^.!?。！？\n]+[.!?。！？]?', text or ''):
        if match.group(0).strip():
            spans.append((len(spans), match.start(), match.end()))
    return spans

def locate_span(text, quote):
    """C06：返回摘录在原文中的字符区间与覆盖的句子编号；无法定位时 None。"""
    located = locate_quote(text, quote)
    if not located:
        return None
    start = text.find(located)
    if start < 0:
        return None
    end = start + len(located)
    ids = [sid for sid, left, right in sentence_spans(text) if left < end and right > start]
    return {'start': start, 'end': end, 'sentence_ids': ids}

__all__ = ['readable_text', 'locate_quote', 'sentence_spans', 'locate_span', 'file_hash']
