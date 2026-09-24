# syntax=docker/dockerfile:1
# Two images from one file:
#   target "web"      the public website (small; default target)
#   target "scraper"  scheduled scraping + jobs queued from the website, with browser scrapers

# ---- frontend -----------------------------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src/web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
# vite writes to ../devicescout/web/dist
RUN npm run build

# ---- shared Python base ----------------------------------------------------------------
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DEVICESCOUT_HOME=/data \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
WORKDIR /app
COPY pyproject.toml README.md ./
COPY devicescout/ devicescout/
COPY --from=frontend /src/devicescout/web/dist devicescout/web/dist
RUN pip install . \
 && useradd --create-home --uid 10001 scout && mkdir -p /data && chown scout /data
VOLUME ["/data"]

# ---- scraper: every scraper in the fallback chain, ready to run ------------------------
FROM base AS scraper
# Chromium for scrapling-dynamic and Crawl4AI (playwright), patched Chromium for
# scrapling-stealth (patchright), their system libraries, and the optional scrapers.
# Firecrawl is a paid hosted service: it activates when FIRECRAWL_API_KEY is set.
RUN pip install "crawl4ai>=0.7" "firecrawl-py>=4" \
 && python -m playwright install --with-deps chromium \
 && python -m patchright install chromium \
 && chmod -R a+rX /ms-playwright \
 && rm -rf /var/lib/apt/lists/*
USER scout
CMD ["devicescout", "schedule", "--every", "6h", "--check-first"]

# ---- web (default target) ----------------------------------------------------------------
FROM base AS web
USER scout
EXPOSE 8765
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8765/api/health', timeout=4)"
# Read-only for visitors. With DEVICESCOUT_ADMIN_KEY set, whoever has the key can queue
# checks and updates from the Data sources page; the scraper container runs them.
CMD ["devicescout", "serve", "--host", "0.0.0.0", "--port", "8765", "--no-browser", "--read-only"]
