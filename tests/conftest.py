"""Shared test setup: canned AI answers (no key, no network) and no rate limiting."""
import os

os.environ["DEMO_MODE"] = "true"
for _k in ("UPSTASH_REDIS_REST_URL", "UPSTASH_REDIS_URL", "UPSTASH_REDIS_REST_TOKEN", "UPSTASH_REDIS_TOKEN"):
    os.environ.pop(_k, None)
