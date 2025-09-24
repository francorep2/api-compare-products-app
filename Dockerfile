FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 WORKERS=2
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 👇 requirements.txt está en la RAÍZ del repo
COPY ./requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt

# Playwright + Chromium
RUN pip install "playwright==1.55.0"
RUN python -m playwright install --with-deps chromium

# Resto del código (también desde la raíz)
COPY . /app

EXPOSE 8000
CMD bash -lc '\
  if [ -f "alembic.ini" ]; then alembic upgrade head || echo "skip"; fi && \
  gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT} --workers ${WORKERS} --timeout 180'
