# Hallazgos del EDA

Cada hallazgo indica cómo se verificó y qué consecuencia tiene sobre el diseño.
Todos los números son reproducibles con las consultas incluidas.

> **Conclusión general del día 1.** El dataset es una simulación *estadística* de
> un banco, no una simulación *causal*. Los volúmenes, las proporciones y las
> distribuciones son realistas y sirven para dimensionar el problema; pero las
> relaciones entre tablas, el texto libre y las etiquetas de resultado se
> generaron de forma independiente y no se sostienen. Esto **no invalida el
> proyecto**: cambia de dónde salen los datos del componente aprendido y
> convierte la validación de datos en una pieza central de la entrega.

---

## Resumen

| # | Hallazgo | Impacto |
|---|---|---|
| F-01 | Los `call_transcripts` no contienen **ninguna** conversación de disputa | Alto |
| F-02 | Texto plantillado: 546 textos únicos en 171.321 transcripts | Alto |
| F-03 | `detected_intents` vale `consulta_general` en el 95% de los casos | Medio |
| F-04 | El dataset es CSV particionado, no Parquet | Bajo |
| F-05 | `complaints.description` tiene **5 textos únicos** en 67.095 filas | Crítico |
| F-06 | **Ninguna** disputa se enlaza con una transacción real del cliente | Crítico |
| F-07 | El **100%** de `affected_product_id` apunta a un producto de **otro cliente** | Crítico (seguridad) |
| F-08 | `origin_interaction_id` está vacío en el 100% de las disputas | Alto |
| F-09 | Las cuatro etiquetas de resultado son ruido (AUC 0,46–0,51) | Alto |
| F-10 | Volúmenes confirmados: 67.095 quejas, 36,50% disputas, 20,11% SLA incumplido | Positivo |

---

## F-01 · Los `call_transcripts` no contienen conversaciones de disputa

**Verificado:** 27 sep 2026 · Descarta una fuente de entrenamiento

Se buscaron 14 expresiones del dominio sobre las 171.321 transcripciones:
`no reconozco`, `no reconocido`, `cargo no`, `desconozco`, `fraude`,
`reembolso`, `cobro indebido`, `duplicado`, `robaron`, `clonaron`, `disputa`,
`reclamo`, `devolución`, `no autoricé`.

**Cero ocurrencias en los catorce casos.** Confirma de forma independiente lo
reportado por otro participante en el canal de Slack.

```sql
SELECT count(*) FROM read_csv_auto('data/call_transcripts/*/*/*/*.csv',
       union_by_name=true, ignore_errors=true)
WHERE lower(full_text) LIKE '%no reconoz%';   -- 0
```

---

## F-02 · Texto plantillado: 546 textos únicos en 171.321 transcripts

**Verificado:** 27 sep 2026

Solo el **0,3%** de las filas tiene texto distinto. Un clasificador entrenado
sobre este campo con split aleatorio vería en test casi las mismas frases que en
entrenamiento y reportaría un accuracy altísimo y engañoso.

---

## F-03 · `detected_intents` no discrimina

**Verificado:** 27 sep 2026

| Valor | Filas |
|---|---:|
| `consulta_general` | 162.864 (95%) |
| `NULL` | 8.457 |

`main_topics` sí varía (Transaccional 59.786, Producto 37.658, Queja 29.198,
Técnico 25.691, Comercial 13.808, Retención 5.180), pero es una categoría gruesa
de negocio y F-01 muestra que el texto bajo "Queja" no habla de quejas.

---

## F-04 · Estructura real: CSV particionado

**Verificado:** 27 sep 2026

7.671 archivos, 5,0 GB. Seis dimensiones como CSV plano en la raíz; siete tablas
de hechos particionadas `year=/month=/day=` (1.097 archivos cada una, salvo
`campaign_sends` con 1.083). `digital_events` pesa 3,5 GB de los 5,0 GB y no
interviene en este flujo: no se descarga.

La capa bronze lee con `union_by_name` (el dataset advierte evolución de
esquema) y convierte a Parquet.

---

## F-05 · `complaints.description` tiene 5 textos únicos en 67.095 filas

**Verificado:** 27 sep 2026 · **Crítico**

```sql
SELECT count(DISTINCT description) FROM complaints;              -- 5
SELECT count(DISTINCT description) FROM complaints WHERE <disputa>;  -- 2
```

Las **dos** descripciones que existen para disputas son:

