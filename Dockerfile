# BlindSpot — single-image build.
#
# Stage 1 builds the React frontend when Node is available. Stage 2 runs the
# Python backend, which serves the built UI automatically.
#
#   docker build -t blindspot .
#   docker run --rm -p 8000:8000 -v blindspot-data:/app/data blindspot
#
# To index a local repository from inside the container, mount it read-only:
#   docker run --rm -p 8000:8000 -v C:\Projects\my-app:/repos/my-app:ro blindspot
# then index the path /repos/my-app from the UI.

# ---------------------------------------------------------------- frontend
FROM node:20-alpine AS frontend
WORKDIR /build
COPY frontend/package.json ./
# No lockfile is committed for the POC, so `npm install` is correct here.
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------------------------------------------------------------- backend
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY scripts/ ./scripts/
COPY pyproject.toml ./

# The built UI; the backend prefers frontend/dist when it exists.
COPY --from=frontend /build/dist ./frontend/dist

# Run as a non-root user: BlindSpot reads user repositories, so it should never
# hold more privilege than reading them requires.
RUN useradd --create-home --uid 10001 blindspot \
    && mkdir -p /app/data \
    && chown -R blindspot:blindspot /app
USER blindspot

ENV BLINDSPOT_HOST=0.0.0.0 \
    BLINDSPOT_PORT=8000 \
    BLINDSPOT_DATABASE_URL=sqlite:////app/data/blindspot.db \
    BLINDSPOT_INDEX_PATH=/app/data/index \
    BLINDSPOT_FRONTEND_DIR=/app/frontend

EXPOSE 8000
WORKDIR /app/backend

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
