# TASKS — Factored AI & Data Hackathon 2026

**Flujo elegido:** Intake de disputas de transacciones con tarjeta (cargo no reconocido / cobro indebido) — MX, CO, AR.
**Tesis:** el LLM entiende y redacta; el código decide y actúa.
**Deadline:** "October 5th at midnight Colombia time" (kickoff 00:11:00). **Ambiguo**: puede ser las 00:00 del lunes 5 (= domingo 4 a la noche) o las 23:59 del 5. Se toma la lectura conservadora: **límite real domingo 4, 23:59 GMT-5**.
**Equipo:** 1 persona (Roberth). Ampliable — ver "Cómo sumar a alguien".

---

## SPRINT FINAL — sábado 3 oct (construir) · domingo 4 oct (video y envío)

Estado al arrancar: 158 tests en verde, backend completo (sesión, herramientas, política, orquestador, handoff, guardrails), **0 commits**, sin clasificador, sin eval set, sin frontend.

| # | Bloque | Tareas que cierra | Est. | Depende de |
|---|---|---|---|---|
| 1 | Repo: `.gitignore` final, commit inicial, repo en GitHub | repo + primer commit | 20 min | — |
| 2 | Corpus es/pt: traducir subconjunto de BANKING77 con Ollama (en background) + test escrito a mano | corpus capa 1, splits sin leakage | 30 min | — |
| 3 | Clasificador: baseline TF-IDF vs encoder multilingüe + conformal por idioma | baseline, candidato, conformal | 75 min | 2 |
| 4 | Integrar: intención desde el clasificador, LLM solo extrae campos (E-02b), reintentos acotados | reintentos + fallback | 40 min | 3 |
| 5 | Eval set ~150 casos, congelado con hash y tag `eval-v1` | 150 casos, casos pt, congelar | 45 min | — |
| 6 | Baseline "LLM ingenuo" + runner + scorecard con denominadores por idioma | baseline, corrida, scorecard, cortes | 60 min (+40 de corrida en background) | 4, 5 |
| 7 | Frontend Streamlit: chat cliente + consola CRM; `docker compose` | chat, CRM, deploy local | 75 min | 4 |
| 8 | Fixture incremental (el reto lo pide literal con datos estáticos) | fixture incremental, bronze | 30 min | — |
| 9 | Docs: `limitations.md`, README final; slides; guion del video | limitations, README, slides | 60 min | 6 |
| — | **Domingo 4:** grabar video (máx 3 min), revisar, enviar | video, envío, verificación | — | 9 |

**Recortado, con su porqué** (va a `limitations.md`):
- **Comparación Ollama vs API** — bloqueada: la clave de OpenAI devuelve HTTP 401 (verificado también en el proyecto del profesor, donde funcionó por última vez el 21 sep). El comparador (`eval/compare_providers.py`) queda listo.
- **Langfuse** — se reemplaza por trazas propias: cada `Turn` registra estados, reglas, latencia y evidencia, y `tools._audit` escribe el log de ejecución. Mismo dato, sin servicio extra.
- **SetFit con fine-tuning contrastivo** — en CPU tarda demasiado para el tiempo disponible. Se usa el encoder multilingüe congelado + cabeza lineal (la etapa final de SetFit sin la primera). Se declara.
- **Todas las P2** (curva riesgo-cobertura como figura, pass^k, cascada, LLM-as-judge).

---

## Cómo usar este archivo

- **P0** = sin esto no hay entrega. **P1** = sube la nota de forma clara. **P2** = solo si sobra tiempo.
- Cada tarea tiene un **criterio de hecho** verificable. Si no se puede verificar con un comando o un archivo, no está hecha.
- **Tracks:** `[DATA]` `[ML]` `[AGENT]` `[EVAL]` `[SHIP]`. Sirven para delegar en bloque.
- Marcá `[x]` al cerrar. No borres tareas descartadas: movelas a "Descartado" al final con una línea del porqué (eso alimenta `limitations.md`, que es criterio de evaluación).

