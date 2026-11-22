import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def load_local_env():
    """Load simple KEY=VALUE entries from .env without overriding the shell."""
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "local-development-key-change-me")
    SQLALCHEMY_DATABASE_URI = os.getenv("DATABASE_URL") or (
        f"sqlite:///{(BASE_DIR / 'instance' / 'food_donation.db').as_posix()}"
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024
    UPLOAD_FOLDER = str(BASE_DIR / "static" / "uploads")
    APP_ENV = os.getenv("APP_ENV", "development")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0") == "1"
