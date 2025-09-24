# app/core/settings.py
import os

def _build_db_url_from_parts():
    host = os.getenv("DB_HOST", "localhost")
    port = os.getenv("DB_PORT", "5432")
    name = os.getenv("DB_NAME", "groceries")
    user = os.getenv("DB_USER", "appuser")
    pwd  = os.getenv("DB_PASS", "super-secret")
    ssl  = os.getenv("DB_SSLMODE", "disable")
    return f"postgresql://{user}:{pwd}@{host}:{port}/{name}?sslmode={ssl}"

DATABASE_URL = os.getenv("DATABASE_URL") or _build_db_url_from_parts()

# Email / SMTP / SendGrid
USE_SENDGRID_API = os.getenv("USE_SENDGRID_API", "1") == "1"
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY", "")
MAIL_FROM = os.getenv("MAIL_FROM", "no-reply@example.com")

SMTP_CFG = {
    "dry_run": os.getenv("SMTP_DRY_RUN", "0") == "1",
    "host": os.getenv("SMTP_HOST", "smtp.sendgrid.net"),
    "port": int(os.getenv("SMTP_PORT", "2525")),
    "user": os.getenv("SMTP_USER", "apikey"),
    "password": os.getenv("SMTP_PASS", ""),
    "use_tls": os.getenv("SMTP_TLS", "1") == "1",
    "use_ssl": os.getenv("SMTP_SSL", "0") == "1",
    "timeout": int(os.getenv("SMTP_TIMEOUT", "20")),
}

APP_NAME = os.getenv("APP_NAME", "Comparador Super")

# JWT
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_SUB_CLAIM = os.getenv("JWT_SUB_CLAIM", "sub")
