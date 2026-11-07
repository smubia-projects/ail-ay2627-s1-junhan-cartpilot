# API-only backend for Cloud Run. The UI (static/) is deployed on Vercel; the original Playwright
# image (Chromium) is kept in original-playwright/Dockerfile and is not built.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY mock_stores/ mock_stores/

RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8080

# --proxy-headers makes request.base_url use https:// behind Cloud Run's front end; the agents'
# product links are built from it.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
