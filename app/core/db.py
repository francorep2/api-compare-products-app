# app/core/db.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

# Construye el DATABASE_URL desde variables de entorno
def _pg_url():
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "groceries")
    user = os.getenv("DB_USER", "appuser")
    pwd  = os.getenv("DB_PASS", "super-secret")
    sslmode = os.getenv("DB_SSLMODE", "disable")  # en prod: require / verify-full
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{name}?sslmode={sslmode}"

DATABASE_URL = os.getenv("DATABASE_URL", _pg_url())

# Engine con pool
engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_size=5,              # ajustá según tu server
    max_overflow=5,
    pool_pre_ping=True,       # detecta conexiones caídas
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

class Base(DeclarativeBase):
    pass

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
