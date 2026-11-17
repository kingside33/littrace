"""kbapp.core.execution：由职责边界组织的知识库能力。"""
from contextvars import ContextVar
import threading
priority = ContextVar('research_priority', default=100)
research_run = ContextVar('research_run', default='manual')
_held = threading.local()
current_store = ContextVar('current_store', default=None)
