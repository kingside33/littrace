"""kbapp.library.generated：由职责边界组织的知识库能力。"""
from pathlib import Path
from kbapp.library.sources import managed, repair_links
from kbapp.core.io import atomic

def write_generated(path, text):
    path = Path(path)
    old = path.read_text(encoding='utf-8') if path.exists() else ''
    atomic(path, repair_links(managed(old, text)))
