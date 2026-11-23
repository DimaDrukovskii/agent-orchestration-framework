"""Manager workflow for the SQLite installation: new -> in_progress -> completed."""
import asyncio
import time

from src.config import settings
from src.services.sqlite_store import _db, init_db


class RequestConflict(Exception):
    pass


class ServiceRequests:
    def __init__(self):
        if settings.storage_backend != 'sqlite':
            raise ValueError('service_workflow_requires_sqlite')
        self.path = init_db(settings.sqlite_path)

    async def list(self, status: str = 'open', offset: int = 0) -> dict:
        def op():
            with _db(self.path) as conn:
                condition = "r.status!='completed'" if status == 'open' else 'r.status=?'
                params = () if status == 'open' else (status,)
                rows = [dict(row) for row in conn.execute(
                    "SELECT r.*, h.name, h.need, h.budget, h.urgency, "
                    "COALESCE(h.contact_phone, c.caller_phone) contact_phone, h.comment "
                    "FROM service_requests r LEFT JOIN handoff_cards h ON h.call_id=r.call_id "
                    "LEFT JOIN calls c ON c.call_id=r.call_id WHERE " + condition +
                    " ORDER BY r.created_at LIMIT 200 OFFSET ?", (*params, offset),
                )]
                total = conn.execute('SELECT COUNT(*) FROM service_requests r WHERE ' + condition, params).fetchone()[0]
                counts = {s: 0 for s in ('new', 'in_progress', 'completed')}
                counts.update({r['status']: r['n'] for r in conn.execute(
                    'SELECT status, COUNT(*) n FROM service_requests GROUP BY status'
                )})
                return {'items': rows, 'total': total, 'offset': offset, 'counts': counts,
                        'has_more': offset + len(rows) < total}
        return await asyncio.to_thread(op)

    async def detail(self, call_id: str) -> dict:
        def op():
            with _db(self.path) as conn:
                row = conn.execute('SELECT * FROM service_requests WHERE call_id=?', (call_id,)).fetchone()
                if row is None:
                    raise KeyError(call_id)
                call = conn.execute('SELECT transcript FROM calls WHERE call_id=?', (call_id,)).fetchone()
                return {**dict(row), 'transcript': call['transcript'] if call else '',
                        'events': [dict(r) for r in conn.execute(
                            'SELECT status,manager,result,created_at FROM service_request_events WHERE call_id=? ORDER BY id', (call_id,))],
                        'revisions': [dict(r) for r in conn.execute(
                            'SELECT card_json,created_at FROM handoff_revisions WHERE call_id=? ORDER BY id', (call_id,))]}
        return await asyncio.to_thread(op)

    async def transition(self, call_id: str, manager: str, result: str = '') -> dict:
        def op():
            with _db(self.path) as conn:
                conn.execute('BEGIN IMMEDIATE')
                current = conn.execute('SELECT * FROM service_requests WHERE call_id=?', (call_id,)).fetchone()
                if current is None:
                    raise KeyError(call_id)
                if result:
                    # Repeating an acknowledged completion is harmless.
                    if current['status'] == 'completed' and current['assigned_to'] == manager and current['result'] == result:
                        return dict(current)
                    if current['status'] != 'in_progress' or current['assigned_to'] != manager:
                        raise RequestConflict('Завершить заявку может назначенный менеджер после принятия в работу')
                    status = 'completed'
                else:
                    if current['status'] == 'in_progress' and current['assigned_to'] == manager:
                        return dict(current)
                    if current['status'] != 'new':
                        raise RequestConflict('Заявка уже принята или завершена')
                    status = 'in_progress'
                now = time.time()
                conn.execute(
                    'UPDATE service_requests SET status=?, assigned_to=?, result=?, updated_at=? WHERE call_id=?',
                    (status, manager, result or None, now, call_id),
                )
                conn.execute(
                    'INSERT INTO service_request_events (call_id,status,manager,result,created_at) VALUES (?,?,?,?,?)',
                    (call_id, status, manager, result or None, now),
                )
                return dict(conn.execute('SELECT * FROM service_requests WHERE call_id=?', (call_id,)).fetchone())
        return await asyncio.to_thread(op)
