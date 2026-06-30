# Production API image (multi-stage)
ARG PYTHON_VERSION=3.10
FROM python:${PYTHON_VERSION}-slim AS builder

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:${PYTHON_VERSION}-slim AS runtime

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin ftth

COPY --from=builder /install /usr/local
COPY . .

ENV PYTHONPATH=/app:/app/backend \
    PYTHONUNBUFFERED=1 \
    FTTH_DEV_RELOAD=0 \
    FTTH_ENV=production

RUN mkdir -p uploads data frontend/public frontend/src && chown -R ftth:ftth /app
USER ftth

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health')" || exit 1

CMD ["python", "-m", "uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "8000"]
