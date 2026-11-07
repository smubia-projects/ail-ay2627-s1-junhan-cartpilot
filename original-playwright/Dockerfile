FROM python:3.12-slim

# Browsers live outside any home dir so the non-root runtime user can read them
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

# Hugging Face Spaces runs containers as uid 1000
RUN useradd -m -u 1000 user
COPY --chown=user . .
USER user

ENV HOST=0.0.0.0 HEADLESS=true
EXPOSE 7860

# Hugging Face uses 7860; Render injects $PORT. --proxy-headers makes request.base_url
# use https:// behind the host's proxy, which the store URLs are built from.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
