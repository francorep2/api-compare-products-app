# app/models/shopping.py
from dataclasses import Field
from typing import List, Optional
from sqlalchemy.orm import Mapped, mapped_column, relationship, DeclarativeBase
from sqlalchemy import Date, ForeignKey, text, Boolean
from datetime import date, datetime

class Base(DeclarativeBase):
    pass

class ShoppingList(Base):
    __tablename__ = "shopping_list"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str]      # exactamente el mismo tipo/valor que tu login expone
    nombre: Mapped[str]
    created_at: Mapped[date] = mapped_column(server_default=text("CURRENT_DATE"))
    items: Mapped[list["ShoppingItem"]] = relationship(
        back_populates="list", cascade="all, delete-orphan", passive_deletes=True
    )

class ShoppingItem(Base):
    __tablename__ = "shopping_item"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    list_id: Mapped[int] = mapped_column(ForeignKey("shopping_list.id", ondelete="CASCADE"), index=True)
    nombre: Mapped[str]
    cantidad: Mapped[str]
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[str] = mapped_column(Date, server_default=text("CURRENT_DATE"))
    list: Mapped[ShoppingList] = relationship(back_populates="items")
    

