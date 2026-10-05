# Limitaciones y trabajo pendiente

Lo que este prototipo **no** demuestra, lo que asume, y lo que faltaría para
operar con clientes reales. El kickoff lo pidió explícitamente: *"leave notes in
your repo saying what things you would improve… what were the limits you
encountered"*.

---

## 1. Datos

**El dataset no contiene texto de cliente utilizable.** Los 171.321
transcripts no tienen ni una conversación de disputa (F-01) y repiten 546
textos (F-02); `complaints.description` tiene 5 textos únicos en 67.095 filas
(F-05). Todo el componente conversacional se entrenó y evaluó con material
externo (BANKING77 traducido) y escrito por el equipo. **Ningún número de este
repo mide el sistema contra lenguaje real de clientes de este banco.**

**Las relaciones entre tablas no se sostienen.** Ninguna disputa se enlaza con
una transacción del cliente (F-06), el 100% de `affected_product_id` apunta a
otro cliente (F-07) y `origin_interaction_id` está vacío (F-08). El paso
`LOCATE_TXN` se valida con un fixture construido por el equipo sobre
transacciones reales (D-13), no con disputas históricas.

**Las etiquetas de resultado son ruido** (F-09: AUC 0,46-0,51). No hay un
"ground truth" histórico de qué debió escalarse; las etiquetas del eval set
salen de la política escrita.

**Volúmenes**: el enunciado anuncia 80.000 quejas; hay 67.095. El ~2% de
duplicados anunciado no aparece en las cuatro tablas que usa el sistema (F-12).

## 2. Idioma

**El portugués es la limitación principal.** El dataset es 100% español. El
portugués de entrenamiento son 240 frases traducidas por un modelo de 2B
parámetros, con errores visibles ("Minha cartão"). El de evaluación lo escribió
el equipo. Ninguno sustituye a clientes brasileños reales.

**Variantes regionales**: el test de español incluye jerga de MX/CO/AR, pero en
pocas frases. El clasificador rinde peor en español (0,622) que en portugués
(0,742), posiblemente por esa jerga; no está verificado.

**Detección de idioma** por listas léxicas: falla con mensajes muy cortos o en
portuñol balanceado, y en ese caso toma el idioma de la sesión.

## 3. Modelos

**El clasificador es modesto** (macro-F1 0,681 sobre 128 casos). La abstención
conformal lo compensa preguntando: en 53 de 64 casos el conjunto mezcla flujos y
el sistema pide aclarar el tema antes de actuar. Es seguro, pero alarga la
conversación.

**La garantía conformal es marginal y asume intercambiabilidad** entre
calibración y uso real. Se calibró con 64 mensajes escritos por el equipo; si
los clientes reales escriben distinto, la cobertura del 90% no está garantizada.

**El LLM (qwen3:1.7b) es débil**: E-02 a E-02b documentan sus fallos. Por diseño
solo extrae campos, y si falla el sistema cae a reglas (regex).

**No se pudo comparar contra un modelo de API.** La clave de OpenAI disponible
devuelve HTTP 401 (verificado también en el proyecto donde se usó por última vez
el 21 de septiembre). `eval/compare_providers.py` queda listo.

**SetFit sin fine-tuning contrastivo**: se usó el encoder congelado más una
cabeza lineal (la segunda etapa de SetFit sin la primera), por tiempo de CPU.

## 4. Política

**Solo México tiene parámetros reales** (CONDUSEF: 90 días para reclamar, 45
para dictaminar). Colombia y Argentina usan parámetros **sintéticos**,
marcados como tales en el YAML, en el SLA del handoff y en la respuesta al
cliente.

**El umbral de escalamiento por monto (400 USD) se calibró sobre el histórico**
(F-13: ninguna compra supera 510 USD). En producción saldría del apetito de
riesgo del banco y de un análisis de costo esperado.

**Las preguntas de "por qué me rechazaron una compra"** reciben la respuesta de
procedimiento, no una explicación de la transacción concreta.

## 5. Seguridad

**La identidad es un mock.** Token HMAC de vida corta y un OTP fijo de prueba
(`123456`). Demuestra el contrato (sesión firmada, niveles, expiración), no
autentica a nadie. Lo mismo vale para la consola humana: el token de agente se
obtiene con un código de acceso de prueba documentado (D-17); en producción
saldría del SSO del banco.

**El detector de inyección es evadible** (`test_detection_can_be_evaded` lo
demuestra). La garantía viene de la arquitectura: el modelo no decide acciones
y ningún argumento sensible puede venir del texto del cliente (DATA-01).

**No se probó inyección indirecta** a través de datos de herramientas (por
ejemplo, un nombre de comercio malicioso en la base). La defensa de
procedencia la cubriría, pero no hay un caso de evaluación que lo ejercite.

**Concurrencia**: las disputas viven en un almacén en memoria compartido por
el proceso. GATE-05 detecta duplicados entre conversaciones, y el caso de
carrera (dos conversaciones abren la misma disputa) se maneja y se prueba, pero
no hay transacciones ni bloqueos reales: con varios procesos haría falta el
core bancario con idempotencia. Dentro de un proceso, la API usa un cursor de
DuckDB por request y locks en el almacén de disputas, la cola y cada
conversación (`tests/test_concurrency.py`). Antes compartía una sola conexión:
con 32 requests simultáneos fallaban 183 de 200 y algunos recibían el resultado
de la consulta de otro hilo.