### Cómo sumar a alguien
Si llega una segunda persona: entregale el track `[ML]` + `[EVAL]` completo (día 3 en adelante) y quedate con `[DATA]` + `[AGENT]`. Si llega una tercera: `[AGENT]` frontend + `[SHIP]`. El `TASKS.md` es el handoff; no hace falta reunión.

---

## Reglas que no se rompen

1. **No escribir código de producto hasta cerrar las 4 preguntas go/no-go del día 1.** Pueden cambiar el diseño.
2. **El eval set se congela el día 5 y no se toca.** Si hay que cambiarlo, se crea `v2` con un test nuevo y se documenta.
3. **Feature freeze el viernes 2 de octubre.** Después de esa fecha solo se arregla lo que la evaluación muestre roto.
4. **Ninguna métrica se reporta sin denominador.** Nunca "70% de resolución": siempre "70% (74/105 casos en alcance)".
5. **Las credenciales de AWS nunca entran al repo.** `.env` + `.gitignore`, verificado antes del primer push.
6. **Toda anomalía del dataset se documenta, no se esconde.** Los organizadores respondieron que encontrarlas y justificarlas es parte de la evaluación.

---

## DÍA 0 — Setup (hoy, sáb 27 sep)

- [x] **P0** `[SHIP]` Crear `.env` con las credenciales de S3 y `.gitignore` que lo excluya **antes** del primer commit. — *Hecho: `.env`, `data/` y la captura con las llaves están excluidos; `tests/test_no_secrets.py` lo verifica automáticamente (2 passed).*
- [x] **P0** `[DATA]` Script de descarga `pipeline/download.py` con **boto3**: paginación, reintentos adaptativos, skip de lo ya bajado, descarga a `.part` + rename, manifiesto con key/size/ETag. — *Hecho: 5 tablas de hechos (1.097 archivos c/u) + 6 dimensiones = 1,2 GB en `data/`; inventario en `docs/DATA_INVENTORY.md`.*
      > `digital_events` (3,5 GB) **no descargado**: no interviene en el flujo de disputas.
      > Plan B con AWS CLI no hizo falta — 0 fallos tras un reintento en `transactions`.
- [x] **P0** `[SHIP]` Nombre de equipo: **VerifiCargo**. — *Hecho: `pyproject.toml` actualizado.*
- [x] **P0** `[SHIP]` Crear repo público `factored-hackathon-2026-verificargo` y primer commit. — *Hecho: `git remote -v` apunta al repo y el push subió.* **→ github.com/jastk45/factored-hackathon-2026-verificargo, público**
- [x] **P0** `[SHIP]` Entorno con **uv**, Python 3.11 pineado, `duckdb polars boto3 python-dotenv` + `pytest`. — *Hecho: `uv.lock` generado.*
      > 3.11 y no 3.13 por las wheels de torch/sentence-transformers que arrastra SetFit.
- [x] **P0** `[SHIP]` LLM configurado: **Ollama local con `qwen3:1.7b`** (D-14). — *Hecho: extracción verificada sobre 4 casos reales del fixture; JSON válido, 2,3 s de latencia.*
      > Sin créditos del organizador, el modelo local es gratis e ilimitado para iterar. `LLM_PROVIDER` es una variable de entorno: cambiar de proveedor no toca código.
      > **La API key deja de ser bloqueante.** Se compara Ollama vs API el día 5 sobre los mismos casos (ver día 5).
- [x] **P1** `[SHIP]` `docs/DECISIONS.md` con las decisiones D-01 a D-06 y su porqué. — *Hecho.*
- [x] **P1** `[DATA]` **Adelantado del día 1:** verificados 4 hallazgos en `docs/EDA_FINDINGS.md` (F-01 a F-04).

---

## DÍA 1 — EDA go/no-go (dom 28 sep)

> **Estas 4 preguntas deciden el diseño. Nada de código de producto hasta responderlas.**

