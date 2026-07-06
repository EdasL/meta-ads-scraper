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
# Chromium for funnel_capture's rendered-DOM probes (playwright itself comes from
# requirements.txt). --with-deps pulls the browser's apt libraries.
RUN playwright install --with-deps chromium && rm -rf /var/lib/apt/lists/*

COPY . /app
# Android sidecar deps. The COPY above may bring a host node_modules along —
# wipe it and install fresh, and FAIL the build if the install or the module
# tree is broken (a silent `|| true` here shipped a corrupt undici that killed
# Android triage in prod for a week: every gp_lookup crashed, whole Android
# candidate groups stayed 'new' forever).
RUN cd src/agf/enrich_full/sidecar && rm -rf node_modules \
    && npm install --omit=dev \
    && node -e "require('google-play-scraper')"

ENV PYTHONPATH=/app/src
RUN mkdir -p /var/log/agf
EXPOSE 8000
CMD ["uvicorn", "agf.api.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
