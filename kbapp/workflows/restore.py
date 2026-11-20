"""kbapp.workflows.restore：按选定的历史备份只读校验并恢复 SQLite 到新的隔离数据目录（issue #101）。

恢复全程 raw sqlite3 与文件操作，绝不构造 Store：Store 构造即建表并迁移
（kbapp/storage/store.py），db() 连接固定开 WAL——那会运行 schema 升级，
违反「恢复过程不得运行 schema 升级或后台任务」。备份产物可能带 WAL 模式文件头，
校验一律以 ?mode=ro 打开后用 backup() 拷入内存再操作（与
kbapp/integrations/zotero.py 的 ro_connect 同款；不 import zotero 模块本身，
它在 import 时执行模块级 Paths.from_base()）。其中备份文件与已落位的目标文件
是校验期间的静态快照，另加 immutable=1 打开，避免 SQLite 为 WAL 头文件在
同目录创建空 sidecar（见 _open_ro_memory）。绝不以可写连接打开目标库做验证，
绝不写回备份源。

恢复范围仅为 SQLite 主库文件：PDF/附件文件（files/）、Zotero 原件
（zotero.sqlite 与 storage/）、外部索引与缓存（indexes/、cache/、cache_root）、
Obsidian 笔记都不在范围内，报告明示。隔离目标是备份快照的落位，
不是可直接继续工作的完整安装。
"""
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path

from kbapp.core.config import DEFAULTS
from kbapp.core.io import read_json
from kbapp.core.paths import Paths
from kbapp.workflows.backup import counts

DB_NAME = 'workbench.sqlite3'
MANIFEST_NAME = 'manifest.json'
# 与 backup.create_backup 写入 manifest 的 8 个计数字段一致（kbapp/workflows/backup.py）。
COUNT_KEYS = ('papers', 'relations', 'relations_confirmed', 'relations_manual', 'approvals', 'usage_records', 'verification_budgets', 'agent_runs')
SCOPE_NOT_INCLUDED = ('PDF 与附件文件（files/）', 'Zotero 原件（zotero.sqlite 与 storage/）', '外部索引与缓存（indexes/、cache/、cache_root）', 'Obsidian 笔记')


class RestoreError(ValueError):
    """恢复失败；stage ∈ {'locate','target','validate','copy','verify'}，供 CLI 报告。"""

    def __init__(self, stage, message):
        super().__init__(message)
        self.stage = stage


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _open_ro_memory(path, immutable=False):
    """以 ?mode=ro 打开 SQLite 文件并用 backup() 拷入内存，返回内存连接。

    与 kbapp/integrations/zotero.py 的 ro_connect 同款，但不 import zotero 模块
    （它在 import 时执行模块级 Paths.from_base()，恢复入口不应依赖环境默认 base）。

    immutable=True 只用于校验期间确定无人写入的静态快照文件（备份文件与已落位的
    目标文件）：纯 ro 打开带 WAL 文件头的库时，SQLite 会在同目录创建空的
    -wal/-shm 协调文件（实测 0 字节 -wal），只读连接关闭后删不掉——既违背
    「绝不写回备份源」，也会让 _locate 的 sidecar 检查把上一轮恢复自己留下的空
    sidecar 误判为复制中断。immutable=1 明示「连接期间此文件不变」，不创建任何
    sidecar。当前活动库绝不能用 immutable：有并发写入时必须读到 WAL 内容，
    保持纯 ro（与 backup.py、ro_connect 同款；活动库目录出现空 sidecar 是仓库
    既有副作用，0 字节不含数据）。
    """
    query = '?mode=ro' + ('&immutable=1' if immutable else '')
    uri = Path(path).resolve().as_uri() + query
    source = sqlite3.connect(uri, uri=True, timeout=10)
    conn = sqlite3.connect(':memory:')
    try:
        source.backup(conn)
    finally:
        source.close()
    return conn


