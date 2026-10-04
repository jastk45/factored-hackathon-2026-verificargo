import { useEffect, useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { api } from "@/lib/api"
import { cn } from "@/lib/utils"

const ROWS: [string, string, "lower" | "higher" | null][] = [
  ["unsafe_outcomes", "Resultados inseguros", "lower"],
  ["missed_escalations", "Escalamientos omitidos", "lower"],
  ["escalation_recall", "Escalamientos correctos", "higher"],
  ["safe_automated_resolution", "Resolución automática segura", "higher"],
  ["automation_attempted", "Automatización intentada", null],
  ["unnecessary_escalations", "Escalamientos innecesarios", "lower"],
  ["containment", "Contención (sin humano)", null],
  ["outcome_acceptable", "Resultado aceptable", "higher"],
  ["latency_turn_p50_ms", "Latencia por turno p50 (ms)", null],
  ["latency_turn_p95_ms", "Latencia por turno p95 (ms)", null],
  ["avg_turns", "Turnos promedio", null],
  ["projected_cost_per_case_usd", "Costo proyectado por caso (USD)*", null],
]

export function EvalView() {
  const [data, setData] = useState<Record<string, any> | null>(null)
  useEffect(() => {
    api.evaluation().then(setData)
  }, [])
  if (!data) return <p className="text-sm text-muted-foreground">Cargando…</p>

  const systems = [
    { key: "baseline", label: "Baseline: el LLM decide" },
    { key: "proposed_v1", label: "VerifiCargo v1 (α=0,10, pre-registrado)" },
    { key: "proposed", label: "VerifiCargo v2 (α=0,20)" },
  ].filter((s) => data[s.key])

  const clf = data.classifier
  return (
    <div className="space-y-4">
      <Card>
        <CardHeader>
          <CardTitle>Sistema completo · 159 conversaciones congeladas (tag eval-v1)</CardTitle>
          <CardDescription>
            Mismos casos, mismo modelo (qwen3:1.7b), mismo usuario simulado. Medición offline: no es una mejora medida en
            producción. v2 se ajustó después de ver v1 y no es una estimación held-out limpia.
          </CardDescription>
        </CardHeader>
        <CardContent className="overflow-x-auto">
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
              {ROWS.map(([key, label, better]) => (
                <TableRow key={key}>
                  <TableCell className="font-medium">{label}</TableCell>
                  {systems.map((s) => (
                    <TableCell
                      key={s.key}
                      className={cn(
                        "font-mono text-xs",
                        better && key === "unsafe_outcomes" && s.key === "baseline" && "text-rose-700 font-semibold",
                        better && key === "missed_escalations" && s.key === "baseline" && "text-rose-700 font-semibold",
                      )}
                    >
                      {String(data[s.key][key] ?? "—")}
                    </TableCell>
                  ))}
                </TableRow>
              ))}
            </TableBody>
          </Table>
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
                  {Object.entries(clf.arms).map(([k, v]: [string, any]) => (
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
                    {clf.alpha_sweep.map((s: any) => (
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
