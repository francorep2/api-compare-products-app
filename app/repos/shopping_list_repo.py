from typing import Optional, List
from types import SimpleNamespace
from sqlalchemy.orm import Session
from sqlalchemy import text

# ---------- helpers ----------

def _row_to_obj(row) -> Optional[SimpleNamespace]:
    if row is None:
        return None
    mapping = getattr(row, "_mapping", None)
    data = dict(mapping) if mapping is not None else dict(row)
    return SimpleNamespace(**data)


def _rows_to_objs(rows) -> List[SimpleNamespace]:
    return [_row_to_obj(r) for r in rows if r is not None]


# ---------- LISTS ----------

def create_list(db: Session, user_id: str, nombre: str) -> SimpleNamespace:
    res = db.execute(
        text(
            """
            INSERT INTO shopping_list (user_id, nombre)
            VALUES (:user_id, :nombre)
            RETURNING id, nombre, created_at
            """
        ),
        {"user_id": user_id, "nombre": nombre},
    )
    db.commit()
    return _row_to_obj(res.fetchone())


def list_user_lists(db: Session, user_id: str) -> List[SimpleNamespace]:
    res = db.execute(
        text(
            """
            SELECT id, nombre, created_at
            FROM shopping_list
            WHERE user_id = :user_id
            ORDER BY created_at DESC, id DESC
            """
        ),
        {"user_id": user_id},
    )
    return _rows_to_objs(res.fetchall())


def get_user_list(db: Session, user_id: str, list_id: int) -> Optional[SimpleNamespace]:
    res = db.execute(
        text(
            """
            SELECT id, nombre, created_at
            FROM shopping_list
            WHERE id = :id AND user_id = :user_id
            """
        ),
        {"id": list_id, "user_id": user_id},
    )
    return _row_to_obj(res.fetchone())


def rename_user_list(db: Session, user_id: str, list_id: int, nombre: str) -> Optional[SimpleNamespace]:
    res = db.execute(
        text(
            """
            UPDATE shopping_list
            SET nombre = :nombre
            WHERE id = :id AND user_id = :user_id
            RETURNING id, nombre, created_at
            """
        ),
        {"id": list_id, "user_id": user_id, "nombre": nombre},
    )
    db.commit()
    return _row_to_obj(res.fetchone())


def delete_user_list(db: Session, user_id: str, list_id: int) -> bool:
    res = db.execute(
        text("DELETE FROM shopping_list WHERE id = :id AND user_id = :user_id"),
        {"id": list_id, "user_id": user_id},
    )
    db.commit()
    return res.rowcount > 0


# ---------- ITEMS ----------

def list_items(db: Session, user_id: str, list_id: int) -> List[SimpleNamespace]:
    if not get_user_list(db, user_id, list_id):
        return []
    res = db.execute(
        text(
            """
            SELECT id, list_id, nombre, cantidad, created_at
            FROM shopping_item
            WHERE list_id = :list_id
            ORDER BY created_at DESC, id DESC
            """
        ),
        {"list_id": list_id},
    )
    return _rows_to_objs(res.fetchall())


def add_item(db: Session, user_id: str, list_id: int, nombre: str, cantidad: str) -> Optional[SimpleNamespace]:
    if not get_user_list(db, user_id, list_id):
        return None
    res = db.execute(
        text(
            """
            INSERT INTO shopping_item (list_id, nombre, cantidad)
            VALUES (:list_id, :nombre, :cantidad)
            RETURNING id, list_id, nombre, cantidad, created_at
            """
        ),
        {"list_id": list_id, "nombre": nombre, "cantidad": cantidad},
    )
    db.commit()
    return _row_to_obj(res.fetchone())


def update_item(db: Session, user_id: str, item_id: int, nombre: str | None, cantidad: str | None) -> Optional[SimpleNamespace]:
    r0 = db.execute(text("SELECT list_id FROM shopping_item WHERE id = :id"), {"id": item_id}).fetchone()
    if r0 is None:
        return None
    list_id = _row_to_obj(r0).list_id  # type: ignore
    if not get_user_list(db, user_id, list_id):
        return None

    sets = []
    params = {"id": item_id}
    if nombre is not None:
        sets.append("nombre = :nombre")
        params["nombre"] = nombre
    if cantidad is not None:
        sets.append("cantidad = :cantidad")
        params["cantidad"] = cantidad
    if not sets:
        res1 = db.execute(text("SELECT id, list_id, nombre, cantidad, created_at FROM shopping_item WHERE id = :id"), {"id": item_id})
        return _row_to_obj(res1.fetchone())

    sql = f"""
        UPDATE shopping_item
        SET {', '.join(sets)}
        WHERE id = :id
        RETURNING id, list_id, nombre, cantidad, created_at
    """
    res = db.execute(text(sql), params)
    db.commit()
    return _row_to_obj(res.fetchone())


def delete_item(db: Session, user_id: str, item_id: int) -> bool:
    r0 = db.execute(text("SELECT list_id FROM shopping_item WHERE id = :id"), {"id": item_id}).fetchone()
    if r0 is None:
        return False
    list_id = _row_to_obj(r0).list_id  # type: ignore
    if not get_user_list(db, user_id, list_id):
        return False

    res = db.execute(text("DELETE FROM shopping_item WHERE id = :id"), {"id": item_id})
    db.commit()
    return res.rowcount > 0
