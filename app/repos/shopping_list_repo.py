# app/repos/shopping_repo.py
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.shopping_list import ShoppingList, ShoppingItem
from sqlalchemy.orm import Session

def create_list(db: Session, user_id: str, nombre: str) -> ShoppingList:
    lst = ShoppingList(user_id=user_id, nombre=nombre)
    db.add(lst)
    db.commit()       # 👈 SIN await
    db.refresh(lst)
    return lst

async def list_user_lists(db: AsyncSession, user_id: str) -> list[ShoppingList]:
    res = await db.execute(
        select(ShoppingList).where(ShoppingList.user_id == user_id).order_by(ShoppingList.created_at.desc())
    )
    return list(res.scalars().all())

async def get_user_list(db: AsyncSession, user_id: str, list_id: int) -> ShoppingList | None:
    res = await db.execute(
        select(ShoppingList).where(ShoppingList.user_id == user_id, ShoppingList.id == list_id)
    )
    return res.scalars().first()

async def rename_user_list(db: AsyncSession, user_id: str, list_id: int, nombre: str) -> ShoppingList | None:
    lst = await get_user_list(db, user_id, list_id)
    if not lst: return None
    lst.nombre = nombre.strip()
    await db.commit()
    await db.refresh(lst)
    return lst

async def delete_user_list(db: AsyncSession, user_id: str, list_id: int) -> bool:
    res = await db.execute(
        delete(ShoppingList).where(ShoppingList.user_id == user_id, ShoppingList.id == list_id)
    )
    await db.commit()
    return res.rowcount > 0

# -------- ITEMS (siempre verificando ownership vía list_id -> user_id) --------
async def list_items(db: AsyncSession, user_id: str, list_id: int) -> list[ShoppingItem]:
    if not await get_user_list(db, user_id, list_id):
        return []
    res = await db.execute(
        select(ShoppingItem).where(ShoppingItem.list_id == list_id).order_by(ShoppingItem.created_at.desc())
    )
    return list(res.scalars().all())

async def add_item(db: AsyncSession, user_id: str, list_id: int, nombre: str, cantidad: str) -> ShoppingItem | None:
    if not await get_user_list(db, user_id, list_id):
        return None
    item = ShoppingItem(list_id=list_id, nombre=nombre.strip(), cantidad=cantidad.strip())
    db.add(item)
    await db.commit()
    await db.refresh(item)
    return item

async def get_item_owned(db: AsyncSession, user_id: str, item_id: int) -> ShoppingItem | None:
    # join para asegurar que el item pertenece a una lista del usuario
    res = await db.execute(
        select(ShoppingItem)
        .join(ShoppingList, ShoppingList.id == ShoppingItem.list_id)
        .where(ShoppingList.user_id == user_id, ShoppingItem.id == item_id)
    )
    return res.scalars().first()

async def update_item(db: AsyncSession, user_id: str, item_id: int, nombre: str | None, cantidad: str | None) -> ShoppingItem | None:
    item = await get_item_owned(db, user_id, item_id)
    if not item: return None
    if nombre is not None: item.nombre = nombre.strip()
    if cantidad is not None: item.cantidad = cantidad.strip()
    await db.commit()
    await db.refresh(item)
    return item

async def delete_item(db: AsyncSession, user_id: str, item_id: int) -> bool:
    item = await get_item_owned(db, user_id, item_id)
    if not item: return False
    await db.delete(item)
    await db.commit()
    return True