**Lo que encontró una revisión externa (4 de octubre).** Path traversal en la
ruta del frontend, una confirmación que aceptaba "Sí, pero no abras la disputa
todavía", escalamientos sin ticket, la cola humana sin control de acceso,
aprobar sin ejecutar, el país fijo en "MX" y un KeyError con disputas repetidas.
Los siete se reprodujeron, se corrigieron y tienen un test de regresión
(`tests/test_review_fixes.py`). Una tercera revisión encontró que un "sí" con
datos nuevos ("Sí, corrige el monto a 500 USD") todavía confirmaba (D-19) y
que "pedir información" no le llegaba al cliente (D-21). Que tres revisiones
encontraran problemas indica que puede haber otros del mismo tipo.

## 6. Evaluación

**El evaluador v1 tenía defectos** (D-18): no comprobaba que las fallas
inyectadas se activaran, ni que un escalamiento tuviera ticket, ni que una
respuesta de política o estado fuera correcta. Las cifras v1-v4 que se
publicaron antes del 4 de octubre (incluido el 82,4% de resolución segura) no
se presentan como validadas.

**eval-v1 no es held-out**: el sistema se ajustó mirándolo. El resultado
principal es **eval-v2** (152 casos), construido y congelado (tag `eval-v2`)
con el sistema ya cerrado. Pero lo escribió el mismo equipo que construyó el
sistema: es independiente de los ajustes, no de quien lo diseñó.

**Muestras pequeñas.** 152 + 159 casos de sistema y 128 del clasificador. Cero
fallas observadas en un bloque de 6-12 casos **no** prueba riesgo cero.

**El usuario simulado sigue un guion.** En eval-v2 dice que no, confirma con
reservas o cambia a una tarjeta robada en plena confirmación, pero lo hace con
frases fijas. Un cliente real se equivoca de cifra, abandona o mezcla temas de
formas que el guion no cubre.

**La veracidad de una respuesta se juzga con reglas**, no con un juez humano
(D-20): plazos asociados al concepto y país correctos, estados reales de los
reclamos, montos que existen, acciones y traspasos que ocurrieron, ninguna
promesa de reembolso. Detecta falsedades de esos tipos, no cualquier
paráfrasis engañosa ni una respuesta confusa o en mal tono. Cuatro revisiones
externas encontraron huecos en el evaluador, en las dos direcciones: aprobaba
falsedades y, después, castigaba negaciones y condiciones válidas. Un corpus
de tests acota el problema; puede haber otros.

**Una sola corrida por sistema**, sin intervalos de confianza ni pass^k. La
variabilidad entre corridas del LLM (temperatura 0, pero no determinista entre
versiones) no está medida.

**El baseline usa el mismo modelo pequeño.** Un "LLM que decide" con un modelo
grande probablemente rendiría mejor; la comparación mide la arquitectura con el
modelo disponible, no el techo de cada enfoque. Desde el evaluador v2 recibe la
política completa del YAML, la fecha de referencia, los reclamos del cliente y
decide él mismo si el cliente confirmó. Siguen diferencias que no son de
arquitectura: redacta la respuesta en texto libre (VerifiCargo usa plantillas)
y hace una sola llamada por turno, sin un ciclo de herramientas.

**Costos**: el sistema corre en local, sin costo por token. Los costos por caso
del scorecard son **proyecciones** con el precio de lista de gpt-4o-mini y
tokens estimados, no gasto medido.

## 7. Qué falta para producción

| Área | Prototipo | Producción |
|---|---|---|
| Identidad | Token HMAC + OTP fijo | IdP del banco (OIDC), MFA real, secreto en gestor y rotación |
| Acciones | Almacén en memoria compartido por el proceso | Core bancario detrás de un adaptador con idempotencia y reintentos |
| Cola humana | JSONL local con re-lectura y dead-letter | CRM (Salesforce/Zendesk) detrás de la interfaz `enqueue/get/update` |
| Agentes | Token de agente con código de prueba | SSO del banco con roles, y auditoría por agente |
| Política | YAML + funciones puras | Mismo modelo, con OPA o Cerbos y revisión de Compliance por versión |
| Datos | DuckDB sobre Parquet local | Mismo pipeline en un lakehouse con orquestador (Airflow/Dagster) |
| Observabilidad | Traza por turno + log de auditoría JSONL | OpenTelemetry → Langfuse/Datadog, alertas sobre tasa de escalamiento y unsafe |
| Retención | Sin política | Logs con PII minimizada; retención según regulación local |
| Modelo | qwen3:1.7b local | Comparar contra un modelo de API y re-calibrar la conformal con tráfico real |
| Capacidad | Un proceso, ~5 s por turno | Clasificador en CPU (2 ms) escala horizontal; el LLM es el cuello de botella |
| Portugués | Traducido y escrito por el equipo | Datos reales de clientes de Brasil y re-entrenamiento |
