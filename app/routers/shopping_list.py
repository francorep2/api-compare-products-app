# app/routers/shopping.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.schemas.shopping_list import (
    ShoppingListCreate, ShoppingListRename, ShoppingListOut, ShoppingListDetail,
    ShoppingItemCreate, ShoppingItemUpdate, ShoppingItemOut
)
from app.utils.dates import format_date

from app.repos.shopping_list_repo import (
    create_list, list_user_lists, get_user_list, rename_user_list, delete_user_list,
    list_items, add_item, update_item, delete_item
)
from app.dependencies.auth import get_current_user  

router = APIRouter(prefix="/shopping", tags=["shopping"])

# -------- LISTAS --------
@router.post("/lists", response_model=ShoppingListOut, status_code=status.HTTP_201_CREATED)
async def create_shopping_list(payload: ShoppingListCreate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    lst = create_list(db, user.id, payload.nombre)
    return ShoppingListOut(id=lst.id, nombre=lst.nombre, created_at=lst.format_date(lst.created_at) )

@router.get("/lists", response_model=list[ShoppingListOut])
async def get_my_lists(db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    lists = await list_user_lists(db, user.id)
    return [ShoppingListOut(id=l.id, nombre=l.nombre, created_at=l.created_at) for l in lists]

@router.get("/lists/{list_id}", response_model=ShoppingListDetail)
async def get_list_detail(list_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    lst = await get_user_list(db, user.id, list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    items = await list_items(db, user.id, list_id)
    return ShoppingListDetail(
        id=lst.id, nombre=lst.nombre, created_at=lst.created_at,
        items=[ShoppingItemOut(id=i.id, list_id=i.list_id, nombre=i.nombre, cantidad=i.cantidad, created_at=i.created_at) for i in items]
    )

@router.patch("/lists/{list_id}", response_model=ShoppingListOut)
async def rename_list_(list_id: int, payload: ShoppingListRename, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    lst = await rename_user_list(db, user.id, list_id, payload.nombre)
    if not lst:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    return ShoppingListOut(id=lst.id, nombre=lst.nombre, created_at=lst.created_at)

@router.delete("/lists/{list_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_list_(list_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    ok = await delete_user_list(db, user.id, list_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Lista no encontrada")

# -------- ITEMS --------
@router.get("/lists/{list_id}/items", response_model=list[ShoppingItemOut])
async def list_items_(list_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    lst = await get_user_list(db, user.id, list_id)
    if not lst:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    items = await list_items(db, user.id, list_id)
    return [ShoppingItemOut(id=i.id, list_id=i.list_id, nombre=i.nombre, cantidad=i.cantidad, created_at=i.created_at) for i in items]

@router.post("/lists/{list_id}/items", response_model=ShoppingItemOut, status_code=status.HTTP_201_CREATED)
async def add_item_(list_id: int, payload: ShoppingItemCreate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    item = await add_item(db, user.id, list_id, payload.nombre, payload.cantidad)
    if not item:
        raise HTTPException(status_code=404, detail="Lista no encontrada")
    return ShoppingItemOut(id=item.id, list_id=item.list_id, nombre=item.nombre, cantidad=item.cantidad, created_at=item.created_at)

@router.patch("/items/{item_id}", response_model=ShoppingItemOut)
async def update_item_(item_id: int, payload: ShoppingItemUpdate, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    item = await update_item(db, user.id, item_id, payload.nombre, payload.cantidad)
    if not item:
        raise HTTPException(status_code=404, detail="Item no encontrado")
    return ShoppingItemOut(id=item.id, list_id=item.list_id, nombre=item.nombre, cantidad=item.cantidad, created_at=item.created_at)

@router.delete("/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_item_(item_id: int, db: AsyncSession = Depends(get_db), user=Depends(get_current_user)):
    ok = await delete_item(db, user.id, item_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Item no encontrado")
