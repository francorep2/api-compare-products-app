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

## Rendimiento
- Búsqueda en las 4 tiendas: ~0,3–1 s (cacheada: instantánea). Búsqueda por código de barras: ~0,3 s.
- Comparar una lista de 15 productos en 4 tiendas: ~2–3 s.

## Notas
- Las comparaciones se guardan en Postgres (`comparison_tasks`, se purgan a las 24 h) y cada una solo la puede leer su dueño, así que sobreviven reinicios y funcionan con varios workers.
