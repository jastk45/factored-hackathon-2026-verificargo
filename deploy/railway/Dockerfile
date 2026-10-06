# VerifiCargo · solo el backend (API FastAPI), para Railway.
#
# El frontend vive en Netlify y llama a esta API (CORS con WIDGET_ORIGINS).
# A diferencia del Dockerfile de la raíz, esta imagen SÍ lleva warehouse/gold:
# se construye desde la carpeta que arma deploy/railway/build_bundle.py y se
# sube con `railway up`, sin pasar por GitHub.

FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ app/
COPY policy/ policy/
COPY models/ models/
COPY eval/reports/ eval/reports/
COPY warehouse/gold/ warehouse/gold/

# El encoder multilingüe se descarga en el build: el contenedor arranca sin
# bajar nada.
RUN uv run --no-dev python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8
# Railway asigna el puerto en $PORT.
CMD ["sh", "-c", "uv run --no-dev python -m uvicorn api:app --app-dir app --host 0.0.0.0 --port ${PORT:-8000}"]
