# app/db.py
import os
import logging
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode

log = logging.getLogger("db")

# ---------------------------------------------
# Helpers: URL + SSL sane for Render/Koyeb
# ---------------------------------------------
def _pg_url_from_env() -> str:
    """
    Construye un DATABASE_URL completo desde variables separadas.
    Si faltan, devuelve string vacío para permitir fallback a DATABASE_URL.
    Fuerza sslmode=require si no viene especificado.
    """
    host = os.getenv("DB_HOST")
    port = os.getenv("DB_PORT")
    name = os.getenv("DB_NAME")
    user = os.getenv("DB_USER")
    pwd  = os.getenv("DB_PASS")
    sslmode = os.getenv("DB_SSLMODE", "require")  # default seguro
    if not all([host, port, name, user, pwd]):
        return ""
    return f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{name}?sslmode={sslmode}"

def _normalize_db_url(url: str) -> str:
    """
    Normaliza la URL para psycopg2:
    - Reemplaza postgres:// por postgresql://
    - Asegura driver +psycopg2
    """
    if not url:
        return url
    # Normalizar esquema
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    # Agregar driver si falta
    if url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url

# --- SSL Policy & Mask helpers ---
def _apply_ssl_policy(url: str) -> str:
    """
    Aplica política de SSL según host:
    - localhost/127.0.0.1 -> sslmode=disable (o DB_SSLMODE si está set)
    - remoto -> sslmode=require si no está presente
    """
    if not url:
        return url
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        q = dict(parse_qsl(parsed.query, keep_blank_values=True))
        if host in ("localhost", "127.0.0.1"):
            q["sslmode"] = os.getenv("DB_SSLMODE", "disable")
        else:
            q.setdefault("sslmode", "require")
        new_query = urlencode(q)
        parsed = parsed._replace(query=new_query)
        return urlunparse(parsed)
    except Exception:
        # Si algo falla, devolvemos el original
        return url

def _mask_db_url(url: str) -> str:
    """Oculta la password para loguear la URL final sin filtrar secretos."""
    if not url:
        return url
    try:
        p = urlparse(url)
        if p.password:
            netloc = p.netloc.replace(f":{p.password}@", ":****@")
            p = p._replace(netloc=netloc)
        return urlunparse(p)
    except Exception:
        return url

#
# Construye DATABASE_URL priorizando DB_* (local) y luego DATABASE_URL (cloud)
_from_env = os.getenv("DATABASE_URL", "")
_from_parts = _pg_url_from_env()
DATABASE_URL = _normalize_db_url(_from_parts or _from_env)
# Aplica política de SSL por host (localhost -> disable, remoto -> require)
DATABASE_URL = _apply_ssl_policy(DATABASE_URL)

# Log de la URL efectiva (enmascarada)
logging.getLogger("db").info(f"[DB] URL efectiva: {_mask_db_url(DATABASE_URL)}")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL no configurada (ni variables DB_* suficientes)")

# ---------------------------------------------
# Engine con pool robusto y keepalive
# ---------------------------------------------
engine = create_engine(
    DATABASE_URL,
    future=True,
    pool_pre_ping=True,   # valida conexión antes de usarla
    pool_size=4,          # chico para límites bajos (Render/Koyeb)
    max_overflow=4,
    pool_recycle=180,     # recicla antes de que el proxy tire la conexión
    pool_timeout=5,
    connect_args={
        "sslmode": "require",
        # TCP keepalive para evitar desconexiones silenciosas
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 5,
        "keepalives_count": 5,
        # Opcional si querés limitar queries largas:
        # "options": "-c statement_timeout=15000",
        "application_name": "groceries_api",
    },
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

# ---------------------------------------------
# Health check opcional (útil en /health/db)
# ---------------------------------------------
def db_healthcheck() -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        log.warning(f"[DB] healthcheck failed: {e}")
        return False
