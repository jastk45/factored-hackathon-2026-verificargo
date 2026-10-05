import { useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api, type Combined, type EvalData, type RunSummary, type Scorecard } from "@/lib/api"
import { cn } from "@/lib/utils"

// [clave del scorecard, etiqueta, ¿una cifra mayor es mejor?]
const ROWS: [string, string, "lower" | "higher" | null][] = [
  ["unsafe_outcomes", "Resultados inseguros (incluye afirmaciones falsas)", "lower"],
  ["false_statements", "Afirmaciones falsas al cliente", "lower"],
  ["missed_escalations", "Escalamientos omitidos (sin ticket verificado)", "lower"],
  ["escalation_recall", "Escalamientos con ticket verificado", "higher"],
  ["safe_automated_resolution_in_scope", "Resolución segura · todos los casos en alcance", "higher"],
  ["safe_automated_resolution", "Resolución segura · casos resolubles", "higher"],
  ["automation_attempted", "Automatización intentada (resolubles)", null],
  ["wrong_resolutions", "Resoluciones incompletas", "lower"],
  ["unnecessary_escalations", "Escalamientos innecesarios", "lower"],
  ["outcome_acceptable", "Resultado aceptable (y no inseguro)", "higher"],
  ["faults_activated", "Fallas inyectadas que se activaron", null],
  ["latency_turn_p50_ms", "Latencia por turno p50 (ms)", null],
  ["latency_turn_p95_ms", "Latencia por turno p95 (ms)", null],
  ["avg_turns", "Turnos promedio", null],
  ["projected_cost_per_case_usd", "Costo proyectado por caso (USD)*", null],
]

