# Dockerfile (raíz)
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

WORKDIR /app

# Requisitos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Código
COPY . .

# Asegurar que FastAPI vea /app como import root
ENV PYTHONPATH=/app

# Uvicorn single worker (menos RAM)
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--timeout-keep-alive", "5"]
