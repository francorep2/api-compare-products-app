# app/models/__init__.py
from app.db import Base  # asegura mismo Base
from .shopping_list import ShoppingList, ShoppingItem
