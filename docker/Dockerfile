FROM mcr.microsoft.com/playwright/python:v1.47.2-jammy
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PORT=8000 WORKERS=2
WORKDIR /app

COPY ../requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . /app
EXPOSE 8000

CMD bash -lc '\
  if [ -f "alembic.ini" ]; then \
    echo ">> Running Alembic migrations"; \
    alembic upgrade head || echo ">> Alembic not configured, skipping"; \
  fi && \
  echo ">> Starting Gunicorn" && \
  gunicorn app.main:app -k uvicorn.workers.UvicornWorker -b 0.0.0.0:${PORT} --workers ${WORKERS} --timeout 180'