def _real(path):
    """resolve + normcase 后的真实路径：别名、符号链接与大小写差异折算成同一形式。"""
    return os.path.normcase(str(Path(path).resolve()))


def _overlaps(a, b):
    """两个真实路径互相相等或存在父子重叠。"""
    return a == b or a.startswith(b + os.sep) or b.startswith(a + os.sep)


def _missing_dirs(state_dir):
    """state 目录及其所有尚不存在的祖先（可能含 target 本身），由深到浅排序。

    复制前按浅到深创建；失败回滚时按同一清单 rmdir，只删本运行新建且已空的目录。
    """
    missing = []
    current = Path(state_dir)
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            break
        current = parent
    return missing


def _rollback(files, dirs):
    """失败清理：删除本运行创建的暂存/落位文件；本运行新建且已空的目录逐层删除。"""
    for path in files:
        try:
            path.unlink()
        except OSError:
            pass
    for directory in dirs:
        try:
            directory.rmdir()
        except OSError:
            pass


def _locate(backup_root):
    """按用户给出的备份目录定位两个文件；manifest 里的历史绝对路径不作为读取源。"""
    if not backup_root.is_dir():
        raise RestoreError('locate', f'备份目录不存在：{backup_root}')
    backup_db = backup_root / DB_NAME
    manifest_path = backup_root / MANIFEST_NAME
    missing = [name for name, path in ((DB_NAME, backup_db), (MANIFEST_NAME, manifest_path)) if not path.is_file()]
    if missing:
        raise RestoreError('locate', f'备份目录缺少文件：{"、".join(missing)}；当前备份格式为 {DB_NAME} + {MANIFEST_NAME}')
    sidecars = [DB_NAME + suffix for suffix in ('-wal', '-shm') if (backup_root / (DB_NAME + suffix)).exists()]
    if sidecars:
        raise RestoreError('locate', f'备份目录出现 SQLite sidecar（{"、".join(sidecars)}）：当前备份格式是自包含单文件，sidecar 说明复制中断或来源异常，拒绝恢复')
    return backup_db, manifest_path


def _zotero_dirs(paths):
    """Zotero 目录候选：settings 的 zotero_dir（DEFAULTS 含 KB_ZOTERO_DIR 环境
    默认值）、state/settings.json 与环境变量 KB_ZOTERO_DIR。

    不构造 Store：Store 构造会建表并迁移；settings.json 只有 zotero_dir 需要读取，
    直接 read_json 即可（api_key/openalex_key 之外的配置均为明文）。
    """
    configured = str(read_json(paths.state / 'settings.json', {}).get('zotero_dir') or '')
    candidates = {os.environ.get('KB_ZOTERO_DIR', ''), str(DEFAULTS.get('zotero_dir') or ''), configured}
    return [item for item in candidates if item]


def _check_target(paths, backup_root, target_dir):
    """目标准入：只校验不创建；通过时返回 Paths.for_data(target)。

    拒绝清单：与当前活动数据目录、备份源目录、Zotero 目录互相相等或父子重叠；
    目标下已存在 state/workbench.sqlite3 或 zotero.sqlite（可能覆盖现有数据）。
    目标允许不存在，或存在但无上述数据文件。
    """
    target = Path(target_dir)
    if target.exists() and not target.is_dir():
        raise RestoreError('target', f'恢复目标已存在且不是目录：{target_dir}')
    target_real = _real(target)
    conflicts = []
    if _overlaps(target_real, _real(paths.data)):
        conflicts.append('当前活动数据目录')
    if _overlaps(target_real, _real(backup_root)):
        conflicts.append('备份源目录')
    for zotero in _zotero_dirs(paths):
        if _overlaps(target_real, _real(zotero)):
            conflicts.append('Zotero 目录')
            break
    if conflicts:
        raise RestoreError('target', f'恢复目标与{"、".join(conflicts)}重合或互为父子，拒绝写入')
    occupied = [name for name in ('state/' + DB_NAME, 'zotero.sqlite') if (target / name).is_file()]
    if occupied:
        raise RestoreError('target', f'恢复目标下已存在数据文件（{"、".join(occupied)}），为避免覆盖现有数据而拒绝')
    return Paths.for_data(target)


