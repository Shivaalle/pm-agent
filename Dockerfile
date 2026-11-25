# ==============================================================================
# AI Project Manager Agent — single container for free hosts (Render, Fly, etc.)
# Builds the React SPA, then serves both the API and the SPA from FastAPI.
# The app is reachable at /app/ (root / redirects there).
# ==============================================================================

# 1. Build the frontend -------------------------------------------------------
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build


# 2. Python runtime ----------------------------------------------------------
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ ./backend/
COPY data/ ./data/
COPY --from=frontend-build /frontend/dist ./frontend/dist

EXPOSE 8000

# Render injects PORT; 8000 keeps local `docker run` simple.
CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port \"${PORT:-8000}\""]
