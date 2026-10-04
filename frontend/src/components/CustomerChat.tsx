import { useEffect, useRef, useState } from "react"
import { KeyRound, Loader2, RotateCcw, Send, ShieldCheck, UserRound } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Badge } from "@/components/ui/badge"
import { ActionCards, OutcomeBadge, TracePanel } from "@/components/shared"
import { api, type Scenario, type SessionInfo, type TurnResult } from "@/lib/api"
import { cn } from "@/lib/utils"

type Entry = { role: "user"; text: string } | { role: "assistant"; turn: TurnResult }

const PATH_STYLE: Record<Scenario["path"], string> = {
  normal: "bg-emerald-50 text-emerald-800 border-emerald-200",
  ambiguous: "bg-sky-50 text-sky-800 border-sky-200",
  human: "bg-amber-50 text-amber-900 border-amber-200",
  attack: "bg-rose-50 text-rose-800 border-rose-200",
}
const PATH_LABEL: Record<Scenario["path"], string> = {
  normal: "Normal",
  ambiguous: "Ambiguo",
  human: "Humano",
  attack: "Ataque",
}

export function CustomerChat({ onEscalated }: { onEscalated: () => void }) {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [cid, setCid] = useState<string | null>(null)
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [input, setInput] = useState("")
  const [otp, setOtp] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const bottom = useRef<HTMLDivElement>(null)

  useEffect(() => {
    api.scenarios().then((s) => {
      setScenarios(s)
      if (s.length) start(s[0])
    }).catch((e) => setError(String(e)))
  }, [])

  // Con llaves: en Chrome reciente scrollIntoView devuelve una Promise, y React
  // tomaría ese valor como función de limpieza del efecto.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth" })
  }, [entries, busy])

  async function start(s: Scenario) {
    setError(null)
    const r = await api.start(s.id)
    setScenario(r.scenario)
    setCid(r.conversation_id)
    setSession(r.session)
    setEntries([])
    setInput(r.scenario.message)
  }

  async function send(text: string, display?: string) {
    if (!cid || !text.trim() || busy) return
    setBusy(true)
    setError(null)
    setEntries((e) => [...e, { role: "user", text: display ?? text }])
    setInput("")
    try {
      const r = await api.send(cid, text)
      setEntries((e) => [...e, { role: "assistant", turn: r.turn }])
      setSession(r.session)
      if (r.turn.outcome === "ESCALATED") onEscalated()
    } catch (e) {
      setError(String(e))
    } finally {
      setBusy(false)
    }
  }

  async function verify() {
    if (!cid) return
    try {
      const r = await api.stepUp(cid, otp)
      setSession(r.session)
      setOtp("")
    } catch (e) {
      setError(String(e))
    }
  }

  const last = entries.length ? entries[entries.length - 1] : null
  const lastTurn = last?.role === "assistant" ? last.turn : null
  const lang = scenario?.lang ?? "es"

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_1fr]">
      <div className="space-y-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">Escenarios de demostración</CardTitle>
            <CardDescription>Clientes reales del dataset elegidos para cada camino.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {scenarios.map((s) => (
              <button
                key={s.id}
                onClick={() => start(s)}
                className={cn(
                  "w-full rounded-md border px-3 py-2 text-left text-sm transition-colors hover:bg-muted",
                  scenario?.id === s.id && "border-primary bg-primary/5",
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{s.title}</span>
                  <Badge variant="outline" className={cn("shrink-0 text-[10px]", PATH_STYLE[s.path])}>
                    {PATH_LABEL[s.path]}
                  </Badge>
                </div>
                {scenario?.id === s.id && <p className="mt-1 text-xs text-muted-foreground">{s.hint}</p>}
              </button>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base">
              <UserRound className="size-4" /> Sesión
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            {session?.valid ? (
              <>
                <p className="font-mono text-xs">{session.customer_id}</p>
                <p className="text-muted-foreground">
                  País {session.country} · {Math.floor((session.seconds_remaining ?? 0) / 60)} min restantes
                </p>
                {session.high_active ? (
                  <p className="flex items-center gap-1.5 text-emerald-700">
                    <ShieldCheck className="size-4" /> Identidad verificada (5 min)
                  </p>
                ) : (
                  <div className="space-y-1.5">
                    <p className="text-muted-foreground">Para actuar sobre la cuenta hace falta verificar identidad.</p>
                    <div className="flex gap-2">
                      <Input
                        value={otp}
                        onChange={(e) => setOtp(e.target.value)}
                        placeholder="OTP (demo: 123456)"
                        type="password"
                        className="h-8"
                      />
                      <Button size="sm" variant="outline" onClick={verify} disabled={!otp}>
                        <KeyRound className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                )}
              </>
            ) : (
              <p className="text-rose-700">{session?.error ?? "Sin sesión"}</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="flex min-h-[640px] flex-col">
        <CardHeader className="flex flex-row items-center justify-between border-b pb-3">
          <div>
            <CardTitle className="text-base">{scenario?.title ?? "Chat"}</CardTitle>
            <CardDescription>
              {lang === "pt" ? "Português" : "Español"} · el modelo entiende, el código decide y actúa
            </CardDescription>
          </div>
          {scenario && (
            <Button variant="ghost" size="sm" onClick={() => start(scenario)}>
              <RotateCcw className="size-3.5" /> Reiniciar
            </Button>
          )}
        </CardHeader>

        <CardContent className="flex-1 space-y-4 overflow-y-auto py-4">
          {entries.length === 0 && (
            <p className="py-10 text-center text-sm text-muted-foreground">
              El mensaje sugerido del escenario ya está en la caja de texto. Envialo o escribí el tuyo.
            </p>
          )}
          {entries.map((e, i) =>
            e.role === "user" ? (
              <div key={i} className="flex justify-end">
                <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
                  {e.text}
                </div>
              </div>
            ) : (
              <div key={i} className="flex justify-start">
                <div className="max-w-[88%] rounded-2xl rounded-bl-sm border bg-card px-3.5 py-2.5 text-sm shadow-xs">
                  <div className="mb-1.5">
                    <OutcomeBadge outcome={e.turn.outcome} />
                  </div>
                  <p className="whitespace-pre-line">{e.turn.reply}</p>
                  <ActionCards turn={e.turn} />
                  <TracePanel turn={e.turn} />
                </div>
              </div>
            ),
          )}
          {busy && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Procesando…
            </div>
          )}
          {error && <p className="text-sm text-rose-700">{error}</p>}
          <div ref={bottom} />
        </CardContent>

        <div className="space-y-2 border-t p-3">
          {lastTurn?.awaiting === "intent" && lastTurn.options.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {lastTurn.options.map((o) => (
                <Button key={o.key} size="sm" variant="outline" onClick={() => send(`choice:${o.key}`, o.label)}>
                  {o.label}
                </Button>
              ))}
            </div>
          )}
          {lastTurn?.awaiting === "confirmation" && (
            <div className="flex gap-2">
              <Button size="sm" onClick={() => send(lang === "pt" ? "Sim, confirmo" : "Sí, confirmo")}>
                {lang === "pt" ? "Sim, confirmo" : "Sí, confirmo"}
              </Button>
            </div>
          )}
          <form
            className="flex gap-2"
            onSubmit={(ev) => {
              ev.preventDefault()
              send(input)
            }}
          >
            <Input
              value={input}
              onChange={(e) => setInput(e.target.value)}
              placeholder={lang === "pt" ? "Escreva sua mensagem" : "Escribí tu mensaje"}
            />
            <Button type="submit" disabled={busy || !input.trim()}>
              <Send className="size-4" />
            </Button>
          </form>
        </div>
      </Card>
    </div>
  )
}
