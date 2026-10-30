FROM python:3.11.9-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin appuser

COPY backend/requirements.txt /app/backend/requirements.txt
RUN python -m pip install --upgrade pip setuptools wheel \
    && python -m pip install --no-cache-dir -r /app/backend/requirements.txt

COPY --chown=appuser:appuser . /app

RUN mkdir -p /app/qa-platform/storage \
    && chown -R appuser:appuser /app/qa-platform \
    && chmod 0755 /app/docker-entrypoint.sh

WORKDIR /app/backend

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl --fail --silent --show-error "http://127.0.0.1:${PORT}/health" || exit 1

ENV HOST=0.0.0.0 \
    PORT=8001 \
    QA_HOST=0.0.0.0 \
    QA_PORT=8001

ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["python", "app.py"]
