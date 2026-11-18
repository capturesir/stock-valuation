# One image for both the API and the worker (docker-compose picks the command).
# Build context: repository root (the v1 engine in src/ is reused as-is).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    APP_DATA_DIR=/app/var \
    TZ=Asia/Macau

WORKDIR /app

# dependencies first, so code changes don't reinstall them
COPY requirements.txt ./requirements.txt
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install -r backend/requirements.txt

# application: engine, backend, prompts, reference data, built-in specs and v1 outputs
COPY VERSION ./
COPY src ./src
COPY libs ./libs
# item and check names for exports: the same dictionaries the web app uses
COPY frontend/packages/i18n/src/locales ./frontend/packages/i18n/src/locales
COPY backend ./backend
COPY prompts ./prompts
COPY data ./data
COPY cases ./cases
COPY out ./out
COPY config.example.yaml ./

RUN useradd --create-home --uid 10001 app \
 && mkdir -p /app/var /app/out /app/cases \
 && chown -R app:app /app/var /app/out /app/cases
USER app

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