- `Queja relacionada con transactions`
- `Queja relacionada con fees`

Es el nombre de la categoría, sin un solo dato del caso: ni monto, ni comercio,
ni fecha, ni relato del cliente.

**Consecuencia — la más importante del EDA.** Sumado a F-01 y F-02, **el dataset
no contiene texto de cliente en ninguna tabla**. El corpus de entrenamiento y de
evaluación del componente conversacional debe construirse por otra vía:
mensajes escritos por el equipo en español y portugués, más datasets externos
públicos (BANKING77, MASSIVE), cuyo uso los organizadores autorizaron
explícitamente en Slack siempre que se justifique. Todo material generado se
etiqueta como `team-generated` según pide el reto.

---

## F-06 · Ninguna disputa se enlaza con una transacción real del cliente

**Verificado:** 27 sep 2026 · **Crítico**

De las 24.491 disputas, 8.125 (33,2%) traen `claimed_amount`. Se intentó
enlazarlas con `transactions` por cliente, monto exacto y una ventana de 90 días
anteriores a la queja:

```
enlazables: 0 / 8.125  (0,00%)
```

No es un artefacto de la consulta. Los controles lo descartan:

- 7.097 de los 7.900 clientes con disputa **sí tienen** transacciones.
- 7.444 de los 8.125 montos reclamados **sí existen** en `transactions`, pero
  siempre en otro cliente o fuera de la ventana.
- Los rangos son incompatibles: `claimed_amount` va de 51 a 4.999 (media 2.528),
  mientras `amount` va de 5 a 39.999.828 (media 1.913.670).

Los montos reclamados se generaron de forma independiente de las transacciones.

**Consecuencia.** El paso `LOCATE_TXN` no puede validarse contra el histórico
real. Se resuelve con un **fixture de casos vinculados** construido por el
equipo: se toman transacciones reales del dataset y se generan disputas
coherentes sobre ellas (mismo cliente, mismo monto, fecha dentro del plazo). El
fixture se etiqueta como `team-generated` y se documenta. La lógica de matching
difuso (tolerancia de monto, ventana de fecha, similitud de comercio) es la
misma que en producción; lo que cambia es el origen de los datos.

---

## F-07 · El 100% de `affected_product_id` apunta a un producto de otro cliente

**Verificado:** 27 sep 2026 · **Crítico — riesgo de aislamiento**

```
disputas con affected_product_id : 16.257
del MISMO cliente                :      0   (0,00%)
product_id inexistente           :      0
de OTRO cliente                  : 16.257   (100,00%)
```

Cero excepciones en 16.257 registros. El `product_id` existe siempre, pero
**nunca** pertenece al cliente que puso la queja.

**Consecuencia.** Un join ingenuo de queja a producto filtraría datos entre
clientes en **todos** los casos. Se convierte en un check de calidad que falla
en rojo (`dq_product_ownership`) y en un caso del set adversarial: el sistema
debe negarse a mostrar ese producto. Es, además, la demostración más directa de
por qué la autorización se aplica en la capa de herramientas y no en el prompt.

---

## F-08 · `origin_interaction_id` está vacío en todas las disputas

**Verificado:** 27 sep 2026

| Campo | No nulos | % |
|---|---:|---:|
| `affected_product_id` | 16.257 | 66,4% |
| `assigned_agent_id` | 16.184 | 66,1% |
| `claimed_amount` | 8.125 | 33,2% |
| `related_branch_id` | 7.064 | 28,8% |
| `origin_interaction_id` | **0** | **0,0%** |

No hay puente de queja a interacción del call center. Junto con F-01, cierra
definitivamente la vía de reconstruir el caso desde las conversaciones.

---

## F-09 · Las etiquetas de resultado son ruido

**Verificado:** 27 sep 2026 · `pipeline/label_validity.py`

Gradient boosting sobre features disponibles al momento del contacto
(`claimed_amount`, `case_type`, `category`, `reception_channel`, `currency`,
`is_repeat_complainer`, segmento, país y `credit_score` del cliente), con split
temporal 70/30. Excluidas por leakage: `resolution_days`, `status` final,
`closing_date`, `compensation_granted`.

| Etiqueta | Prevalencia | AUC | Veredicto |
|---|---:|---:|---|
| `sla_breached` | 20,2% | **0,489** | Sin señal |
| `priority_high` | 19,7% | **0,513** | Sin señal |
| `was_escalated` | 5,1% | **0,512** | Sin señal |
| `rejected` | 1,0% | **0,457** | Sin señal |

