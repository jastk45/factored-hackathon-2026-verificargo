# VerifiCargo · imagen de la demo: frontend React compilado + API FastAPI.
#
# Los datos NO van en la imagen: el gold se monta como volumen (ver
# docker-compose.yml). Así la imagen no contiene datos de clientes, y el
# pipeline sigue siendo el único camino para producirlos.

# --- 1. frontend --------------------------------------------------------
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# --- 2. API -------------------------------------------------------------
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ app/
COPY policy/ policy/
COPY models/ models/
COPY eval/reports/ eval/reports/
COPY --from=web /web/dist frontend/dist

# El encoder multilingüe se descarga en el build: el contenedor arranca sin red.
RUN uv run --no-dev python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

ENV LLM_PROVIDER=none PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
EXPOSE 8000
# Railway (y otros PaaS) asignan el puerto en $PORT; en local, 8000.
CMD ["sh", "-c", "uv run --no-dev python -m uvicorn api:app --app-dir app --host 0.0.0.0 --port ${PORT:-8000}"]
