import uuid
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app import task_db
from app.dependencies.auth import User, get_current_user
from app.schemas import shopping_list
from app.services import list_comparator

router = APIRouter(prefix="/compare", tags=["Comparison"])


@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
def start_comparison(
    request_body: shopping_list.ComparisonRequestIn,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
):
    """Inicia una comparación en segundo plano y devuelve un ID de tarea (responde al instante)."""
    task_id = str(uuid.uuid4())
    # La fila existe antes de responder: el primer poll nunca da 404.
    task_db.create_task(task_id, current_user.id, list_id=request_body.list_id, title=request_body.title)
    background_tasks.add_task(
        list_comparator.compare_items_background,
        task_id=task_id,
        items=request_body.items,
        enabled_stores=request_body.enabled,
    )
    return {"task_id": task_id, "message": "La comparación ha comenzado."}


@router.get("/history", status_code=status.HTTP_200_OK)
def comparison_history(
    limit: int = Query(30, ge=1, le=100),
    list_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
):
    """Comparaciones anteriores del usuario (resumen), más recientes primero."""
    return task_db.list_tasks(current_user.id, limit=limit, list_id=list_id)


@router.get("/results/{task_id}", status_code=status.HTTP_200_OK)
def get_comparison_results(task_id: str, current_user: User = Depends(get_current_user)):
    """Estado y/o resultado de una comparación."""
    task = task_db.get_task(task_id, current_user.id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_comparison(task_id: str, current_user: User = Depends(get_current_user)):
    if not task_db.delete_task(task_id, current_user.id):
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
