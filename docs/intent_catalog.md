# Catálogo de intenciones

Ocho clases, fijadas el 28 sep 2026. **El alcance está congelado**: no se añaden
intenciones aunque sobre tiempo.

El catálogo lo define el equipo, no el dataset. F-03 mostró que
`detected_intents` vale `consulta_general` en el 95% de los casos y F-05 que
`complaints.description` tiene 5 textos únicos: no hay etiquetas utilizables de
las que partir.

---

## Las ocho clases

| # | Intención | Qué expresa el cliente | Ruta esperada |
|---|---|---|---|
| 1 | `unrecognized_charge` | No reconoce un cargo | Disputa |
| 2 | `duplicate_charge` | Le cobraron dos veces lo mismo | Disputa |
| 3 | `wrong_amount` | El monto no coincide con lo pactado | Disputa |
| 4 | `merchandise_not_received` | Pagó y no recibió | Disputa |
| 5 | `card_lost_stolen` | Perdió la tarjeta o se la robaron | **Siempre ESCALATE** (ESC-03) |
| 6 | `dispute_status` | Pregunta por un caso ya abierto | Consulta |
| 7 | `policy_question` | Pregunta por plazos o procedimiento | Respuesta con cita |
| 8 | `out_of_scope` | Cualquier otra cosa | Abstención o derivación |

`out_of_scope` no es un cajón de sastre: es una clase de primera con sus propios
ejemplos de entrenamiento. Un sistema que no sabe decir "esto no me toca"
termina inventando.

---

## Mapeo desde BANKING77

De las 77 intenciones de BANKING77, **27 pertenecen al dominio**. Este es el
mapeo al catálogo (D-08b):

| Intención del catálogo | Intenciones de BANKING77 |
|---|---|
| `unrecognized_charge` | `card_payment_not_recognised`, `cash_withdrawal_not_recognised`, `direct_debit_payment_not_recognised`, `extra_charge_on_statement` |
| `duplicate_charge` | `transaction_charged_twice` |
| `wrong_amount` | `card_payment_wrong_exchange_rate`, `wrong_exchange_rate_for_cash_withdrawal`, `wrong_amount_of_cash_received`, `cash_withdrawal_charge`, `card_payment_fee_charged`, `transfer_fee_charged`, `exchange_charge`, `top_up_by_card_charge`, `top_up_by_bank_transfer_charge` |
| `merchandise_not_received` | `Refund_not_showing_up`, `request_refund` |
| `card_lost_stolen` | `lost_or_stolen_card`, `compromised_card`, `lost_or_stolen_phone` |
| `dispute_status` | `pending_card_payment`, `pending_cash_withdrawal`, `pending_transfer`, `pending_top_up` |
| `policy_question` | `declined_card_payment`, `declined_cash_withdrawal`, `declined_transfer`, `cancel_transfer` |
| `out_of_scope` | las 50 restantes (alta de cuenta, PIN, tarjetas virtuales, cripto…) |

**Dos decisiones de mapeo que conviene discutir:**

- Las intenciones `pending_*` van a `dispute_status` porque el cliente pregunta
  por el estado de algo en curso, que es la misma necesidad conversacional
  aunque el objeto sea distinto.
- Las `declined_*` van a `policy_question`: el cliente quiere entender por qué
  falló algo, y la respuesta sale de `response_code`, no de abrir una disputa.

Ambas son opinables. Están escritas para que un juez pueda estar en desacuerdo
con criterio.

---

## Las tres capas del corpus

| Capa | Origen | Para qué |
|---|---|---|
| BANKING77 traducido a es/pt | `external-public` | Volumen y lenguaje real de clientes bancarios |
| Ejemplos del equipo (es MX/CO/AR, pt) | `team-generated` | Regionalismos, portuñol, casos adversariales |
| MASSIVE es/pt | `external-public` | `out_of_scope` que el equipo no escribió (D-11) |

Cada fila del corpus lleva su `origin`. El reto pide identificar qué es real,
sintético o generado por el equipo.

---

## Lo que queda fuera del alcance

Transferencias, alta y baja de productos, cambio de PIN, préstamos,
inversiones, reclamos de sucursal y problemas de la app. Un mensaje sobre
cualquiera de esos temas es `out_of_scope` y el sistema lo dice en vez de
intentarlo.
