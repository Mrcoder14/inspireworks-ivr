import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


class Config:
    PLIVO_AUTH_ID = os.getenv("PLIVO_AUTH_ID", "").strip()
    PLIVO_AUTH_TOKEN = os.getenv("PLIVO_AUTH_TOKEN", "").strip()
    PLIVO_FROM_NUMBER = os.getenv("PLIVO_FROM_NUMBER", "").strip()

    ALLOWED_RECEIVER_NUMBER = os.getenv(
        "ALLOWED_RECEIVER_NUMBER", ""
    ).strip()

    ASSOCIATE_NUMBER = os.getenv("ASSOCIATE_NUMBER", "").strip()
    PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    DEMO_ACCESS_KEY = os.getenv("DEMO_ACCESS_KEY", "").strip()

    PORT = int(os.getenv("PORT") or "5000")