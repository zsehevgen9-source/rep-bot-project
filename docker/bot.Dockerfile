# ---------- Сборка зависимостей ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------- Итоговый образ ----------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    PATH="/opt/venv/bin:$PATH"

# Дополнительные корневые сертификаты (Минцифры и т.п.) из docker/certs/*.crt
COPY docker/certs/ /usr/local/share/ca-certificates/extra/
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg fonts-dejavu-core fonts-noto-color-emoji \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

RUN useradd --create-home --uid 1000 bot

WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
COPY --chown=bot:bot app ./app
COPY --chown=bot:bot alembic.ini ./
COPY --chown=bot:bot data ./data

USER bot

EXPOSE 8080
CMD ["python", "-m", "app.main"]
