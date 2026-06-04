# Field Desk app image (api / worker / cron share it). Built from the repo root
# (compose sets context: ..). Node is included for the Plan 6 google-play sidecar;
# cron for the scheduled jobs; aria2/ffmpeg for creative media (Plan 6 system deps).
FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        cron postgresql-client nodejs npm aria2 ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
RUN cd src/agf/enrich_full/sidecar && npm install --omit=dev || true   # Android sidecar deps

ENV PYTHONPATH=/app/src
RUN mkdir -p /var/log/agf
EXPOSE 8000
CMD ["uvicorn", "agf.api.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
