# app/core/settings.py
import logging
import os

log = logging.getLogger("settings")

# "dev" permite arrancar sin JWT_SECRET (solo desarrollo local). Cualquier otro valor = producción.
APP_ENV = os.getenv("APP_ENV", "production").lower()

_WEAK_SECRETS = {"", "change-me", "dev-secret-change-me", "secret", "changeme"}
_DEV_FALLBACK = "dev-only-insecure-secret"


def get_jwt_secret() -> str:
    """Secreto JWT. En producción es obligatorio y no puede ser uno de los valores de ejemplo."""
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if secret.lower() not in _WEAK_SECRETS:
        return secret
    if APP_ENV == "dev":
        log.warning("JWT_SECRET no definido: usando secreto inseguro de desarrollo (APP_ENV=dev)")
        return _DEV_FALLBACK
    raise RuntimeError(
        "JWT_SECRET no está definido o es un valor de ejemplo. Definí un secreto largo y aleatorio "
        "(p. ej. `python -c \"import secrets; print(secrets.token_urlsafe(48))\"`) "
        "o usá APP_ENV=dev solo en desarrollo local."
    )


JWT_SECRET = get_jwt_secret()
JWT_ALGORITHM = "HS256"
