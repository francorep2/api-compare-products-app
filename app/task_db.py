"""Persistencia de las tareas de comparación (tabla `comparison_tasks`)."""
from __future__ import annotations

import datetime
import logging
from typing import Any, Dict, Optional

from app.db import SessionLocal
from app.models.comparison import ComparisonTask

log = logging.getLogger("task_db")

RETENTION = datetime.timedelta(hours=24)


def create_task(task_id: str, user_id: str) -> None:
    with SessionLocal() as db:
        # limpieza oportunista de tareas viejas
        cutoff = datetime.datetime.now(datetime.timezone.utc) - RETENTION
        db.query(ComparisonTask).filter(ComparisonTask.created_at < cutoff).delete()
        db.add(ComparisonTask(id=task_id, user_id=str(user_id), status="processing"))
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
    """Devuelve {status, data} solo si la tarea es del usuario."""
    with SessionLocal() as db:
        row = db.get(ComparisonTask, task_id)
        if row is None or row.user_id != str(user_id):
            return None
        return {"status": row.status, "data": row.data}