- [x] **P0** `[DATA]` Bronze: cargar los CSV/Parquet crudos a DuckDB con `ingested_at`, `source_file`, `batch_id`. — *Hecho: `SELECT count(*)` por tabla corre y coincide con `DATA_INVENTORY.md`.* **→ bronze = archivos crudos inmutables en data/ + manifiesto con ETag (download.py); silver lee de ahí**
- [x] **P0** `[DATA]` **Q1 — Enlazabilidad.** — *Resultado: **0 de 8.125** disputas con monto se enlazan a una transacción del cliente (F-06). Verificado que no es artefacto de la consulta.*
      → `LOCATE_TXN` se valida con un **fixture de disputas vinculadas** a transacciones reales, construido por el equipo y etiquetado `team-generated`.
- [x] **P0** `[DATA]` **Q2 — Validez de etiquetas.** — *Resultado: las 4 etiquetas son **ruido** — AUC 0,489 / 0,513 / 0,512 / 0,457 (F-09). `pipeline/label_validity.py`.*
      → **No hay segundo componente de ML supervisado.** Se reporta como resultado nulo documentado; el escalamiento queda en reglas deterministas.
- [x] **P0** `[DATA]` **Q3 — Plantillas de texto.** — *Resultado: `complaints.description` tiene **5 textos únicos** en 67.095 filas; solo **2** para disputas, y son el nombre de la categoría (F-05).*
      → **No hay texto de cliente en ninguna tabla del dataset.** El corpus es/pt se escribe desde cero + datasets externos autorizados.
- [x] **P0** `[DATA]` **Q4 — Conteo real.** — *Resultado: **67.095** quejas (no 80k), **24.491** disputas (36,50%), **20,11%** SLA incumplido (F-10). Coincide con lo reportado por otros equipos.*
- [x] **P0** `[DATA]` **Q5 — `affected_product_id`.** — *Resultado: **16.257 de 16.257 (100%)** apuntan a un producto de **otro cliente** (F-07). Cero excepciones.*
      → Check de calidad `dq_product_ownership` que falla en rojo + caso del set adversarial.
- [x] **P0** `[DATA]` Confirmar el hallazgo de Gabriel Silveira en Slack sobre `call_transcripts`. — *Resultado: **cero ocurrencias** de 14 términos del dominio en 171.321 registros (F-01), y solo **546 textos únicos** (F-02).*
- [x] **P1** `[DATA]` Gráfica de justificación del flujo. — *Hecho: `docs/img/flow_justification.png`, generada por `pipeline/plot_justification.py` (reproducible). Cuatro paneles: mezcla de subcategorías (36,5% disputas), SLA (20,2%), canal (50,5% call center) y demanda mensual sostenida (681/mes).*
- [x] **P0** `[SHIP]` **Congelar alcance por escrito** en `DECISIONS.md`: ~8 intenciones, 4 acciones (consultar transacciones, crear disputa, bloquear tarjeta, handoff), es/pt, 3 países. Todo lo demás queda fuera. — *Hecho: sección commiteada.*

---

## DÍA 2 — Pipeline y política (lun 29 sep)

> **Alcance recortado a propósito.** Solo pasan a silver las **4 tablas que el
> agente toca**: `customers`, `products`, `transactions`, `complaints`. Las otras
> 9 se quedan en bronze. Es una decisión documentada (D-12), no deuda técnica:
> construir contratos para tablas que el producto no consulta gastaría un día
> sin mover ninguna métrica.

- [x] **P0** `[DATA]` Silver de las 4 tablas: tipado, dedup por clave natural, normalización de catálogos (México/Mexico), conversión USD con `daily_exchange_rates`, flags `dq_*`, cuarentena de filas inválidas. — *Hecho: `make pipeline` corre de punta a punta.*
- [x] **P0** `[DATA]` Contratos en bronze→silver. Fallo de contrato = fila en cuarentena + métrica. — *Hecho: `pytest tests/test_contracts.py` en verde.*
      > Incluye `dq_product_ownership`, que **falla en rojo** por F-07 (100% de `affected_product_id` apunta a otro cliente). Es el check que demuestra el aislamiento por cliente.
