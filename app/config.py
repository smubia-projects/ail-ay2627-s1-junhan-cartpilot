"""Central settings, read once from environment variables / .env.

Keeping everything here means no other file calls os.getenv directly,
and the API key is never hardcoded anywhere.
"""
import os

from dotenv import load_dotenv

load_dotenv()  # reads .env in the project root (if present)


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


def _first(*names: str, default: str = "") -> str:
    """The first non-empty variable: new AI Lodge names first, the student's original names as fallbacks."""
    for n in names:
        v = os.getenv(n, "").strip()
        if v:
            return v
    return default


# Any OpenAI-compatible chat-completions gateway (AI Lodge uses apiyi).
API_BASE_URL: str = _first("API_BASE_URL", default="https://api.apiyi.com/v1").rstrip("/")
API_KEY: str = _first("API_KEY", "GEMINI_API_KEY")
MODEL_NAME: str = _first("MODEL_NAME", "GEMINI_MODEL", default="deepseek-v4-1-flash-260910")
VISION_MODEL_NAME: str = _first("VISION_MODEL_NAME", default="deepseek-v4-1-flash-260910")  # fridge-photo scan (v4.1 reads images)
DEMO_MODE: bool = _bool("DEMO_MODE", False)  # true = canned AI answers, no model calls (local dev only)
STORE_MODE: str = os.getenv("STORE_MODE", "mock")
MAX_PARALLEL: int = int(os.getenv("MAX_PARALLEL", "4"))
CORS_ORIGINS: list[str] = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]
