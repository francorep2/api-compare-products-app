"""Persistencia de las comparaciones (tabla `comparison_tasks`): estado, resultado e historial."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, List, Optional

from app.db import SessionLocal
from app.models.comparison import ComparisonTask

log = logging.getLogger("task_db")

RETENTION = datetime.timedelta(days=90)
MAX_PER_USER = 100  # historial: se conservan las últimas N comparaciones por usuario


def create_task(task_id: str, user_id: str, list_id: Optional[int] = None, title: Optional[str] = None) -> None:
    uid = str(user_id)
    with SessionLocal() as db:
        # limpieza oportunista: vencidas y excedentes de este usuario
        cutoff = datetime.datetime.now(datetime.timezone.utc) - RETENTION
        db.query(ComparisonTask).filter(ComparisonTask.created_at < cutoff).delete()
        old = (
            db.query(ComparisonTask.id)
            .filter(ComparisonTask.user_id == uid)
            .order_by(ComparisonTask.created_at.desc())
            .offset(MAX_PER_USER - 1)
            .all()
        )
        if old:
            db.query(ComparisonTask).filter(ComparisonTask.id.in_([r[0] for r in old])).delete(synchronize_session=False)
        db.add(ComparisonTask(id=task_id, user_id=uid, status="processing", list_id=list_id, title=(title or None),
                              created_at=datetime.datetime.now(datetime.timezone.utc)))
        db.commit()


def finish_task(task_id: str, data: Dict[str, Any]) -> None:
    with SessionLocal() as db:
        row = db.get(ComparisonTask, task_id)
        if row is None:
            log.warning("tarea %s no existe al terminar", task_id)
            return
        row.status, row.data = "completed", data
        db.commit()


def get_task(task_id: str, user_id: str) -> Optional[Dict[str, Any]]:
    """Devuelve {status, data, ...} solo si la tarea es del usuario."""
    with SessionLocal() as db:
        row = db.get(ComparisonTask, task_id)
        if row is None or row.user_id != str(user_id):
            return None
        return {
            "id": row.id,
            "status": row.status,
            "data": row.data,
            "list_id": row.list_id,
            "title": row.title,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def list_tasks(user_id: str, limit: int = 30, list_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Historial resumido (más recientes primero), sin el detalle por producto."""
    with SessionLocal() as db:
        q = db.query(ComparisonTask).filter(ComparisonTask.user_id == str(user_id))
        if list_id is not None:
            q = q.filter(ComparisonTask.list_id == list_id)
        out = []
        for row in q.order_by(ComparisonTask.created_at.desc()).limit(limit).all():
            data = row.data or {}
            totals = {s: v.get("total") for s, v in (data.get("totals_by_store") or {}).items()}
            out.append({
                "id": row.id,
                "status": row.status,
                "title": row.title,
                "list_id": row.list_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "winner": data.get("winner"),
                "totals": totals,
                "items_count": data.get("items_count"),
                "savings": (data.get("savings") or {}).get("amount"),
            })
        return out


def delete_task(task_id: str, user_id: str) -> bool:
    with SessionLocal() as db:
        row = db.get(ComparisonTask, task_id)
        if row is None or row.user_id != str(user_id):
            return False
        db.delete(row)
        db.commit()
        return True
