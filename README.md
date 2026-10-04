# VerifiCargo — intake de disputas de tarjeta

Sistema de atención al cliente AI-first para el **intake de disputas de cargos
con tarjeta** (cargo no reconocido, cobro indebido) en México, Colombia y
Argentina.

> Factored AI & Data Hackathon 2026 · en construcción

**Principio de diseño:** el LLM entiende y redacta; el código decide y actúa.
Autenticación, autorización, elegibilidad, ejecución de acciones y decisión de
escalar son deterministas y viven fuera del modelo.

---

## Setup

Requiere [uv](https://docs.astral.sh/uv/). El intérprete lo descarga uv solo.

```bash
uv sync                      # instala dependencias desde uv.lock
cp .env.example .env         # y rellenar las credenciales
uv run python pipeline/download.py
```

Las credenciales de S3 están en la primera página del Data Dictionary del
hackathon. **Nunca se commitean:** `uv run pytest tests/test_no_secrets.py`
falla si alguna se cuela en un archivo versionado.

### Descarga selectiva

`digital_events` pesa 3,5 GB de los 5,0 GB del dataset y no interviene en este
flujo. Para bajar solo una tabla:

```bash
uv run python pipeline/download.py --prefix data/complaints/
uv run python pipeline/download.py --dry-run     # listar sin descargar
```

El script es idempotente: si se corta, se relanza y continúa.

---

## Estructura

```
docs/         DECISIONS.md · EDA_FINDINGS.md · DATA_INVENTORY.md
pipeline/     descarga y capas bronze/silver/gold
policy/       dispute_policy.yaml — reglas con ID estable
eval/cases/   casos held-out en JSONL
tests/
TASKS.md      plan de trabajo con prioridades y criterios de "hecho"
```

---

## Estado

Días 0 y 1 completados: entorno, descarga reproducible y EDA go/no-go. Ver
[TASKS.md](TASKS.md) para el plan, [docs/EDA_FINDINGS.md](docs/EDA_FINDINGS.md)
para los hallazgos con sus consultas, y [docs/DECISIONS.md](docs/DECISIONS.md)
para el porqué de cada decisión.

## Lo que el EDA encontró

El dataset es una simulación **estadística** de un banco, no **causal**: las
distribuciones son realistas, pero las relaciones entre tablas y las etiquetas
de resultado se generaron de forma independiente.

| Hallazgo | Evidencia |
|---|---|
| Los `call_transcripts` no contienen **ninguna** conversación de disputa | 0 ocurrencias de 14 términos del dominio en 171.321 registros |
| El texto está plantillado | 546 textos únicos en transcripts; **5** en `complaints.description`, y solo **2** para disputas |
| Ninguna disputa se enlaza con una transacción real | 0 de 8.125, verificado contra tres controles |
| `affected_product_id` apunta siempre a **otro cliente** | 16.257 de 16.257 (100%) |
| Las etiquetas de resultado son ruido | AUC 0,489 / 0,513 / 0,512 / 0,457 |

**Lo que sí sirve:** volúmenes y proporciones (24.491 disputas, 36,5% de las
quejas, 20,2% de SLA incumplido, 50,5% por call center), las dimensiones,
`transactions` como universo de hechos verificables, y el particionado por fecha
para demostrar carga incremental.

![Justificación del flujo](docs/img/flow_justification.png)

## Cómo se responde a eso

- El corpus del componente aprendido se arma en tres capas con procedencia
  etiquetada: **BANKING77** traducido a es/pt (consultas reales de clientes
  bancarios; 27 de sus 77 intenciones son del dominio de disputas), ejemplos
  escritos por el equipo, y **MASSIVE** como material fuera de alcance.
- `LOCATE_TXN` se valida con un **fixture de disputas vinculadas** a
  transacciones reales, generado con semilla fijada y etiquetado
  `team-generated`.
- **No hay modelo de escalamiento**: se reporta como resultado nulo documentado
  y la decisión queda en reglas deterministas.
- La **abstención conformal** convierte la incertidumbre de un clasificador con
  pocos datos en una garantía de cobertura por idioma.
