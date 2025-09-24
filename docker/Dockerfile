# Base liviana y estable
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    WORKERS=2

WORKDIR /app

# Paquetes del sistema (psycopg2 y Playwright deps via --with-deps)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Dependencias Python
COPY requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r ../requirements.txt

# Instala Playwright y navegadores (Chromium) + dependencias del SO
RUN pip install "playwright==1.47.2" && \
    python -m playwright install --with-deps chromium

# Copia el código
COPY . /app

EXPOSE 8000

# (Opcional) Migraciones Alembic + Gunicorn
CMD bash -lc '\
  if [ -f "alembic.ini" ]; then \
    echo ">> Running Alembic migrations"; \
    alembic upgrade head || echo ">> Alembic not configured, skipping"; \
  fi && \
  echo ">> Starting Gunicorn" && \
  gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT} --workers ${WORKERS} --timeout 180'
