# VerifiCargo · imagen de la demo (UI + orquestador)
#
# Los datos NO van en la imagen: el gold y el modelo se montan como volúmenes
# (ver docker-compose.yml). Así la imagen no contiene datos de clientes, y el
# pipeline sigue siendo el único camino para producirlos.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv
WORKDIR /app

# Dependencias primero, para aprovechar la caché de capas.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY app/ app/
COPY policy/ policy/
COPY models/ models/
COPY docs/img/ docs/img/
COPY eval/reports/ eval/reports/

# El encoder multilingüe se descarga en el build para que el contenedor
# arranque sin red.
RUN uv run --no-dev python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small')"

ENV LLM_PROVIDER=none \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8
EXPOSE 8501
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"
CMD ["uv", "run", "--no-dev", "streamlit", "run", "app/ui.py", "--server.address=0.0.0.0", "--server.port=8501"]
