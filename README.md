# Changuito (beta) — API comparador de precios

API (FastAPI + PostgreSQL) que compara precios de supermercados argentinos
(**Coto, Carrefour, Jumbo y Día**). Front: [front-compare-products-app](https://github.com/francorep2/front-compare-products-app).

## Qué hace
- Búsqueda de productos por nombre o código de barras en las 4 tiendas (`/search/...`).
- Listas de compras por usuario (`/shopping/...`).
- Comparación de una lista completa: devuelve el total por tienda, los ítems que faltan y la tienda más barata (`POST /compare/start` + `GET /compare/results/{task_id}`).
- Auth con registro por OTP por email, JWT + refresh tokens, reset de contraseña, preferencias de tiendas y tickets de soporte.

## Estructura
- `app/routers` endpoints · `app/services` lógica y wrappers de scrapers · `app/auth` usuarios/OTP/JWT
- `app/providers/` una integración HTTP por tienda: Coto (buscador Constructor.io del sitio) y Carrefour/Jumbo/Día (API pública VTEX). Cliente `httpx` compartido (HTTP/2, keep-alive), cache de 10 min y límite de concurrencia por host. Sin navegador.
- `docker/` Postgres + API para desarrollo · `Dockerfile` + `render.yaml` deploy en Render

## Correr local
```bash
cp .env.example .env          # completar JWT_SECRET, SENDGRID_API_KEY, etc.
docker compose -f docker/docker-compose.yml up
# o sin docker:
pip install -r requirements.txt
uvicorn app.main:app --reload
```
Docs interactivas en `http://localhost:8000/docs`, health check en `/health`.

## Deploy en Railway
- `railway.json` + `Dockerfile` (imagen ~360 MB, sin navegador). Healthcheck en `/health`.
- Servicios: `api` (este repo, enlazado a GitHub: cada push a `main` despliega) + `Postgres`. Variables de la API: `DATABASE_URL` (referencia al Postgres), `JWT_SECRET` (aleatorio), `APP_NAME`, `MAIL_FROM` (con Resend debe ser un remitente de un dominio verificado, p. ej. `Changuito <no-reply@tu-dominio>`) y `RESEND_API_KEY` para enviar los códigos por mail. Sin clave de mail, los códigos salen por los logs (`SMTP_DRY_RUN=1`). También se soportan SendGrid (`USE_SENDGRID_API=1` + `SENDGRID_API_KEY`) y SMTP.
- La configuración del servicio vive en `.railway/railway.ts` (infraestructura como código de Railway).
- Deploy desde la carpeta del repo: `railway up`.

## Configuración y base de datos
- `JWT_SECRET` es **obligatorio** en producción (la app no arranca sin uno real). Generar: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Para desarrollo local usá `APP_ENV=dev` (ver `.env.example`).
- El esquema se maneja con **Alembic** (`alembic/`). El Dockerfile corre `alembic upgrade head` antes de levantar el server y falla el deploy si la migración falla. Nueva migración: `alembic revision -m "descripcion"`.
- La migración `0001` es idempotente: sobre una base existente no toca nada y crea solo las tablas que falten.

## Tests
```bash
pip install -r requirements.txt -r requirements-dev.txt
pytest -q
```
Los tests no usan red ni Postgres (tiendas simuladas, SQLite en memoria). CI: `.github/workflows/tests.yml`.

## Rendimiento
- Búsqueda en las 4 tiendas: ~0,3–1 s (cacheada: instantánea). Búsqueda por código de barras: ~0,3 s.
- Comparar una lista de 15 productos en 4 tiendas: ~2–3 s.

## Notas
- Las comparaciones se guardan en Postgres (`comparison_tasks`, se purgan a las 24 h) y cada una solo la puede leer su dueño, así que sobreviven reinicios y funcionan con varios workers.
