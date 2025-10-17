# ==== Build base ====
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    # Playwright: evita fallos por sandbox y fuentes
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    DEBIAN_FRONTEND=noninteractive

# Paquetes del sistema requeridos por Chromium/Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git tini \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libxrender1 libdrm2 libgbm1 libasound2 \
    libxshmfence1 libgtk-3-0 libpango-1.0-0 libharfbuzz0b fonts-liberation \
    libu2f-udev libxcb1 libx11-6 libxext6 libxtst6 libpci3 libwayland-client0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ==== Depencias Python ====
COPY requirements.txt .
RUN pip install -r requirements.txt

# Instalar Chromium + deps de Playwright (en Debian/Ubuntu funciona --with-deps)
RUN playwright install --with-deps chromium

# ==== Código ====
COPY . .

# Puerto de la app FastAPI
EXPOSE 8000

# Healthcheck simple a /health
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# ==== Arranque ====
# tini evita procesos zombis; uvicorn lanza la API
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
