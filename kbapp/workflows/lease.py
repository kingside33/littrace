"""kbapp.workflows.lease：在途单元的租约与心跳（I02）。

认领单元时写入 owner 与租约到期时间；执行期间心跳线程定期续租。
接管条件：原 owner 进程已退出，或租约过期且进程身份不可确认；
仍在执行且租约有效的单元不会被其他进程抢占。
"""
from __future__ import annotations
import threading
from datetime import datetime, timedelta

from kbapp.core.io import TZ, now
from kbapp.core.process import identity

LEASE_SECONDS = 180
HEARTBEAT_SECONDS = 30


def _expiry(seconds=LEASE_SECONDS):
    return (datetime.now(TZ) + timedelta(seconds=seconds)).isoformat()


def acquire(store, unit_id, seconds=LEASE_SECONDS, db=None):
    owner = identity()
    if db is not None:
        db.execute('INSERT OR REPLACE INTO agent_unit_leases VALUES(?,?,?,?)', (unit_id, owner, _expiry(seconds), now()))
    else:
        with store.db() as handle:
            handle.execute('INSERT OR REPLACE INTO agent_unit_leases VALUES(?,?,?,?)', (unit_id, owner, _expiry(seconds), now()))
    return owner


def heartbeat(store, unit_id, owner=None, seconds=LEASE_SECONDS, db=None):
    owner = owner or identity()
    if db is not None:
        db.execute('UPDATE agent_unit_leases SET lease_until=?,updated=? WHERE unit_id=? AND owner=?', (_expiry(seconds), now(), unit_id, owner))
    else:
        with store.db() as handle:
            handle.execute('UPDATE agent_unit_leases SET lease_until=?,updated=? WHERE unit_id=? AND owner=?', (_expiry(seconds), now(), unit_id, owner))


def release(store, unit_id, owner=None, db=None):
    handle = db if db is not None else store.db()
    if db is not None:
        _delete(handle, unit_id, owner)
        return
    with handle as conn:
        _delete(conn, unit_id, owner)

def _delete(db, unit_id, owner):
    if owner:
        db.execute('DELETE FROM agent_unit_leases WHERE unit_id=? AND owner=?', (unit_id, owner))
    else:
        db.execute('DELETE FROM agent_unit_leases WHERE unit_id=?', (unit_id,))


def lease_of(store, unit_id):
    with store.db() as db:
        row = db.execute('SELECT * FROM agent_unit_leases WHERE unit_id=?', (unit_id,)).fetchone()
    return dict(row) if row else None


def takeover_allowed(store, unit_id):
    """租约缺失、过期或 owner 进程已退出时允许接管。"""
    from kbapp.core.process import alive
    lease = lease_of(store, unit_id)
    if not lease:
        return True
    if not alive(lease['owner']):
        return True
    try:
        return datetime.fromisoformat(lease['lease_until']) < datetime.now(TZ)
    except (TypeError, ValueError):
        return True


class Lease:
    """执行期间自动续租；退出时释放。"""

    def __init__(self, store, unit_id, interval=HEARTBEAT_SECONDS):
        self.store = store
        self.unit_id = unit_id
        self.interval = interval
        self.owner = None
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        self.owner = acquire(self.store, self.unit_id)
        self._thread = threading.Thread(target=self._beat, daemon=True, name='unit-lease-' + str(self.unit_id))
        self._thread.start()
        return self

    def _beat(self):
        while not self._stop.wait(self.interval):
            try:
                heartbeat(self.store, self.unit_id, self.owner)
            except Exception:
                return

    def __exit__(self, *exc):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=1)
        release(self.store, self.unit_id, self.owner)
        return False