- [x] **P0** `[DATA]` Gold `txn_lookup` indexada por cliente y fecha. — *Hecho: la tabla existe y `SELECT` por `customer_id` responde.*
      > Es el insumo del fixture (tarea siguiente) y la base del caso de demo de F-07.
- [x] **P0** `[DATA]` **Fixture de disputas vinculadas**, generado **desde silver** (no desde raw: si sale del crudo hay que rehacerlo tras la deduplicación). Se parte de transacciones reales y se generan disputas coherentes: mismo cliente, mismo monto, fecha dentro del plazo. Etiquetado `team-generated` con semilla fijada. — *Hecho: `fixtures/linked_disputes/` + test que verifica que cada disputa apunta a una transacción existente del mismo cliente.*
      > **Semilla y tolerancias distintas para el fixture de evaluación** que para el de desarrollo (D-11): otra semilla, otras tolerancias de monto y fecha, otra forma de nombrar comercios.
- [x] **P1** `[DATA]` Gold `dispute_cases` y `customer_360_min` (solo los campos que el servicio necesita — minimización de datos).
- [x] **P1** `[DATA]` `build_manifest.json`: hash de entradas, versión de contrato, conteos por capa, commit de git. — *Hecho: se genera en cada corrida.*
- [x] **P1** `[DATA]` Fixture incremental etiquetado: `fixtures/incremental_v2/` con 50 filas nuevas, 5 duplicadas, 3 tardías, 1 columna nueva. Test que demuestre que incremental == full rebuild. — *Hecho: `pytest tests/test_incremental.py` en verde.* **(El reto lo pide explícitamente si los datos son estáticos.)** **→ fixtures/incremental_v2 + tests/test_incremental.py (6/6): incremental == rebuild; el watermark por fecha pierde la llegada tardía**
- [x] **P0** `[AGENT]` `policy/dispute_policy.yaml` con IDs estables: `GATE-01` (plazo 90 días MX), `ESC-02` (monto > umbral), `ACT-03` (bloqueo requiere confirmación). Reglas de CO y AR **etiquetadas como sintéticas**. — *Hecho: YAML commiteado + `docs/dispute_policy.md` que lo explica.*
- [x] **P1** `[ML]` **Corpus capa 1:** descargar BANKING77, quedarse con el subconjunto relevante (27 de 77 intenciones), mapearlo al catálogo de 8 y traducirlo a es/pt. — *Hecho: `data/corpus/banking77_es_pt.jsonl` con `origin: external-public` en cada fila.*
      > Ver D-08b: son consultas reales de clientes bancarios. Da origen defendible al componente aprendido.

---

## DÍA 3 — Agente: esqueleto seguro (mar 30 sep)

- [x] **P0** `[AGENT]` Auth mock: token firmado de vida corta con `customer_id`, `country`, `lang_pref`, `auth_level`. — *Hecho: token expirado y token de otro cliente son rechazados en tests.*
- [x] **P0** `[AGENT]` Herramientas tipadas con el ciclo completo: autorizar → validar precondiciones → ejecutar → **re-leer y comparar** → log de auditoría → `{ok, verified, evidence_ids}`. — *Hecho: `pytest tests/test_authz.py` cubre el caso "cliente A pide datos de cliente B".*
      > El `customer_id` sale **siempre** del token, nunca de un argumento del LLM.
