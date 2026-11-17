"""kbapp.core.markers：由职责边界组织的知识库能力。"""
from __future__ import annotations
import json
import os
START = '<!-- KB:READING:START -->'
END = '<!-- KB:READING:END -->'
AUTO_START = '<!-- KB:AUTO:START -->'
AUTO_END = '<!-- KB:AUTO:END -->'
# 课题名与阶段名属于使用者私有信息，默认只给占位符；
# 需要真实名称时用 KB_PROJECT / KB_PROJECT_STAGES 注入。
PROJECT = os.environ.get('KB_PROJECT', '当前研究课题')
_STAGES_DEFAULT = {'01': '阶段一', '02': '阶段二', '03': '阶段三', '04': '阶段四', '05': '阶段五', '07': '阶段六'}


def _stages_from_env():
    raw = os.environ.get('KB_PROJECT_STAGES')
    if raw is None or not raw.strip():
        return dict(_STAGES_DEFAULT)
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError('KB_PROJECT_STAGES 必须是 {"方向编号": "阶段名"} 形式的 JSON 对象')
    return {str(key): str(name) for key, name in value.items()}
STAGES = _stages_from_env()
