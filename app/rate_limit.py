"""Project adapter over the shared AI Lodge rate-limit engine.

The engine (`rate_limit_engine.py`) is vendored unmodified from
`shared/rate-limit/engine/fastapi/`. This adapter only supplies the project slug
and one bucket per kind of AI action; the engine handles X-Forwarded-For, central
config, event mode, the kill switch (503) and fail-open behaviour.

Buckets:
  plan    recipe options, the ingredient list + method, leftover recipe ideas (text)
  scan    the fridge-photo scan (vision)
  search  one agent search run (ends with one AI double-check of the matches)
"""
import os

from fastapi import Request

from app.rate_limit_engine import RateLimiter


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "").strip() or default)
    except ValueError:
        return default


limiter = RateLimiter(
    # Slug = full project folder name (matches the central control sheet rows).
    project="ail-ay2627-s1-junhan-cartpilot",
    buckets={
        "plan": _env_int("RATE_LIMIT_PLAN_MAX", 12),
        "scan": _env_int("RATE_LIMIT_SCAN_MAX", 4),
        "search": _env_int("RATE_LIMIT_SEARCH_MAX", 4),
    },
    redis_url=os.getenv("UPSTASH_REDIS_REST_URL") or os.getenv("UPSTASH_REDIS_URL", ""),
    redis_token=os.getenv("UPSTASH_REDIS_REST_TOKEN") or os.getenv("UPSTASH_REDIS_TOKEN", ""),
    default_window=_env_int("RATE_LIMIT_WINDOW_SECONDS", 432000),
)


def check_rate_limit(request: Request, bucket: str) -> None:
    limiter.enforce(request, bucket)
