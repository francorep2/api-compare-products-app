# Groceries API — Comparador Super

API (FastAPI + PostgreSQL) que compara precios de supermercados argentinos
(**Coto, Carrefour, Jumbo y Día**). Front: [front-compare-products-app](https://github.com/francorep2/front-compare-products-app).

## Qué hace
- Búsqueda de productos por nombre o código de barras en las 4 tiendas (`/search/...`).
- Listas de compras por usuario (`/shopping/...`).
- Comparación de una lista completa: devuelve el total por tienda, los ítems que faltan y la tienda más barata (`POST /compare/start` + `GET /compare/results/{task_id}`).
- Auth con registro por OTP por email, JWT + refresh tokens, reset de contraseña, preferencias de tiendas y tickets de soporte.

## Estructura
- `app/routers` endpoints · `app/services` lógica y wrappers de scrapers · `app/auth` usuarios/OTP/JWT
- `scrapers/` scrapers por tienda (Coto con Playwright; Carrefour/Jumbo/Día vía VTEX)
- `docker/` Postgres + API para desarrollo · `Dockerfile` + `render.yaml` deploy en Render

## Correr local
```bash
cp .env.example .env          # completar JWT_SECRET, SENDGRID_API_KEY, etc.
docker compose -f docker/docker-compose.yml up
# o sin docker:
pip install -r requirements.txt && playwright install --with-deps chromium
uvicorn app.main:app --reload
```
Docs interactivas en `http://localhost:8000/docs`, health check en `/health`.

## Notas
- El estado de las comparaciones (`app/task_db.py`) vive en memoria: se pierde al reiniciar y requiere 1 solo worker.
