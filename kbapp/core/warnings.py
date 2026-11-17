"""kbapp.core.warnings：来源警告按发布轮次分区，历史故障不再与当前待办混在一起（F12/D10）。"""
from __future__ import annotations
from kbapp.core.io import now

ARCHIVE_LIMIT = 400


def publish_warnings(previous, current):
    """返回 (当前警告, 归档警告)。

    只有本轮发布再次产生的警告才保留在 coverage.warnings；
    上一轮存在而本轮未再出现的警告移入 warnings_archive，
    归档记录保留原文、时间与原因，可回查但不计入待办数量。
    """
    current = list(dict.fromkeys(current or []))
    archive = list(previous.get('warnings_archive', [])) if isinstance(previous, dict) else []
    known = {entry.get('warning') for entry in archive if isinstance(entry, dict)}
    for warning in (previous.get('warnings', []) if isinstance(previous, dict) else []):
        if warning not in current and warning not in known:
            archive.append({'warning': warning, 'archived_at': now(), 'reason': '最近一次发布未再出现'})
    return current, archive[-ARCHIVE_LIMIT:]


def migrate_existing(previous, reason):
    """一次性迁移：把历史累积的平铺警告整体归档，当前警告清空。"""
    warnings = previous.get('warnings', []) if isinstance(previous, dict) else []
    archive = list(previous.get('warnings_archive', []))
    known = {entry.get('warning') for entry in archive if isinstance(entry, dict)}
    for warning in warnings:
        if warning not in known:
            archive.append({'warning': warning, 'archived_at': now(), 'reason': reason})
    return [], archive[-ARCHIVE_LIMIT:]
