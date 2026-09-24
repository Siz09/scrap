# syntax=docker/dockerfile:1
# One image, two roles: the website (default command) and the scraper (`devicescout schedule`).

# ---- 1. build the Vite frontend -------------------------------------------------
FROM node:22-slim AS web
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# vite writes to ../devicescout/web/dist
RUN npm run build

# ---- 2. Python runtime ------------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEVICESCOUT_HOME=/data
WORKDIR /app

COPY pyproject.toml README.md ./
COPY devicescout/ devicescout/
COPY --from=web /src/devicescout/web/dist devicescout/web/dist
RUN pip install .

# Browser-based scrapers (Playwright/Camoufox) add ~600 MB. Off by default: the HTTP
# scrapers handle Shopify, WooCommerce, Daraz JSON and JSON-LD sites. Enable with
#   docker build --build-arg WITH_BROWSERS=true .
ARG WITH_BROWSERS=false
RUN if [ "$WITH_BROWSERS" = "true" ]; then scrapling install; fi

RUN useradd --create-home --uid 10001 scout && mkdir -p /data && chown scout /data
USER scout
VOLUME ["/data"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"

# Public website: read-only (no scraping from the browser), listening on all interfaces.
CMD ["devicescout", "serve", "--host", "0.0.0.0", "--port", "8765", "--no-browser", "--read-only"]
