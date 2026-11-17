"""kbapp.core.metadata：由职责边界组织的知识库能力。"""
import re
from urllib.parse import unquote

def normalize_doi(value):
    value = unquote(str(value or '')).strip().lower()
    value = re.sub('^(?:https?://(?:dx\\.)?doi\\.org/|doi\\s*:\\s*)', '', value).rstrip('.,;')
    return value if re.fullmatch('10\\.\\d{4,9}/\\S+', value) else ''
