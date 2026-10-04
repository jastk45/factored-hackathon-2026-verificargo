# VerifiCargo · comandos reproducibles. En Windows sin make, copiar el comando.

.PHONY: setup data pipeline train eval test web serve ui docker

setup:            ## dependencias exactas desde uv.lock
	uv sync

data:             ## descarga el dataset (requiere credenciales en .env) y construye silver/gold
	uv run python pipeline/download.py --prefix data/complaints/
	uv run python pipeline/download.py --prefix data/transactions/
	uv run python pipeline/download.py --prefix data/customers.csv
	uv run python pipeline/download.py --prefix data/products.csv
	uv run python pipeline/download.py --prefix data/daily_exchange_rates.csv
	$(MAKE) pipeline

pipeline:         ## silver con contratos, gold, fixtures
	uv run python pipeline/silver.py
	uv run python pipeline/gold.py
	uv run python pipeline/build_fixture.py
	uv run python pipeline/build_incremental_fixture.py

train:            ## clasificador de intención (E-05)
	uv run python pipeline/build_corpus.py
	uv run python ml/train_intent.py

eval:             ## sistema vs baseline sobre el eval set congelado (requiere Ollama)
	uv run python eval/runner.py --system proposed
	uv run python eval/runner.py --system baseline
	uv run python eval/runner.py --report

test:             ## toda la suite
	uv run pytest -q

web:              ## compila el frontend React
	cd frontend && npm ci && npm run build

serve:            ## API + frontend en http://localhost:8000
	uv run python -m uvicorn api:app --app-dir app --port 8000

ui:               ## interfaz alternativa en Streamlit (respaldo)
	uv run streamlit run app/ui.py

docker:           ## demo en contenedor, sin modelo
	docker compose up --build