def _validate(backup_db, manifest_path):
    """阶段 validate：只读校验备份，并核对 manifest 有值字段。

    缺字段或值为 None（backup.py 对读取失败字段的既有形态）→ 标记「未记录」继续；
    有值且与实际计数/完整性矛盾 → 失败。size_mb 为 0.1MB 舍入值，不做精确比较。
    """
    source_sha256 = _file_sha256(backup_db)
    try:
        conn = _open_ro_memory(backup_db, immutable=True)
    except sqlite3.Error as exc:
        raise RestoreError('validate', f'备份文件无法作为 SQLite 只读打开：{exc}') from exc
    try:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        actual = counts(conn)
    except sqlite3.Error as exc:
        raise RestoreError('validate', f'备份库损坏或无法读取：{exc}') from exc
    finally:
        conn.close()
    if integrity != 'ok':
        raise RestoreError('validate', f'备份库完整性检查失败：{integrity}')
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise RestoreError('validate', f'manifest.json 无法解析：{exc}') from exc
    if not isinstance(manifest, dict):
        raise RestoreError('validate', 'manifest.json 内容不是 JSON 对象')
    unrecorded = []
    mismatches = []
    recorded = manifest.get('counts')
    if not isinstance(recorded, dict):
        recorded = {}
    for key in COUNT_KEYS:
        value = recorded.get(key)
        if value is None:
            unrecorded.append('counts.' + key)
        elif value != actual[key]:
            mismatches.append(f'counts.{key}: manifest 记录 {value!r}，实际 {actual[key]!r}')
    recorded_integrity = manifest.get('integrity')
    if recorded_integrity is None:
        unrecorded.append('integrity')
    elif recorded_integrity != 'ok':
        mismatches.append(f'integrity: manifest 记录 {recorded_integrity!r}，实际 ok')
    if mismatches:
        raise RestoreError('validate', '备份与 manifest 记录矛盾：' + '；'.join(mismatches))
    return {'sha256': source_sha256, 'counts': actual, 'unrecorded': unrecorded,
            'manifest_created': manifest.get('created'), 'manifest_label': manifest.get('label')}


def _verify(final_db, expected):
    """阶段 verify：独立重新只读打开目标库复核；完整性与计数与 validate 一致才算成功。"""
    try:
        conn = _open_ro_memory(final_db, immutable=True)
    except sqlite3.Error as exc:
        raise RestoreError('verify', f'目标库无法作为 SQLite 只读打开：{exc}') from exc
    try:
        integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
        actual = counts(conn)
    except sqlite3.Error as exc:
        raise RestoreError('verify', f'目标库读取失败：{exc}') from exc
    finally:
        conn.close()
    if integrity != 'ok':
        raise RestoreError('verify', f'目标库完整性检查失败：{integrity}')
    differing = [key for key in COUNT_KEYS if actual[key] != expected[key]]
    if differing:
        raise RestoreError('verify', f'目标库计数与校验阶段不一致：{differing}')
    return actual


def _active_db_snapshot(paths):
    """当前库只读计数快照；库不存在或无法只读读取时计数为 None（如实标注，不猜）。"""
    if not paths.database.is_file():
        return {'existed': False, 'counts': None}
    try:
        conn = _open_ro_memory(paths.database)
    except sqlite3.Error:
        return {'existed': True, 'counts': None}
    try:
        return {'existed': True, 'counts': counts(conn)}
    except sqlite3.Error:
        return {'existed': True, 'counts': None}
    finally:
        conn.close()


