import { useCallback, useEffect, useState } from "react"
import { CheckCircle2, CircleHelp, Clock, FileJson, Inbox, LockKeyhole, RefreshCw, XCircle } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { api, type QueueItem } from "@/lib/api"
import { cn } from "@/lib/utils"

const PRIORITY: Record<string, string> = {
  critical: "bg-rose-600 text-white border-rose-600",
  high: "bg-amber-500 text-white border-amber-500",
  normal: "bg-emerald-600 text-white border-emerald-600",
  low: "bg-zinc-400 text-white border-zinc-400",
}

const DECISION_LABEL: Record<string, string> = {
  approved: "Aprobado",
  rejected: "Rechazado",
  info_requested: "Información pedida",
}

function resultText(item: QueueItem) {
  const r = item.result
  if (!r) return null
  if (r.case_id) return `disputa ${r.case_id} creada · verificada al releer`
  if (r.existing_case_id) return `ya existía la disputa ${r.existing_case_id}; no se abrió otra`
  return null
}

function CaseCard({ item, token, onResolved }: { item: QueueItem; token: string; onResolved: () => void }) {
  const p = item.package
  const [note, setNote] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const resolve = async (decision: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.resolve(token, p.handoff_id, decision, note)
      onResolved()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Card>
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center gap-2">
          <Badge className={cn("uppercase", PRIORITY[p.priority])}>{p.priority}</Badge>
          {item.status === "info_requested" && (
            <Badge variant="outline" className="border-sky-300 bg-sky-50 text-sky-800">esperando al cliente</Badge>
          )}
          <span className="font-mono text-sm font-medium">{p.handoff_id}</span>
          <span className="text-sm text-muted-foreground">
            {p.country} · {p.language.toUpperCase()} · {p.customer_ref}
          </span>
          <span className="ml-auto flex items-center gap-1 text-sm">
            <Clock className="size-3.5" />
            {p.sla.days_remaining != null ? `vence en ${p.sla.days_remaining} días` : "sin plazo calculado"}
            <Badge variant="outline" className="ml-1 text-[10px]">
              plazo {p.sla.provenance === "real" ? "regulatorio real" : "sintético"}
            </Badge>
          </span>
        </div>
        <CardDescription className="pt-1 text-sm text-foreground">{p.request_summary}</CardDescription>
        <div className="flex flex-wrap gap-1 pt-1">
          {p.trigger_rules.map((r) => (
            <code key={r} className="rounded bg-amber-100 px-1.5 py-0.5 font-mono text-xs text-amber-900">
              {r}
            </code>
          ))}
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-4 md:grid-cols-2">
          <section>
            <h4 className="mb-1.5 text-sm font-semibold">Hechos verificados</h4>
            {p.verified_facts.length ? (
              <ul className="space-y-1.5 text-sm">
                {p.verified_facts.map((f) => (
                  <li key={f.evidence_id} className="rounded-md border bg-emerald-50/60 px-2.5 py-1.5">
                    {f.fact}
                    <div className="font-mono text-xs text-muted-foreground">
                      {f.evidence_id} · {f.source}
                    </div>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Ninguno.</p>
            )}
            <h4 className="mb-1.5 mt-3 text-sm font-semibold">Acciones ya tomadas</h4>
            <ul className="space-y-1 text-sm">
              {p.actions_taken.map((a, i) => (
                <li key={i}>
                  {a.action} · {a.verified ? "verificada" : "sin verificar"}
                </li>
              ))}
              {p.actions_not_taken.map((a, i) => (
                <li key={`n${i}`} className="text-muted-foreground">
                  No ejecutada: {a.action} — {a.reason}
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h4 className="mb-1.5 text-sm font-semibold">Lo que dice el cliente (sin verificar)</h4>
            {p.customer_claims_unverified.length ? (
              <ul className="space-y-1 text-sm">
                {p.customer_claims_unverified.map((c, i) => (
                  <li key={i} className="rounded-md border border-dashed px-2.5 py-1.5">
                    {c.claim}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted-foreground">Nada que verificar.</p>
            )}
            {item.info_request && item.status === "info_requested" && (
              <p className="mt-3 rounded-md border border-sky-200 bg-sky-50 px-2.5 py-1.5 text-sm">
                Pregunta enviada al cliente: {item.info_request.question}
              </p>
            )}
            {item.customer_replies?.length ? (
              <>
                <h4 className="mb-1.5 mt-3 text-sm font-semibold">Respuestas del cliente (sin verificar)</h4>
                <ul className="space-y-1.5 text-sm">
                  {item.customer_replies.map((r, i) => (
                    <li key={i} className="rounded-md border border-dashed px-2.5 py-1.5">
                      <span className="text-muted-foreground">{r.question} → </span>{r.text}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            <h4 className="mb-1.5 mt-3 text-sm font-semibold">Preguntas abiertas</h4>
            <ul className="list-disc space-y-1 pl-4 text-sm">
              {p.open_questions.map((q, i) => (
                <li key={i}>{q}</li>
              ))}
            </ul>
          </section>
        </div>

        {p.policy_trace.length > 0 && (
          <p className="text-xs text-muted-foreground">Traza de política: {p.policy_trace.join(" · ")}</p>
        )}

        <Separator />
        <div className="flex flex-wrap items-center gap-2">
          <Input value={note} onChange={(e) => setNote(e.target.value)} placeholder="Nota, o la pregunta para el cliente" className="h-8 max-w-sm" />
          <Button size="sm" onClick={() => resolve("approved")} disabled={busy || !item.can_approve}
            title={item.can_approve ? "Abre la disputa y la verifica al releer" : "No hay una transacción verificada para disputar"}>
            <CheckCircle2 className="size-3.5" /> Aprobar y abrir disputa
          </Button>
          {item.status === "pending" && (
            <Button size="sm" variant="outline" onClick={() => resolve("info_requested")} disabled={busy || !note.trim()}
              title={note.trim() ? "La nota se envía al cliente como pregunta" : "Escribí la pregunta en la nota"}>
              <CircleHelp className="size-3.5" /> Pedir información
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={() => resolve("rejected")} disabled={busy}>
            <XCircle className="size-3.5" /> Rechazar
          </Button>
          <Collapsible className="ml-auto">
            <CollapsibleTrigger className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              <FileJson className="size-3.5" /> Paquete JSON
            </CollapsibleTrigger>
            <CollapsibleContent>
              <pre className="mt-2 max-h-72 max-w-[min(90vw,720px)] overflow-auto rounded-md bg-muted p-2 text-xs">
                {JSON.stringify(p, null, 2)}
              </pre>
            </CollapsibleContent>
          </Collapsible>
        </div>
        {error && <p className="text-sm text-rose-700">{error}</p>}
      </CardContent>
    </Card>
  )
}

function Login({ onToken }: { onToken: (token: string) => void }) {
  const [code, setCode] = useState("")
  const [error, setError] = useState<string | null>(null)
  const submit = async () => {
    setError(null)
    try {
      onToken((await api.agentLogin(code)).token)
    } catch (e) {
      setError(String(e))
    }
  }
  return (
    <Card className="mx-auto max-w-md">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <LockKeyhole className="size-4" /> Consola del agente
        </CardTitle>
        <CardDescription>
          La cola tiene datos de clientes: solo la abre un agente autenticado. En producción, SSO del banco; en la demo,
          el código de acceso de prueba documentado en el README.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); submit() }}>
          <Input type="password" value={code} onChange={(e) => setCode(e.target.value)} placeholder="Código de acceso" />
          <Button type="submit" disabled={!code}>Entrar</Button>
        </form>
        {error && <p className="mt-2 text-sm text-rose-700">{error}</p>}
      </CardContent>
    </Card>
  )
}

export function AgentConsole({ refreshKey }: { refreshKey: number }) {
  const [token, setToken] = useState<string | null>(null)
  const [pending, setPending] = useState<QueueItem[]>([])
  const [waiting, setWaiting] = useState<QueueItem[]>([])
  const [closed, setClosed] = useState<QueueItem[]>([])
  const load = useCallback(() => {
    if (!token) return
    const fail = () => setToken(null)
    api.handoffs(token, "pending").then(setPending).catch(fail)
    api.handoffs(token, "info_requested").then(setWaiting).catch(fail)
    api.handoffs(token, "closed").then(setClosed).catch(fail)
  }, [token])
  useEffect(() => {
    load()
  }, [load, refreshKey])

  if (!token) return <Login onToken={setToken} />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-lg font-semibold">Cola de casos escalados</h2>
          <p className="text-sm text-muted-foreground">
            Ordenada por prioridad y plazo regulatorio. Hechos verificados separados de lo que afirma el cliente; sin
            transcript crudo.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={load}>
          <RefreshCw className="size-3.5" /> Actualizar
        </Button>
      </div>
      {pending.length === 0 ? (
        <Card>
          <CardContent className="flex flex-col items-center gap-2 py-12 text-center text-muted-foreground">
            <Inbox className="size-8" />
            No hay casos pendientes. Escalá uno desde la vista Cliente (monto alto, robo o inyección).
          </CardContent>
        </Card>
      ) : (
        pending.map((item) => <CaseCard key={item.package.handoff_id} item={item} token={token} onResolved={load} />)
      )}
      {waiting.length > 0 && (
        <>
          <h3 className="pt-2 text-sm font-semibold">Esperando al cliente ({waiting.length}) · siguen abiertos</h3>
          {waiting.map((item) => (
            <CaseCard key={item.package.handoff_id} item={item} token={token} onResolved={load} />
          ))}
        </>
      )}
      {closed.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Cerrados ({closed.length})</CardTitle>
          </CardHeader>
          <CardContent className="space-y-1 text-sm">
            {closed.slice(-8).reverse().map((r) => (
              <p key={r.package.handoff_id}>
                <span className="font-mono">{r.package.handoff_id}</span> → {DECISION_LABEL[r.status] ?? r.status}
                {resultText(r) ? <span className="text-emerald-700"> · {resultText(r)}</span> : null}
                {r.agent_note ? <span className="text-muted-foreground"> · {r.agent_note}</span> : null}
              </p>
            ))}
          </CardContent>
        </Card>
      )}
    </div>
  )
}
