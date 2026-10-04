import { useEffect, useRef, useState } from "react"
import {
  CreditCard, KeyRound, Loader2, MessageCircle, RotateCcw, Send, ShieldCheck, Sparkles, X,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ActionCards, OutcomeBadge, TracePanel } from "@/components/shared"
import { api, type Scenario, type SessionInfo, type TurnResult, type Txn } from "@/lib/api"
import { cn } from "@/lib/utils"

type Entry = { role: "user"; text: string } | { role: "assistant"; turn: TurnResult }

const PATH_STYLE: Record<Scenario["path"], string> = {
  normal: "bg-emerald-50 text-emerald-800 border-emerald-200",
  ambiguous: "bg-sky-50 text-sky-800 border-sky-200",
  human: "bg-amber-50 text-amber-900 border-amber-200",
  attack: "bg-rose-50 text-rose-800 border-rose-200",
}
const PATH_LABEL: Record<Scenario["path"], string> = {
  normal: "Normal", ambiguous: "Ambiguo", human: "Humano", attack: "Ataque",
}

const T = {
  es: {
    hello: "Hola", accounts: "Tu tarjeta de crédito", recent: "Movimientos recientes",
    notMine: "¿No lo reconocés?", open: "¿Un cargo que no reconocés?", assistant: "Asistente de disputas",
    placeholder: "Escribí tu mensaje", confirm: "Sí, confirmo", cancel: "No, cancelar", otp: "Ingresá el código que te enviamos",
    verify: "Verificar", verified: "Identidad verificada", greeting:
      "Hola, soy el asistente de disputas. Contame qué cargo no reconocés: el monto, la fecha y el comercio me ayudan a encontrarlo.",
    date: "Fecha", merchant: "Comercio", amount: "Monto", status: "Estado",
  },
  pt: {
    hello: "Olá", accounts: "Seu cartão de crédito", recent: "Movimentações recentes",
    notMine: "Não reconhece?", open: "Uma cobrança que não reconhece?", assistant: "Assistente de contestações",
    placeholder: "Escreva sua mensagem", confirm: "Sim, confirmo", cancel: "Não, cancelar", otp: "Digite o código que enviamos",
    verify: "Verificar", verified: "Identidade verificada", greeting:
      "Olá, sou o assistente de contestações. Me conte qual cobrança não reconhece: valor, data e estabelecimento me ajudam a encontrá-la.",
    date: "Data", merchant: "Estabelecimento", amount: "Valor", status: "Situação",
  },
}

const fmtMoney = (v: number, cur: string, lang: "es" | "pt") =>
  `${v.toLocaleString(lang === "pt" ? "pt-BR" : "es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`