def _active_db_report(before, after):
    """当前库一致性证明段；并发写入导致的差异与不可读取都如实写入报告，不谎报成功。"""
    if before['counts'] is None or after['counts'] is None or not (before['existed'] and after['existed']):
        return {'existed': bool(before['existed'] and after['existed']), 'unchanged': None,
                'counts_before': before['counts'], 'counts_after': after['counts'],
                'note': '当前库不存在或无法只读读取计数，未纳入一致性证明'}
    unchanged = before['counts'] == after['counts']
    note = '' if unchanged else '恢复期间当前库计数发生变化（并发写入），如实报告'
    return {'existed': True, 'unchanged': unchanged,
            'counts_before': before['counts'], 'counts_after': after['counts'], 'note': note}


def restore_backup(paths, backup_dir, target_dir):
    """按选定备份只读校验并恢复 SQLite 到新的隔离数据目录，返回报告 dict。

    任一阶段失败抛 RestoreError（stage 标明阶段），并清理本运行创建的暂存文件
    与新建的空目录，不留下看似成功的恢复目标。全程不经 Store 打开任何库，
    目标写路径经 Paths.for_data 落位。
    """
    backup_root = Path(backup_dir)
    # 阶段 locate：定位备份的两个文件；manifest 历史绝对路径只作元数据。
    backup_db, manifest_path = _locate(backup_root)
    active_before = _active_db_snapshot(paths)
    # 阶段 target：目标准入，只校验不创建。
    target_paths = _check_target(paths, backup_root, target_dir)
    state_dir = target_paths.state
    final_db = state_dir / DB_NAME
    staging = state_dir / f'{DB_NAME}.staging-{os.getpid()}'
    # 阶段 validate：只读校验备份并核对 manifest 有值字段。
    validated = _validate(backup_db, manifest_path)
    created_dirs = _missing_dirs(state_dir)
    created_files = []
    kept_existing = []
    try:
        # 阶段 copy：建最小布局（仅 state/），暂存文件 + fsync + 原子落位；已有文件保留原样。
        for directory in reversed(created_dirs):
            directory.mkdir(parents=True, exist_ok=True)
        if final_db.exists():
            kept_existing.append('state/' + DB_NAME)
        else:
            created_files.append(staging)
            try:
                shutil.copyfile(backup_db, staging)
                with open(staging, 'rb+') as fh:
                    os.fsync(fh.fileno())
                os.replace(staging, final_db)
            except Exception as exc:
                raise RestoreError('copy', f'备份复制失败：{exc}') from exc
            created_files.remove(staging)
            created_files.append(final_db)
        # 阶段 verify：独立复核目标库。
        verified_counts = _verify(final_db, validated['counts'])
        # unchanged 证明：备份文件 sha256 前后一致，否则恢复来源已不可信。
        sha_after = _file_sha256(backup_db)
        if sha_after != validated['sha256']:
            raise RestoreError('verify', '备份文件在恢复过程中发生变化，无法证明恢复自选定的历史备份')
    except BaseException:
        _rollback(created_files, created_dirs)
        raise
    # 阶段 unchanged：当前库只读计数恢复前后一致性；差异与不可读取都如实进报告。
    active_after = _active_db_snapshot(paths)
    return {
        'ok': True,
        'scope': {'sqlite_only': True, 'not_included': list(SCOPE_NOT_INCLUDED),
                  'note': '恢复范围仅为 SQLite 主库文件；隔离目标是备份快照的落位，不是可直接继续工作的完整安装'},
        'source': {'backup_dir': str(backup_dir), 'sha256': validated['sha256'],
                   'sha256_after_restore': sha_after, 'unchanged': True},
        'manifest': {'created': validated['manifest_created'], 'label': validated['manifest_label'],
                     'unrecorded': validated['unrecorded'], 'historical_paths_metadata_only': True},
        'counts': verified_counts,
        'target': {'dir': str(target_dir), 'restored_database': str(final_db), 'kept_existing': kept_existing},
        'verify': {'integrity': 'ok', 'counts_match_validate': True},
        'active_db': _active_db_report(active_before, active_after),
    }
