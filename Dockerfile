# VerifiCargo · backend (API FastAPI). El frontend se publica aparte (Netlify)
# y llama a esta API; CORS se controla con WIDGET_ORIGINS.
#
# La imagen lleva el gold MÍNIMO de la demo (deploy/demo_gold: solo los
# clientes de los escenarios, unos KB). Para correr con el gold completo,
# montá ./warehouse como volumen (docker-compose.yml): reemplaza al mínimo.

FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ app/
COPY policy/ policy/
COPY models/ models/
COPY eval/reports/ eval/reports/
COPY deploy/demo_gold/ warehouse/gold/

# El encoder multilingüe se descarga en el build: el contenedor arranca sin red.
RUN uv run --no-dev python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

ENV LLM_PROVIDER=none PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
EXPOSE 8000
# Railway (y otros PaaS) asignan el puerto en $PORT; en local, 8000.
CMD ["sh", "-c", "uv run --no-dev python -m uvicorn api:app --app-dir app --host 0.0.0.0 --port ${PORT:-8000}"]
