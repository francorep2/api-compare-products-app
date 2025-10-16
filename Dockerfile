# ---- Base
FROM python:3.11-slim

# Env básicos + ruta de browsers de Playwright + logs sin buffer
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    PYTHONPATH=/app

WORKDIR /app

# Paquetes del sistema (lo mínimo para chromium)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates dumb-init \
    build-essential libpq-dev \
    # libs que chromium usa (si ya instalás --with-deps igual ayuda tenerlas)
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 \
    libasound2 libatspi2.0-0 libxshmfence1 fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# ---- Reqs primero (mejor cache)
COPY ./requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Playwright + Chromium (versión puede ser la de tus reqs; esta es estable)
RUN pip install "playwright==1.55.0" && python -m playwright install --with-deps chromium

# ---- Código
COPY . /app

EXPOSE 8000

# Señales limpias en contenedores
ENTRYPOINT ["dumb-init", "--"]

# Un solo worker para evitar OOM en Render Free
# (workers extra + Chromium = kill por memoria)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "debug"]