function ScoreTable({ baseline, proposed }: { baseline?: Scorecard; proposed?: Scorecard }) {
  const systems = [
    { key: "baseline", label: "Baseline: el LLM decide", card: baseline },
    { key: "proposed", label: "VerifiCargo v6", card: proposed },
  ].filter((s) => s.card)
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Métrica</TableHead>
          {systems.map((s) => (
            <TableHead key={s.key}>{s.label}</TableHead>
          ))}
        </TableRow>
      </TableHeader>
      <TableBody>
        {ROWS.map(([key, label]) => (
          <TableRow key={key}>
            <TableCell className="font-medium">{label}</TableCell>
            {systems.map((s) => (
              <TableCell
                key={s.key}
                className={cn(
                  "font-mono text-xs",
                  s.key === "proposed" && key === "unsafe_outcomes" && "font-semibold",
                )}
              >
                {String(s.card?.[key] ?? "—")}
              </TableCell>
            ))}
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

const V3_ROWS: [string, string][] = [
  ["unsafe_outcomes", "Resultados inseguros detectados"],
  ["false_statements", "Afirmaciones falsas al cliente"],
  ["missed_escalations", "Escalamientos omitidos (sin ticket verificado)"],
  ["safe_automated_resolution_in_scope", "Resolución segura · todos los casos en alcance"],
  ["safe_automated_resolution", "Resolución segura · casos resolubles"],
  ["wrong_resolutions", "Resoluciones incompletas"],
  ["unnecessary_escalations", "Escalamientos innecesarios"],
  ["outcome_acceptable", "Resultado aceptable (y no inseguro)"],
  ["faults_activated", "Fallas inyectadas que se activaron"],
]

const pct1 = (x: number) => `${(Math.round(x * 1000) / 10).toLocaleString("es-AR")}%`

function combined(m?: Combined) {
  if (!m || m.rate == null || !m.range) return "—"
  const [lo, hi] = m.range
  const spread = lo === hi ? `${lo} de ${m.per_run_den} en cada corrida` : `${lo}–${hi} de ${m.per_run_den} por corrida`
  return `${pct1(m.rate)} (${spread})`
}

const seconds = (ms?: number) => (ms == null ? "—" : `${(ms / 1000).toLocaleString("es-AR", { maximumFractionDigits: 1 })} s`)

function V3Table({ baseline, proposed }: { baseline?: RunSummary; proposed?: RunSummary }) {
  if (!proposed) return <p className="text-sm text-muted-foreground">Sin reporte.</p>
  const cuts: [string, "by_language" | "by_country" | "by_segment"][] = [
    ["Idioma", "by_language"], ["País", "by_country"], ["Segmento", "by_segment"],
  ]
  return (
    <div className="space-y-4">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Métrica</TableHead>
            <TableHead>Baseline: el LLM decide</TableHead>
            <TableHead>VerifiCargo v6</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {V3_ROWS.map(([key, label]) => (
            <TableRow key={key}>
              <TableCell className="font-medium">{label}</TableCell>
              <TableCell className="font-mono text-xs">{combined(baseline?.metrics[key])}</TableCell>
              <TableCell className={cn("font-mono text-xs", key === "unsafe_outcomes" && "font-semibold")}>
                {combined(proposed.metrics[key])}
              </TableCell>
            </TableRow>
          ))}
          {[["latency_turn", "Latencia por turno p50 / p95"], ["latency_case", "Latencia por conversación p50 / p95"]].map(
            ([key, label]) => (
              <TableRow key={key}>
                <TableCell className="font-medium">{label}</TableCell>
                {[baseline, proposed].map((sys, i) => (
                  <TableCell key={i} className="font-mono text-xs">
                    {seconds(sys?.latency[`${key}_p50_ms`])} / {seconds(sys?.latency[`${key}_p95_ms`])}
                  </TableCell>
                ))}
              </TableRow>
            ),
          )}
        </TableBody>
      </Table>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Corte (3 corridas juntas)</TableHead>
            <TableHead>Casos</TableHead>
            <TableHead>Inseguros · baseline</TableHead>
            <TableHead>Inseguros · VerifiCargo</TableHead>
            <TableHead>Resolución segura · VerifiCargo</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {cuts.flatMap(([label, key]) =>
            Object.entries(proposed[key] ?? {}).map(([group, m]) => {
              const b = baseline?.[key]?.[group]
              const rate = (x?: Combined | number) =>
                x && typeof x !== "number" && x.rate != null ? `${pct1(x.rate)} (${x.num}/${x.den})` : "—"
              return (
                <TableRow key={`${key}-${group}`}>
                  <TableCell className="font-medium">{label}: {group}</TableCell>
                  <TableCell>{String(m.n ?? "—")}</TableCell>
                  <TableCell className="font-mono text-xs">{rate(b?.unsafe_outcomes)}</TableCell>
                  <TableCell className="font-mono text-xs">{rate(m.unsafe_outcomes)}</TableCell>
                  <TableCell className="font-mono text-xs">{rate(m.safe_automated_resolution)}</TableCell>
                </TableRow>
              )
            }),
          )}
        </TableBody>
      </Table>
    </div>
  )
}

export function EvalView() {
  const [data, setData] = useState<EvalData | null>(null)
  useEffect(() => {
    api.evaluation().then(setData)
  }, [])
  if (!data) return <p className="text-sm text-muted-foreground">Cargando…</p>

  const clf = data.classifier
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>eval-v3 · la versión final sobre casos nuevos, 3 corridas por sistema</CardTitle>
          <CardDescription>
            152 conversaciones creadas después de congelar sistema, prompts y evaluador (tags system-v6-frozen y eval-v3).
            Cada celda: la tasa de las 3 corridas juntas y, entre paréntesis, el rango por corrida. Detectado con reglas
            deterministas: evidencia sobre estos casos, no una garantía de seguridad.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <V3Table {...(data.v3 ?? {})} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">eval-v2 · casos nuevos para v5; ya vistos durante el desarrollo de v6</CardTitle>
          <CardDescription>
            Transacciones, redacción y conductas nuevas (negativas, cambio de tema, fallas que se comprueba que se
            activan), con el sistema ya congelado. Mismo modelo (qwen3:1.7b) y mismo usuario simulado para los dos.
            Medición offline: no es una mejora medida en producción.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {data.v2 ? <ScoreTable {...data.v2} /> : <p className="text-sm text-muted-foreground">Sin reporte.</p>}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">eval-v1 · el set usado durante el desarrollo</CardTitle>
          <CardDescription>
            El sistema se ajustó mirando estos casos: no es una estimación held-out. Las cifras publicadas antes del 4
            de octubre salían de un evaluador que una auditoría encontró defectuoso y no se presentan como validadas.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {data.v1 ? <ScoreTable {...data.v1} /> : <p className="text-sm text-muted-foreground">Sin reporte.</p>}
          <p className="mt-2 text-xs text-muted-foreground">
            * Proyección con precio de lista de gpt-4o-mini y tokens estimados; el sistema corre local sin costo por token.
          </p>
        </CardContent>
      </Card>

      {clf && (
        <div className="grid gap-4 lg:grid-cols-2">
          <Card>
            <CardHeader>
              <CardTitle className="text-base">Clasificador de intención (E-05, pre-registrado)</CardTitle>
              <CardDescription>
                128 mensajes es/pt escritos a mano. Criterio: el candidato debía superar al baseline por ≥3 puntos.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Brazo</TableHead>
                    <TableHead>macro-F1</TableHead>
                    <TableHead>es</TableHead>
                    <TableHead>pt</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(clf.arms).map(([k, v]) => (
                    <TableRow key={k} className={k === clf.acceptance.deployed ? "bg-emerald-50" : ""}>
                      <TableCell className="font-mono text-xs">{k}</TableCell>
                      <TableCell>{v.macro_f1.toFixed(3)}</TableCell>
                      <TableCell>{v.macro_f1_es.toFixed(3)}</TableCell>
                      <TableCell>{v.macro_f1_pt.toFixed(3)}</TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
              <p className="mt-2 text-sm">
                Ganancia sobre el baseline: <strong>+{(clf.acceptance.gain_macro_f1 * 100).toFixed(1)} puntos</strong> →{" "}
                {clf.acceptance.accepted ? "aceptado" : "rechazado"}.
              </p>
            </CardContent>
          </Card>
          {clf.alpha_sweep && (
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Abstención conformal: el costo de la garantía</CardTitle>
                <CardDescription>64 mensajes no usados para calibrar. α es la tasa de ruteo equivocado aceptada.</CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>α</TableHead>
                      <TableHead>Cobertura</TableHead>
                      <TableHead>Decide</TableHead>
                      <TableHead>Pregunta</TableHead>
                      <TableHead>Errores</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {clf.alpha_sweep.map((s) => (
                      <TableRow key={s.alpha} className={s.alpha === 0.2 ? "bg-emerald-50" : ""}>
                        <TableCell>{s.alpha}</TableCell>
                        <TableCell>{(s.coverage * 100).toFixed(1)}%</TableCell>
                        <TableCell>{s.decides}</TableCell>
                        <TableCell>{s.clarifies}</TableCell>
                        <TableCell>{s.routing_errors}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  )
}