Las cuatro son indistinguibles del azar: se asignaron aleatoriamente.

**Consecuencia.** No hay segundo componente de ML supervisado sobre estas
etiquetas. Se reporta como **resultado nulo documentado**, que es un hallazgo
legítimo y verificable, no una carencia. La decisión de escalar queda en reglas
deterministas con umbrales justificados por política, que es donde debe estar en
un sistema bancario.

---

## F-10 · Volúmenes confirmados

**Verificado:** 27 sep 2026

- **67.095 quejas**, sin duplicados en `complaint_id`. El enunciado dice 80.000:
  la diferencia es real y se reporta.
- **24.491 disputas** (36,50%): "Cargo no reconocido" 12.297 (18,33%) y "Cobro
  indebido" 12.194 (18,17%).
- **SLA incumplido: 20,11%** (13.495 de 67.095).
- Las cinco categorías están casi perfectamente equilibradas (19,7%–20,2% cada
  una), lo que refuerza que la generación fue aleatoria y no simula demanda real.

Estas cifras coinciden con las reportadas públicamente por otros equipos, lo que
da confianza en la lectura del dataset.

**Consecuencia.** Los volúmenes **sí** sirven para justificar la elección del
flujo y dimensionar el caso de negocio: las disputas son el mayor grupo de
quejas y una de cada cinco incumple SLA.

---

## F-11 · La mitad de las disputas entra por el canal más caro

**Verificado:** 28 sep 2026 · Sustenta el caso de negocio

| Canal | Disputas | % |
|---|---:|---:|
| Call Center | 12.363 | **50,48%** |
| Email | 4.775 | 19,50% |
| Web | 3.604 | 14,72% |
| App | 2.525 | 10,31% |
| Branch | 962 | 3,93% |
| Regulator | 262 | 1,07% |

Volumen mensual de disputas: **681 de media**, estable durante los tres años,
sin estacionalidad que permita diferir carga.

### Costo por caso (la unidad correcta)

Las llamadas de categoría **Queja** duran **431 s de mediana** (≈ 7,2 min),
frente a 205 s de las Transaccionales — es el motivo de contacto más caro
después de Comercial y Retención:

| Categoría | Interacciones | Mediana |
|---|---:|---:|
| Comercial | 54.879 | 540 s |
| Retención | 20.578 | 478 s |
| **Queja** | **117.021** | **431 s** |
| Técnico | 102.899 | 360 s |
| Producto | 150.863 | 263 s |
| Transaccional | 240.056 | 205 s |

A esto se suma una espera mediana de 119 s, que es **tiempo del cliente, no del
agente**, y no debe sumarse al costo operativo.

**Unidad de negocio:** una disputa cuesta **≈ 7 min de agente** en la llamada
más **15 días de mediana de back-office** hasta la resolución.

**Escalado anual (proyección, no medición).** 12.363 disputas llegaron por call
center en los **tres años** del dataset (2023-06-17 a 2026-06-18), es decir
**≈ 4.120 al año**. A 431 s cada una: **≈ 493 horas de agente al año**.
Es una proyección aritmética sobre datos sintéticos, no una medición de ahorro:
se etiqueta como tal, según exige el enunciado.

**Caveat:** el **14% de `duration_seconds` es nulo** (96.234 de 686.296), lo que
sesga cualquier promedio. Se usa la mediana y se reporta el faltante.

**Matiz honesto.** Las disputas **no** se resuelven peor que el resto de quejas:
mediana de 15 días frente a 16, y SLA incumplido de 20,16% frente a 20,08%. La
justificación del flujo es el **volumen, la mezcla de canal y el costo por
llamada**, no un peor desempeño relativo. Presentarlo de otra manera sería
sobrevender el hallazgo.

Gráfica: `docs/img/flow_justification.png` — reproducible con
`uv run python pipeline/plot_justification.py`.

---

## F-12 · Hallazgos de la construcción del silver

**Verificado:** 28 sep 2026 · `pipeline/silver.py`, manifiesto en `docs/build_manifest.json`

**No hay duplicados por PK.** El dataset anuncia ~2% de duplicados; en las
cuatro tablas del agente no hay **ninguno**: 150.000 / 400.000 / 4.425.008 /
67.095 filas, todas con clave única. La deduplicación se mantiene en el pipeline
igualmente, porque el contrato debe sostenerse si una carga futura los trae.

