# API, worker and beat share this image; the entrypoint picks the role.
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Tesseract (+ French and English data) for OCR of scanned PDFs.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-fra tesseract-ocr-eng \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY alembic.ini pyproject.toml ./
COPY alembic ./alembic
COPY src ./src
COPY eval ./eval
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh

RUN useradd --create-home --uid 10001 contexthub \
    && mkdir -p /data/uploads \
    && chown -R contexthub:contexthub /data \
    && chmod +x /usr/local/bin/entrypoint.sh
USER contexthub

EXPOSE 8000
ENTRYPOINT ["entrypoint.sh"]
CMD ["api"]
