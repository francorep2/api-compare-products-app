FROM python:3.11-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 WORKERS=2
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl ca-certificates libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# 👇 requirements.txt está en la RAÍZ del repo
COPY ./requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt
RUN pip install gunicorn uvicorn[standard]

# Playwright + Chromium
RUN pip install "playwright==1.55.0"
RUN python -m playwright install --with-deps chromium

# Resto del código (también desde la raíz)
COPY . /app

ENV PYTHONPATH=/app

EXPOSE 8000
CMD bash -lc '\
  if [ -f "alembic.ini" ]; then alembic upgrade head || echo "skip"; fi && \
  python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --log-level debug'