export function CustomerChat({ onEscalated }: { onEscalated: () => void }) {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [cid, setCid] = useState<string | null>(null)
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [txns, setTxns] = useState<Txn[]>([])
  const [entries, setEntries] = useState<Entry[]>([])
  const [open, setOpen] = useState(false)
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
  }, [entries, busy, open])

  async function start(s: Scenario) {
    setError(null)
    const r = await api.start(s.id)
    setScenario(r.scenario)
    setCid(r.conversation_id)
    setSession(r.session)
    setEntries([])
    setInput(r.scenario.message)
    setOtp("")
    api.transactions(r.conversation_id).then(setTxns).catch(() => setTxns([]))
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
    if (!cid || !otp) return
    try {
      const r = await api.stepUp(cid, otp)
      setSession(r.session)
      setOtp("")
      // Con la identidad verificada, se reintenta la confirmación pendiente.
      await send("choice:confirm", t.confirm)
    } catch (e) {
      setError(String(e))
    }
  }

  function disputeRow(x: Txn) {
    const msg = lang === "pt"
      ? `Não reconheço a cobrança de ${fmtMoney(x.amount, x.currency, lang)} em ${x.merchant} de ${x.date}.`
      : `No reconozco el cargo de ${fmtMoney(x.amount, x.currency, lang)} en ${x.merchant} del ${x.date}.`
    setInput(msg)
    setOpen(true)
  }

  const lang = scenario?.lang ?? "es"
  const t = T[lang]
  const last = entries.length ? entries[entries.length - 1] : null
  const lastTurn = last?.role === "assistant" ? last.turn : null
  const needsStepUp = !!lastTurn?.actions_not_taken.some((a) => a.reason?.includes("verificación adicional"))
    && !session?.high_active

  return (
    <div className="grid gap-4 lg:grid-cols-[260px_1fr]">
      {/* --- panel de demostración ------------------------------------ */}
      <Card className="h-fit">
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Sparkles className="size-4" /> Modo demostración
          </CardTitle>
          <CardDescription className="text-xs">Clientes reales del dataset, uno por camino.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1.5">
          {scenarios.map((s) => (
            <button
              key={s.id}
              onClick={() => { start(s); setOpen(true) }}
              className={cn(
                "w-full rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors hover:bg-muted",
                scenario?.id === s.id && "border-primary bg-primary/5",
              )}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="font-medium">{s.title}</span>
                <Badge variant="outline" className={cn("shrink-0 text-[10px]", PATH_STYLE[s.path])}>
                  {PATH_LABEL[s.path]}
                </Badge>
              </div>
              {scenario?.id === s.id && <p className="mt-1 text-muted-foreground">{s.hint}</p>}
            </button>
          ))}
          <p className="pt-2 text-[11px] text-muted-foreground">OTP de prueba: <code>123456</code></p>
        </CardContent>
      </Card>

      {/* --- home banking ----------------------------------------------- */}
      <div className="space-y-4">
        <Card className="overflow-hidden">
          <div className="bg-primary px-6 py-5 text-primary-foreground">
            <p className="text-sm opacity-80">LATAM Bank · {session?.country ?? ""}</p>
            <h2 className="text-2xl font-semibold">
              {t.hello}, <span className="font-mono text-lg">{session?.customer_id ?? "…"}</span>
            </h2>
          </div>
          <CardContent className="flex flex-wrap items-center gap-6 py-4">
            <div className="flex items-center gap-3">
              <div className="flex size-10 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <CreditCard className="size-5" />
              </div>
              <div>
                <p className="text-sm font-medium">{t.accounts}</p>
                <p className="text-xs text-muted-foreground">
                  {session?.high_active ? <span className="text-emerald-700">{t.verified}</span> : "Sesión estándar · solo lectura"}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base">{t.recent}</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>{t.date}</TableHead>
                  <TableHead>{t.merchant}</TableHead>
                  <TableHead className="text-right">{t.amount}</TableHead>
                  <TableHead>{t.status}</TableHead>
                  <TableHead />
                </TableRow>
              </TableHeader>
              <TableBody>
                {txns.map((x) => (
                  <TableRow key={x.transaction_id}>
                    <TableCell className="whitespace-nowrap text-muted-foreground">{x.date}</TableCell>
                    <TableCell className="font-medium">{x.merchant}</TableCell>
                    <TableCell className="whitespace-nowrap text-right font-mono">{fmtMoney(x.amount, x.currency, lang)}</TableCell>
                    <TableCell className="text-xs text-muted-foreground">{x.status}</TableCell>
                    <TableCell className="text-right">
                      <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={() => disputeRow(x)}>
                        {t.notMine}
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
                {txns.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={5} className="py-6 text-center text-muted-foreground">Sin movimientos.</TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      {/* --- asistente flotante ------------------------------------------ */}
      {!open && (
        <button
          onClick={() => setOpen(true)}
          className="fixed bottom-6 right-6 z-50 flex items-center gap-2 rounded-full bg-primary px-5 py-3.5 text-sm font-medium text-primary-foreground shadow-lg transition-transform hover:scale-105"
        >
          <MessageCircle className="size-5" /> {t.open}
        </button>
      )}

      {open && (
        <div className="fixed bottom-6 right-6 z-50 flex h-[min(680px,calc(100vh-3rem))] w-[min(430px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl">
          <div className="flex items-center gap-3 bg-primary px-4 py-3 text-primary-foreground">
            <div className="flex size-8 items-center justify-center rounded-full bg-white/15">
              <ShieldCheck className="size-4" />
            </div>
            <div className="flex-1">
              <p className="text-sm font-semibold leading-tight">{t.assistant}</p>
              <p className="text-[11px] opacity-80">VerifiCargo · {lang === "pt" ? "Português" : "Español"}</p>
            </div>
            {scenario && (
              <button onClick={() => start(scenario)} title="Reiniciar" className="rounded p-1 hover:bg-white/15">
                <RotateCcw className="size-4" />
              </button>
            )}
            <button onClick={() => setOpen(false)} title="Cerrar" className="rounded p-1 hover:bg-white/15">
              <X className="size-4" />
            </button>
          </div>

          <div className="flex-1 space-y-3 overflow-y-auto bg-muted/30 p-3">
            <div className="max-w-[90%] rounded-2xl rounded-bl-sm border bg-background px-3 py-2 text-sm">{t.greeting}</div>
            {entries.map((e, i) =>
              e.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3 py-2 text-sm text-primary-foreground">
                    {e.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="max-w-[94%] rounded-2xl rounded-bl-sm border bg-background px-3 py-2 text-sm">
                  <div className="mb-1"><OutcomeBadge outcome={e.turn.outcome} /></div>
                  <p className="whitespace-pre-line">{e.turn.reply}</p>
                  <ActionCards turn={e.turn} />
                  <TracePanel turn={e.turn} />
                </div>
              ),
            )}
            {busy && (
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <Loader2 className="size-3.5 animate-spin" /> …
              </div>
            )}
            {error && <p className="text-xs text-rose-700">{error}</p>}
            <div ref={bottom} />
          </div>

          <div className="space-y-2 border-t bg-background p-3">
            {lastTurn?.awaiting === "intent" && lastTurn.options.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {lastTurn.options.map((o) => (
                  <Button key={o.key} size="sm" variant="outline" className="h-7 text-xs"
                    onClick={() => send(`choice:${o.key}`, o.label)}>
                    {o.label}
                  </Button>
                ))}
              </div>
            )}
            {needsStepUp ? (
              <div className="flex items-center gap-2 rounded-lg border border-primary/30 bg-primary/5 p-2">
                <KeyRound className="size-4 shrink-0 text-primary" />
                <Input value={otp} onChange={(e) => setOtp(e.target.value)} placeholder={t.otp}
                  type="password" className="h-8" onKeyDown={(e) => e.key === "Enter" && verify()} />
                <Button size="sm" onClick={verify} disabled={!otp || busy}>{t.verify}</Button>
              </div>
            ) : lastTurn?.awaiting === "confirmation" ? (
              <div className="flex gap-1.5">
                <Button size="sm" className="h-7 text-xs" onClick={() => send("choice:confirm", t.confirm)} disabled={busy}>
                  {t.confirm}
                </Button>
                <Button size="sm" variant="outline" className="h-7 text-xs"
                  onClick={() => send("choice:cancel", t.cancel)} disabled={busy}>
                  {t.cancel}
                </Button>
              </div>
            ) : null}
            <form className="flex gap-2" onSubmit={(ev) => { ev.preventDefault(); send(input) }}>
              <Input value={input} onChange={(e) => setInput(e.target.value)} placeholder={t.placeholder} />
              <Button type="submit" size="icon" disabled={busy || !input.trim()}>
                <Send className="size-4" />
              </Button>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