**`transactions` sí respeta la propiedad por cliente.** Cero `customer_id`
huérfanos, cero `product_id` inexistentes y **cero transacciones cuyo
`product_id` pertenezca a otro cliente**. Es el contraste exacto con F-07 y lo
que justifica usar `transactions` como universo de hechos verificables del
sistema.

**Ningún producto mexicano está en MXN.** Los 200.398 productos de clientes de
México están **todos en USD**, mientras Colombia usa COP (107.975) y Argentina
ARS (71.524), cada uno con una minoría en USD. `daily_exchange_rates` sí tiene
los 12 pares, MXN incluido.

> **Consecuencia.** Los umbrales de política son por monto. Comparar un umbral
> pensado en pesos mexicanos contra saldos en USD daría resultados absurdos.
> Toda comparación de política se hace en **USD normalizado**, nunca en moneda
> local, y el umbral por país se define en USD.

**1.040 quejas en cuarentena por CMP-07:** tienen `claimed_amount` sin
`currency`. Un monto sin moneda no es comparable contra un umbral, así que no
llega al agente.

**La conversión a USD pasó de 43% a 99,999%.** Tras recalcular con las tasas
diarias: `identity` 2.437.979 (ya en USD), `daily_rate` 1.986.422, `source` 572,
**`unavailable` 35**.

Esas 35 son transacciones del **2026-06-18**, un día después de la última tasa
disponible (2026-06-17). **No se les inventa una tasa**: quedan marcadas como
`unavailable` y el sistema debe escalarlas. Es un caso de frescura real y hay un
test que impide que alguien las "arregle" rellenando el valor.

**Nulos relevantes** (son características del dataset, no errores; se cuentan
como avisos y no mandan filas a cuarentena): `amount_usd` 57%, `merchant_name`
77%, `claimed_amount` 68%, `credit_limit` 69%, `detected_accent` 30%,
`credit_score` 15%.

---

## F-13 · Ninguna compra supera los 510 USD

**Verificado:** 28 sep 2026 · Obligó a recalibrar un umbral de política

Al escribir el test de escalamiento por monto apareció que la consulta no
encontraba **ninguna** transacción sobre 1.000 USD con comercio, pese a que el
máximo del dataset es 10.199 USD.

| Tipo (aprobadas) | Máximo USD | Sobre 1.000 USD |
|---|---:|---:|
| Transfer | 10.200 | 749.518 |
| Deposit | 5.100 | 452.953 |
| Payment | 2.040 | 349.063 |
| Adjustment | 1.019 | 294 |
| **Purchase** | **510** | **0** |
| **Withdrawal** | **510** | **0** |

Los montos altos están solo en transferencias y depósitos, que no tienen
comercio asociado y no son disputas de tarjeta. Las compras —el universo real
de este flujo— están acotadas a 510 USD.

Percentiles de las compras aprobadas: p50 252 · p90 450 · p95 475 · p99 495.

**Consecuencia.** El umbral inicial de ESC-01 (1.000 USD, elegido a ojo)
**nunca se habría disparado**. Se recalibró a **400 USD**, que escala el 20,2%
de las compras: una proporción defendible para revisión humana.

Queda declarado en el YAML que en producción ese umbral saldría del apetito de
riesgo del banco y de un análisis de costo esperado, no de los percentiles del
histórico.

**Cómo apareció.** No lo buscamos: el test `test_high_amount_escalates` se
saltó por falta de datos, y eso llevó a revisar por qué. Es un argumento a
favor de escribir los tests contra datos reales en vez de contra fixtures
cómodos.

---

## Qué sobrevive y qué no

**Sirve:** volúmenes y proporciones (F-10), catálogos de categorías, dimensiones
(`customers`, `products`, `branches`, `daily_exchange_rates`), `transactions`
como universo de hechos verificables, y la estructura de particionado para
demostrar carga incremental.

**No sirve:** texto libre de cualquier tabla (F-01, F-02, F-05), enlaces
queja↔transacción (F-06) y queja↔interacción (F-08), y las etiquetas de
resultado (F-09).

**Se construye:** corpus conversacional es/pt escrito por el equipo, fixture de
disputas vinculadas a transacciones reales, y el set adversarial de evaluación.
Todo etiquetado `team-generated`.
