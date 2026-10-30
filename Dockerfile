# Image untuk web & worker (sama, beda perintah). Build: docker compose build
# Image dasar resmi. Untuk build yang dapat diulang, pin digest: --build-arg PYTHON_IMAGE=python:3.12-slim@sha256:...
ARG PYTHON_IMAGE=python:3.12-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    VIDEO_APP_DATA=/data HF_HOME=/data/hf-cache

# FFmpeg dari Debian sudah termasuk libass (subtitle) dan sidechaincompress (ducking musik).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tini \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY web ./web
COPY fonts ./fonts
COPY models ./models

RUN useradd --create-home --uid 1000 app && mkdir -p /data && chown app:app /data
USER app
VOLUME /data
EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
# FORWARDED_ALLOW_IPS: alamat/subnet reverse proxy yang dipercaya untuk header X-Forwarded-* (staging: subnet Caddy).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers ${WEB_WORKERS:-2} --proxy-headers --forwarded-allow-ips=\"${FORWARDED_ALLOW_IPS:-*}\""]
