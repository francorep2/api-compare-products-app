import os

os.environ.setdefault("APP_ENV", "dev")
os.environ.setdefault("JWT_SECRET", "test-secret-no-usar-en-prod")
os.environ.setdefault("DATABASE_URL", "sqlite://")