- [x] **P0** `[AGENT]` Policy engine: funciones puras + YAML, con test por cada regla. — *Hecho: `pytest tests/test_policy.py` en verde, una aserción por ID de regla.*
- [x] **P0** `[AGENT]` Máquina de estados: `AUTH → DETECT_LANG → GUARD → UNDERSTAND → (CLARIFY | ABSTAIN) → LOCATE_TXN → CHECK_POLICY → CONFIRM → ACT → VERIFY → RESPOND | ESCALATE`. LLM solo en `UNDERSTAND` y `RESPOND`. — *Hecho: `/chat` resuelve el camino normal en español.*
- [x] **P1** `[ML]` Definir catálogo de ~8 intenciones y mapear `complaints.subcategory` con reglas documentadas. — *Hecho: `docs/intent_catalog.md`.*
- [x] **P1** `[ML]` Splits sin leakage: dedup near-duplicates **antes** del split, split temporal + agrupado por `customer_id`. — *Hecho: script reproducible + conteos por split.* **→ test escrito a mano, independiente del entrenamiento; 1 texto filtrado entre train/test de BANKING77 eliminado**

---

## DÍA 4 — ML, guardrails y handoff (mié 1 oct)

- [x] **P0** `[ML]` Baseline TF-IDF char (2-5 gramas) + regresión logística, con MLflow. — *Hecho: macro-F1 registrado.* **→ macro-F1 0,550 (E-05); tracking en eval/reports/intent_classifier.json con hash de datos y commit**
- [x] **P0** `[ML]` Candidato SetFit sobre `multilingual-e5-base`. **Criterio de aceptación pre-registrado antes de ver el test.** — *Hecho: comparación en `docs/experiments.md`; si no supera al baseline, se despliega el baseline y se documenta.* **→ encoder e5 congelado + LR: macro-F1 0,681, +13,1 sobre el baseline (pre-registrado en 81f690c)**
- [x] **P1** `[ML]` **Abstención conformal** (CICC) sobre las probabilidades del clasificador: split conformal con cuantil **por idioma** (Mondrian), calibrado en un set held-out que incluya ejemplos `out_of_scope`. Router: |conjunto|=1 → `ACT`; 2≤|conjunto|≤3 → `CLARIFY` solo con esas opciones; vacío, OOS o >3 → `ESCALATE`. — *Hecho: cobertura empírica vs objetivo 1−α reportada por idioma en `docs/experiments.md`.* **→ cobertura 93,8% es / 96,9% pt; el cuantil global sub-cubre en es (87,5%)**
      > **Por qué P1 y no P2:** el corpus es escrito a mano y pequeño (D-08), así que el clasificador será inseguro. La conformal convierte esa incertidumbre en una garantía estadística de cobertura en vez de un umbral arbitrario — es la respuesta honesta a una debilidad real, no un adorno académico.
      > Da además el nodo `CLARIFY` de la máquina de estados sin heurísticas: se pregunta solo por las intenciones del conjunto.
- [x] **P0** `[AGENT]` Handoff JSON validado con Pydantic: hechos verificados con `evidence_id`, separados de lo que el cliente afirma sin verificar; acciones tomadas y no tomadas; `policy_trace`; preguntas abiertas. **Nunca se vuelca el transcript.** — *Hecho: `pytest tests/test_handoff_schema.py` en verde.*
- [x] **P0** `[AGENT]` Guardrails en capas: (1) el LLM no elige herramientas de escritura; (2) spotlighting del texto no confiable; (3) validación Pydantic de salida; (4) check determinista de que todo monto/fecha en la respuesta existe en la evidencia. — *Hecho: un ataque de inyección en el chat no dispara ninguna acción.*
- [x] **P1** `[AGENT]` Etiquetas de procedencia (`source ∈ {system, verified_tool, customer_text}`) + regla dura: ningún argumento de acción sensible viene de `customer_text` sin verificación. — *Hecho: regla en el YAML + test.*
- [x] **P0** `[EVAL]` Escribir los ~150 casos held-out en JSONL con `expected_outcome` ∈ {RESOLVE, CLARIFY, ABSTAIN, ESCALATE}. Mezcla: normal es (25), normal pt (20), ambiguo (20), requiere humano (20), datos malos (15), sesión/no autorizado (15), inyección (20), fallas de herramienta (15). — *Hecho: `eval/cases/*.jsonl` completo.* **→ 159 casos, 12 bloques, etiquetas derivadas de la política escrita**
- [x] **P1** `[EVAL]` Los ~100 casos en portugués: escritos o revisados a mano, **no solo traducidos**. Reportar traducido y escrito por separado. — *Hecho: archivo separado + nota en `limitations.md`.* **→ 57 casos pt en el sistema + 64 en el test del clasificador, escritos a mano**

