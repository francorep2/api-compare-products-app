import uuid
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException, status
from app.schemas import shopping_list
from app.services import list_comparator
from app.dependencies.auth import get_current_user, User

router = APIRouter(prefix="/compare", tags=["Comparison"])

@router.post("/start", status_code=status.HTTP_202_ACCEPTED)
def start_comparison(
    request_body: shopping_list.ComparisonRequestIn,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """
    Inicia una nueva comparación en segundo plano y devuelve un ID de tarea.
    Responde al instante.
    """
    task_id = str(uuid.uuid4())
    background_tasks.add_task(
        list_comparator.compare_items_background,
        task_id=task_id,
        items=request_body.items,
        enabled_stores=request_body.enabled
    )
    return {"task_id": task_id, "message": "La comparación ha comenzado."}


@router.get("/results/{task_id}", status_code=status.HTTP_200_OK)
def get_comparison_results(
    task_id: str,
    current_user: User = Depends(get_current_user)
):
    """
    Consulta el estado y/o el resultado de una tarea de comparación.
    """
    from app.task_db import tasks 
    task = tasks.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Tarea no encontrada o aún no ha comenzado.")
    
    return task