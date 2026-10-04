import { useState } from "react"
import { CheckCircle2, ChevronDown, CircleAlert, CirclePause, ShieldAlert } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import type { Outcome, TurnResult } from "@/lib/api"
import { cn } from "@/lib/utils"

const OUTCOME_STYLE: Record<Outcome, string> = {
  RESOLVED: "bg-emerald-100 text-emerald-800 border-emerald-200",
  CLARIFY: "bg-sky-100 text-sky-800 border-sky-200",
  ESCALATED: "bg-amber-100 text-amber-900 border-amber-200",
  DENIED: "bg-rose-100 text-rose-800 border-rose-200",
  ABSTAINED: "bg-zinc-100 text-zinc-700 border-zinc-200",
  BLOCKED: "bg-rose-100 text-rose-800 border-rose-200",
}

const OUTCOME_LABEL: Record<Outcome, string> = {
  RESOLVED: "Resuelto",
  CLARIFY: "Pregunta",
  ESCALATED: "Escalado",
  DENIED: "Denegado",
  ABSTAINED: "Fuera de alcance",
  BLOCKED: "Bloqueado",
}

export function OutcomeBadge({ outcome }: { outcome: Outcome }) {
  return (
    <Badge variant="outline" className={cn("font-medium", OUTCOME_STYLE[outcome])}>
      {OUTCOME_LABEL[outcome]}
    </Badge>
  )
}

const ACTION_LABEL: Record<string, string> = {
  create_dispute_case: "Disputa creada",
  create_handoff_ticket: "Caso enviado a un especialista",
  block_card: "Tarjeta bloqueada",
}

export function ActionCards({ turn }: { turn: TurnResult }) {
  return (
    <div className="mt-2 space-y-1.5">
      {turn.actions_taken.map((a, i) => (
        <div
          key={i}
          className={cn(
            "flex items-start gap-2 rounded-md border px-2.5 py-1.5 text-xs",
            a.verified ? "border-emerald-200 bg-emerald-50 text-emerald-900" : "border-rose-200 bg-rose-50 text-rose-900",
          )}
        >
          {a.verified ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0" /> : <ShieldAlert className="mt-0.5 size-3.5 shrink-0" />}
          <span>
            <strong>{ACTION_LABEL[a.action] ?? a.action}</strong> {a.case_id ?? a.ticket_id ?? ""}
            {a.verified ? " · verificada al releer" : " · NO se pudo verificar"}
            {a.evidence_ids?.length ? <span className="font-mono opacity-70"> · {a.evidence_ids.join(", ")}</span> : null}
          </span>
        </div>
      ))}
      {turn.actions_not_taken.map((a, i) => (
        <div key={i} className="flex items-start gap-2 rounded-md border border-sky-200 bg-sky-50 px-2.5 py-1.5 text-xs text-sky-900">
          <CirclePause className="mt-0.5 size-3.5 shrink-0" />
          <span>
            No se ejecutó <code className="font-mono">{a.action}</code>: {a.reason}
          </span>
        </div>
      ))}
      {turn.grounding_violations.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-2.5 py-1.5 text-xs text-amber-900">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
          Respuesta reemplazada: cifras sin respaldo en la evidencia ({turn.grounding_violations.join(", ")})
        </div>
      )}
    </div>
  )
}

export function TracePanel({ turn }: { turn: TurnResult }) {
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-2">
      <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
        <ChevronDown className={cn("size-3.5 transition-transform", open && "rotate-180")} />
        Traza del turno · {turn.latency_ms} ms
      </CollapsibleTrigger>
      <CollapsibleContent>
        <div className="mt-2 space-y-3 rounded-md border bg-muted/40 p-3 text-xs">
          <div>
            <div className="mb-1 font-medium text-foreground">Estados</div>
            <div className="flex flex-wrap items-center gap-1">
              {turn.states.map((s, i) => (
                <span key={i} className="flex items-center gap-1">
                  <code className="rounded bg-background px-1.5 py-0.5 font-mono">{s}</code>
                  {i < turn.states.length - 1 && <span className="text-muted-foreground">→</span>}
                </span>
              ))}
            </div>
          </div>
          {turn.intent && (
            <div>
              <div className="mb-1 font-medium text-foreground">Intención (clasificador + conformal)</div>
              <p>
                <code className="font-mono">{turn.intent.intent}</code> · confianza {turn.intent.confidence.toFixed(2)} · ruta{" "}
                <strong>{turn.intent.route}</strong> — {turn.intent.reason}
              </p>
              <p className="text-muted-foreground">Conjunto: {turn.intent.prediction_set.join(", ") || "vacío"}</p>
            </div>
          )}
          {Object.keys(turn.extracted).length > 0 && (
            <div>
              <div className="mb-1 font-medium text-foreground">
                Campos extraídos {turn.extraction_source && <span className="font-normal text-muted-foreground">por {turn.extraction_source}</span>}
              </div>
              <pre className="overflow-x-auto rounded bg-background p-2 font-mono">{JSON.stringify(turn.extracted, null, 2)}</pre>
            </div>
          )}
          {turn.policy_trace.length > 0 && (
            <div>
              <div className="mb-1 font-medium text-foreground">Reglas de política evaluadas</div>
              <div className="flex flex-wrap gap-1">
                {turn.policy_trace.map((r) => (
                  <code
                    key={r}
                    className={cn(
                      "rounded px-1.5 py-0.5 font-mono",
                      r.endsWith(":fire") ? "bg-amber-100 text-amber-900" : "bg-background",
                    )}
                  >
                    {r}
                  </code>
                ))}
              </div>
            </div>
          )}
          {turn.escalation_reasons.length > 0 && (
            <div>
              <div className="mb-1 font-medium text-foreground">Motivos de escalamiento</div>
              <ul className="list-disc space-y-0.5 pl-4">
                {turn.escalation_reasons.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          )}
          {turn.evidence.length > 0 && (
            <div>
              <div className="mb-1 font-medium text-foreground">Evidencia verificada</div>
              <ul className="space-y-0.5">
                {turn.evidence.map((e) => (
                  <li key={e.id}>
                    <code className="font-mono">{e.id}</code> <span className="text-muted-foreground">({e.source})</span> {e.fact}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}