---

## DÍA 5 — Frontend, observabilidad y congelar eval (jue 2 oct)

- [x] **P0** `[EVAL]` **CONGELAR el eval set.** A partir de acá no se toca. — *Hecho: tag de git `eval-v1`.* **→ tag eval-v1 con SHA-256 del archivo**
- [x] **P0** `[AGENT]` Chat de cliente con selector es/pt, login de prueba y tarjetas de "acción verificada ✅" con `evidence_id`. — *Hecho: los 3 caminos se navegan en la UI.* **→ app/ui.py (vista Cliente)**
- [x] **P1** `[AGENT]` Consola CRM del agente humano: cola priorizada, paquete de handoff, timeline de la transacción, reglas evaluadas, temporizador de SLA regulatorio. — *Hecho: un handoff generado aparece en la cola.* **→ app/ui.py (vista Agente) + app/handoff_queue.py**
- [x] **P0** `[AGENT]` Reintentos acotados (máx 2 con backoff) y fallback seguro a ESCALATE. — *Hecho: test que inyecta timeout y verifica el escalamiento.* **→ app/llm.py + fallback a regex; orquestador escala ante cualquier excepción**
- [~] **P0** `[EVAL]` **Comparar Ollama vs API** **→ BLOQUEADO: la clave de OpenAI da HTTP 401; documentado en limitations.md** sobre los mismos casos: calidad de extracción, latencia y costo, por idioma. — *Hecho: tabla en `docs/experiments.md`; decide con qué proveedor se presenta.*
      > **No dejar esto para el día 6.** Un prompt afinado sobre qwen3 no se comporta igual en Haiku, sobre todo en extracción estructurada y portugués. Descubrirlo con el feature freeze encima costaría medio día.
      > La tabla resultante es material de slide: trade-off explícito entre costo, latencia y calidad.
- [x] **P1** `[SHIP]` Observabilidad (Langfuse o equivalente): traza por caso con `case_id`, `lang`, `country`, tokens, costo, latencia. — *Hecho: p50/p95 y costo salen de las trazas, no de estimaciones.* **→ reemplazado por trazas propias (Turn + audit_log); ver Sprint final**
- [x] **P2** `[AGENT]` Modo offline sin API key (plantillas deterministas) para que los jueces puedan probarlo siempre. — *Hecho: corre con `LLM_ENABLED=false`.* **→ LLM_PROVIDER=none**

---

## DÍA 6 — Feature freeze y primera evaluación (vie 3 oct)

