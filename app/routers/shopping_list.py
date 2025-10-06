# app/routers/shopping.py
from dataclasses import Field
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.db import Base, get_db
from app.schemas.shopping_list import (
    ShoppingListCreate, ShoppingListRename, ShoppingListOut, ShoppingListDetail,
    ShoppingItemCreate, ShoppingItemUpdate, ShoppingItemOut
)

from app.repos.shopping_list_repo import (
    create_list, list_user_lists, get_user_list, rename_user_list, delete_user_list,
    list_items, add_item, update_item, delete_item, update_list_name
)
from app.dependencies.auth import get_current_user  

class RenameIn(Base):
    nombre: str 


router = APIRouter(prefix="/shopping", tags=["shopping"])

# -------- LISTAS --------
@router.post("/lists", response_model=ShoppingListOut, status_code=status.HTTP_201_CREATED)
def create_shopping_list(payload: ShoppingListCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    lst = create_list(db, user.id, payload.nombre)
    return ShoppingListOut(id=lst.id, nombre=lst.nombre, created_at=lst.created_at)

@router.put("/{list_id}")
def rename_list(list_id: int, payload: RenameIn, db: Session = Depends(get_db)):
    out = update_list_name(db, list_id, payload.nombre.strip())
    if not out:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    # devolver lo que tu front espera
    return {"id": out.id, "nombre": out.nombre, "created_at": out.created_at, "updated_at": getattr(out, "updated_at", None)}

@router.get("/lists", response_model=list[ShoppingListOut])
def get_my_lists(db: Session = Depends(get_db), user=Depends(get_current_user)):
    lists = list_user_lists(db, user.id)
    return [ShoppingListOut(id=l.id, nombre=l.nombre, created_at=l.created_at) for l in lists]

# app/routers/shopping.py
@router.get("/lists/{list_id}", response_model=ShoppingListDetail)
def get_list_detail(list_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    lst = get_user_list(db, user.id, list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    items = list_items(db, user.id, list_id)
    return ShoppingListDetail(
        id=lst.id, nombre=lst.nombre, created_at=lst.created_at,
        items=[ShoppingItemOut(
            id=i.id, list_id=i.list_id, nombre=i.nombre, cantidad=i.cantidad, done=i.done, created_at=i.created_at
        ) for i in items]
    )

@router.get("/lists/{list_id}/items", response_model=list[ShoppingItemOut])
def list_items_(list_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    lst = get_user_list(db, user.id, list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    items = list_items(db, user.id, list_id)
    return [ShoppingItemOut(
        id=i.id, list_id=i.list_id, nombre=i.nombre, cantidad=i.cantidad, done=i.done, created_at=i.created_at
    ) for i in items]


@router.patch("/items/{item_id}", response_model=ShoppingItemOut)
def update_item_(item_id: int, payload: ShoppingItemUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = update_item(db, user.id, item_id, payload.nombre, payload.cantidad, payload.done)  # 👈
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return ShoppingItemOut(
        id=item.id, list_id=item.list_id, nombre=item.nombre, cantidad=item.cantidad, done=item.done, created_at=item.created_at
    )
    
@router.delete("/lists/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_list_(list_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ok = delete_user_list(db, user.id, list_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lista no encontrada")


# -------- ITEMS --------
@router.get("/lists/{list_id}/items", response_model=list[ShoppingItemOut])
def list_items_(list_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    lst = get_user_list(db, user.id, list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    items = list_items(db, user.id, list_id)
    return [ShoppingItemOut(id=i.id, list_id=i.list_id, nombre=i.nombre, cantidad=i.cantidad, created_at=i.created_at, done=i.done) for i in items]

@router.post("/lists/{list_id}/items", response_model=ShoppingItemOut, status_code=status.HTTP_201_CREATED)
def add_item_(list_id: int, payload: ShoppingItemCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = add_item(db, user.id, list_id, payload.nombre, payload.cantidad, payload.done or False)
    if not item:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    return ShoppingItemOut(id=item.id, list_id=item.list_id, nombre=item.nombre, cantidad=item.cantidad, created_at=item.created_at , done=item.done)

@router.patch("/items/{item_id}", response_model=ShoppingItemOut)
def update_item_(item_id: int, payload: ShoppingItemUpdate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    item = update_item(db, user.id, item_id, payload.nombre, payload.cantidad)
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return ShoppingItemOut(id=item.id, list_id=item.list_id, nombre=item.nombre, cantidad=item.cantidad, created_at=item.created_at)

@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item_(item_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ok = delete_item(db, user.id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item no encontrado")