- [x] **P0** `[SHIP]` **FEATURE FREEZE.** Desde acá solo se arregla lo que la evaluación muestre roto. **→ congelado tras v3; solo correcciones documentadas**
- [x] **P0** `[EVAL]` Baseline de sistema: "LLM ingenuo con todas las herramientas y la política en el prompt". — *Hecho: corre sobre el mismo eval set.* **→ eval/runner.py, NaiveAgent**
- [x] **P0** `[EVAL]` Corrida completa: sistema propuesto vs baseline sobre el mismo workload. — *Hecho: `eval/reports/scorecard_v1.md` generado.* **→ baseline + v1 (pre-registrada) + v2 + v3 en eval/reports/**
- [x] **P0** `[EVAL]` Scorecard con **denominadores** en cada fila: safe automated resolution, containment, escalation quality (transferencias omitidas y innecesarias), unsafe outcomes (por tipo), latencia p50/p95, costo por caso y por resolución exitosa. — *Hecho: tabla completa, sin celdas vacías; "no definido" donde corresponda.* **→ docs/experiments.md (E-06) y vista Evaluación**
- [x] **P1** `[EVAL]` Cortes por idioma (es/pt), país y segmento, con nota de muestra pequeña. — *Hecho: tabla segmentada.* **→ by_language en cada reporte; segmento/país no (muestra chica)**
- [~] **P1** `[SHIP]` Deploy accesible + `docker compose up` reproducible. **→ make serve probado; Dockerfile multi-etapa escrito, SIN PROBAR (daemon de Docker apagado)** **Cloud no es obligatorio** (confirmado por los organizadores): si es local, documentar la ruta a producción. — *Hecho: un tercero puede levantarlo siguiendo el README.*

---

## DÍA 7 — Arreglos y documentación (sáb 4 oct — DÍA DE ENTREGA)

- [x] **P0** `[EVAL]` Arreglar lo que la evaluación mostró roto. **Sin tocar el eval set.** — *Hecho: scorecard final regenerado.* **→ v1: conformal inutilizable en es -> v2; v2: B01-0072 inseguro -> v3 con test de regresión**
- [x] **P0** `[SHIP]` `docs/limitations.md`: portugués sin datos reales, texto plantillado, normativa CO/AR sintética, etiquetas sin señal, tamaños de muestra, qué falta para producción. — *Hecho: archivo completo.* **(Los organizadores dijeron explícitamente que documentar lo que falta suma.)** **→ docs/limitations.md**
- [x] **P0** `[SHIP]` README final: pitch, arquitectura, cómo correr, scorecard, limitaciones. — *Hecho: alguien que no conoce el proyecto lo levanta siguiendo el README.* **→ README.md**
- [x] **P0** `[SHIP]` Slides (4-6): problema y datos → arquitectura → demo de los 3 caminos → scorecard → ruta a producción. — *Hecho: PDF listo.* **→ docs/VerifiCargo.pptx (6 slides, números leídos de los reportes)**
- [ ] **P0** `[SHIP]` Video pitch **máximo 3 minutos**: los 3 caminos en es y pt, una inyección bloqueada con su traza, el handoff llegando a la consola. — *Hecho: archivo grabado y revisado.*
- [ ] **P0** `[SHIP]` **ENVIAR a hackathon.admin@factored.ai**: link del repo, link del deploy, slides, video. — *Hecho: email enviado con acuse.*

---

## DÍA 8 — Contingencia (dom 5 oct)

- [ ] **P0** `[SHIP]` Verificar que el repo es público (o compartido con los jueces y avisado por email).
- [ ] **P0** `[SHIP]` Verificar que el link del deploy responde.
- [ ] **P2** `[SHIP]` Reemplazar el video si hace falta.

> Cierre real: **5 oct 23:59 GMT-5**. Este día es colchón, no plan.

---

## Técnicas del informe de papers — estado tras el EDA

El EDA del día 1 invalidó parte del informe. Este es el balance.

### Vivas y reforzadas

| Técnica | Estado tras el EDA |
|---|---|
| **Abstención conformal** (CICC, NAACL 2024) | **Sube a P1.** Con un corpus escrito a mano el clasificador será *menos* seguro, no más: poder garantizar "actúo solo si el conjunto tiene 1 intención" deja de ser adorno y pasa a ser la respuesta a una debilidad real. Ver día 4. |
| **Action-selector / CaMeL-lite** | Intacta y reforzada: **F-07** (100% de `affected_product_id` apunta a otro cliente) es la demostración perfecta de por qué la autorización vive en la capa de herramientas. Ya está en el día 4. |
| **pass^k, métricas estilo AgentDojo** | Intactas: son metodología de evaluación, no dependen del dataset. |
| **Dedup MinHash / split por plantilla** | Ya no hace falta aplicarla — pero el hallazgo que la motivaba (**F-02**: 546 textos únicos; **F-05**: 5 textos únicos) es ahora un resultado principal de la entrega. |

### Muertas por el EDA — se reportan como tales

| Técnica | Por qué cae |
|---|---|
| Modelo de escalamiento / prioridad | **F-09**: las cuatro etiquetas son ruido (AUC 0,46–0,51). Documentado como resultado nulo en D-09. |

### Revividas por BANKING77 (corrección del 28 sep)

Se habían dado por muertas asumiendo que no quedaba texto fuente. Es falso:
**BANKING77 son consultas reales de clientes bancarios** y 27 de sus 77
intenciones son del dominio de disputas, varias con correspondencia casi 1:1
(`card_payment_not_recognised`, `transaction_charged_twice`,
`extra_charge_on_statement`, `lost_or_stolen_card`, `request_refund`,
`declined_card_payment`, `pending_card_payment`…).

| Técnica | Nuevo estado |
|---|---|
| **`translate-train`** | **Viva.** Se traduce el subconjunto relevante de BANKING77 a es y pt. Da origen defendible al corpus: "consultas reales de clientes bancarios, traducidas y adaptadas", en vez de 100% inventado por el equipo. |
| **Cascada / SetFit vs TF-IDF** | **Viva.** Con BANKING77 traducido más los ejemplos propios hay volumen suficiente para entrenar y comparar de verdad. |

---

## Ideas P2 (solo si sobra tiempo, en este orden)

- [ ] **P2** `[EVAL]` Curva riesgo-cobertura: conformal vs umbral fijo, por idioma. *(La implementación conformal ya está en P1; esto es la figura comparativa.)*
- [ ] **P2** `[EVAL]` `pass^k` sobre 20 escenarios: orquestador vs baseline ReAct. Si el baseline tiene alta varianza, valida la arquitectura.
- [ ] **P2** `[ML]` Cascada de modelos con ahorro medido + gráfica costo/latencia. **Solo si el corpus propio llega a tamaño suficiente.**
- [ ] **P2** `[EVAL]` LLM-as-judge solo para tono, validado contra ~40 casos humanos (reportar kappa). Seguridad se juzga **determinísticamente** desde el log de acciones.

---

## Preguntas resueltas (no volver a preguntar en Slack)

| Pregunta | Respuesta | Fuente |
|---|---|---|
| Deadline exacto | 5 oct, medianoche hora Colombia (GMT-5) | Kickoff [00:11:00] |
| ¿Créditos / API keys? | No. Free tiers recomendados | Kickoff [00:42:37] |
| Largo del video | Máximo 3 minutos | Kickoff [00:43:15] |
| ¿Deploy 24/7? | No; los jueces coordinan el acceso | Kickoff [00:30:21] |
| ¿Datasets externos? | Sí, justificándolos | Antonio → Kevin Vicent, Slack |
| ¿Cloud obligatorio? | No; importa la ruta creíble a producción | Antonio → marciopg, Slack |
| ¿El componente aprendido debe entrenarse desde cero? | No; un LLM prompted o fine-tuned cuenta si se evalúa con rigor | Antonio → Kevin Soto, Slack |
| ¿Repo privado? | Permitido si se comparte con los jueces y se avisa | Kickoff [00:29:13] |

---

## Descartado

*(Mover acá lo que se decida no hacer, con una línea del porqué. Alimenta `limitations.md`.)*

- **YOLO / visión por computador** — el dataset no tiene imágenes; no hay ground truth para entrenar ni evaluar.
- **Spark / Databricks** — 19M filas caben en DuckDB en una laptop; añadiría fricción sin beneficio en 8 días. Se menciona como ruta de escalado.
- **OPA / Cerbos** — funciones puras + YAML se testean igual y se defienden igual. Se menciona como ruta a producción.
- **Fine-tuning de LLM generativo** — no hay tiempo ni datos de diálogo de calidad.
- **Emuladores cloud locales (LocalStack o similar)** — el pipeline corre en DuckDB sobre archivos ya descargados de S3; no hay servicios AWS que emular. Los organizadores confirmaron que cloud no es obligatorio. La ruta a producción se comunica con `docker compose` + un `cdk/` sin desplegar.
