import { useCallback, useEffect, useRef, useState } from "react"
import { KeyRound, Loader2, MessageCircle, RotateCcw, Send, ShieldCheck, X } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { ActionCards, OutcomeBadge, TracePanel } from "@/components/shared"
import { api, type AgentQuestion, type Scenario, type SessionInfo, type TurnResult } from "@/lib/api"
import { cn } from "@/lib/utils"

// El asistente completo en un chat flotante: conversación, opciones,
// confirmación, verificación de identidad (OTP) y preguntas del especialista.
// Es el mismo componente en el home banking de la demo y en el widget
// embebible (src/widget.tsx), que lo monta en cualquier página con una línea.

type Entry =
  | { role: "user"; text: string }
  | { role: "assistant"; turn: TurnResult }
  | { role: "agent"; text: string; handoffId: string }
  | { role: "notice"; text: string }

const T = {
  es: {
    open: "¿Un cargo que no reconocés?", assistant: "Asistente de disputas",
    placeholder: "Escribí tu mensaje", confirm: "Sí, confirmo", cancel: "No, cancelar",
    agentAsks: "Un especialista te pregunta", replyPlaceholder: "Tu respuesta para el especialista",
    replySent: "Tu respuesta se envió al especialista (caso {id}). No hace falta que repitas lo anterior.",
    otp: "Ingresá el código que te enviamos", verify: "Verificar", demo: "Cliente de demostración",
    otpHint: "OTP de prueba: 123456",
    greeting: "Hola, soy el asistente de disputas. Contame qué cargo no reconocés: el monto, la fecha y el comercio me ayudan a encontrarlo.",
  },
  pt: {
    open: "Uma cobrança que não reconhece?", assistant: "Assistente de contestações",
    placeholder: "Escreva sua mensagem", confirm: "Sim, confirmo", cancel: "Não, cancelar",
    agentAsks: "Um especialista pergunta", replyPlaceholder: "Sua resposta para o especialista",
    replySent: "Sua resposta foi enviada ao especialista (caso {id}). Não precisa repetir o que já disse.",
    otp: "Digite o código que enviamos", verify: "Verificar", demo: "Cliente de demonstração",
    otpHint: "OTP de teste: 123456",
    greeting: "Olá, sou o assistente de contestações. Me conte qual cobrança não reconhece: valor, data e estabelecimento me ajudam a encontrá-la.",
  },
}

export interface ChatWidgetProps {
  /** Escenario de demo con el que arranca (por defecto, el primero). */
  scenarioId?: string
  /** Muestra el selector de clientes de demostración dentro del chat. */
  demo?: boolean
  /** Muestra la traza de cada turno (para jueces y desarrollo). */
  showTrace?: boolean
  /** Abierto al cargar. */
  defaultOpen?: boolean
  onConversation?: (cid: string, session: SessionInfo, scenario: Scenario) => void
  onSession?: (session: SessionInfo) => void
  onEscalated?: () => void
}

/** Abre el chat desde la página que lo aloja, opcionalmente con un mensaje. */
export const OPEN_EVENT = "verificargo:open"

export function ChatWidget({
  scenarioId, demo = true, showTrace = true, defaultOpen = false,
  onConversation, onSession, onEscalated,
}: ChatWidgetProps) {
  const [scenarios, setScenarios] = useState<Scenario[]>([])
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [cid, setCid] = useState<string | null>(null)
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [entries, setEntries] = useState<Entry[]>([])
  const [open, setOpen] = useState(defaultOpen)
  const [input, setInput] = useState("")
  const [otp, setOtp] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Pregunta abierta de un agente humano: el próximo mensaje va a ese caso.
  const [question, setQuestion] = useState<AgentQuestion | null>(null)
  const shownQuestions = useRef<Set<string>>(new Set())
  const bottom = useRef<HTMLDivElement>(null)

  const lang = scenario?.lang ?? "es"
  const t = T[lang]

  const start = useCallback(async (s: Scenario) => {
    setError(null)
    try {
      const r = await api.start(s.id)
      setScenario(r.scenario)
      setCid(r.conversation_id)
      setSession(r.session)
      setEntries([])
      setQuestion(null)
      setInput(r.scenario.message)
      setOtp("")
      onConversation?.(r.conversation_id, r.session, r.scenario)
    } catch (e) {
      setError(String(e))
    }
  }, [onConversation])

  useEffect(() => {
    api.scenarios().then((s) => {
      setScenarios(s)
      const first = s.find((x) => x.id === scenarioId) ?? s[0]
      if (first) start(first)
    }).catch((e) => setError(String(e)))
    // Solo al montar: cambiar de escenario lo hace el selector.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // La página que aloja el chat puede abrirlo con un mensaje ya escrito.
  useEffect(() => {
    const onOpen = (ev: Event) => {
      const message = (ev as CustomEvent<{ message?: string }>).detail?.message
      if (message) setInput(message)
      setOpen(true)
    }
    window.addEventListener(OPEN_EVENT, onOpen)
    return () => window.removeEventListener(OPEN_EVENT, onOpen)
  }, [])

  // "Pedir información" desde la consola llega acá.
  useEffect(() => {
    if (!cid) return
    const poll = () =>
      api.questions(cid).then((qs) => {
        const q = qs[0] ?? null
        const key = q ? `${q.handoff_id}·${q.asked_at}` : null
        if (q && key && !shownQuestions.current.has(key)) {
          shownQuestions.current.add(key)
          setEntries((e) => [...e, { role: "agent", text: q.question, handoffId: q.handoff_id }])
          setOpen(true)
        }
        setQuestion(q)
      }).catch(() => undefined)
    poll()
    const id = setInterval(poll, 4000)
    // Con la ventana oculta el navegador frena los timers: al volver, se consulta.
    const onVisible = () => document.visibilityState === "visible" && poll()
    document.addEventListener("visibilitychange", onVisible)
    return () => {
      clearInterval(id)
      document.removeEventListener("visibilitychange", onVisible)
    }
  }, [cid])

  // Con llaves: en Chrome reciente scrollIntoView devuelve una Promise, y React
  // tomaría ese valor como función de limpieza del efecto.
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "nearest" })
  }, [entries, busy, open])

  const updateSession = (s: SessionInfo) => {
    setSession(s)
    onSession?.(s)
  }

  async function send(text: string, display?: string) {
    if (!cid || !text.trim() || busy) return
    setBusy(true)
    setError(null)
    setEntries((e) => [...e, { role: "user", text: display ?? text }])
    setInput("")
    try {
      if (question) {
        await api.replyToAgent(cid, question.handoff_id, text)
        setEntries((e) => [...e, { role: "notice", text: t.replySent.replace("{id}", question.handoff_id) }])
        setQuestion(null)
        onEscalated?.()
        return
      }
      const r = await api.send(cid, text)
      setEntries((e) => [...e, { role: "assistant", turn: r.turn }])
      updateSession(r.session)
      if (r.turn.outcome === "ESCALATED") onEscalated?.()
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
      updateSession(r.session)
      setOtp("")
      // Con la identidad verificada, se reintenta la confirmación pendiente.
      await send("choice:confirm", t.confirm)
    } catch (e) {
      setError(String(e))
    }
  }

  const last = entries.length ? entries[entries.length - 1] : null
  const lastTurn = last?.role === "assistant" ? last.turn : null
  const needsStepUp = !!lastTurn?.actions_not_taken.some((a) => a.reason?.includes("verificación adicional"))
    && !session?.high_active

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="fixed bottom-6 right-6 z-[2147483000] flex items-center gap-2 rounded-full bg-primary px-5 py-3.5 font-sans text-sm font-medium text-primary-foreground shadow-lg transition-transform hover:scale-105"
      >
        <MessageCircle className="size-5" /> {t.open}
      </button>
    )
  }

  return (
    <div className="fixed bottom-6 right-6 z-[2147483000] flex h-[min(680px,calc(100vh-3rem))] w-[min(430px,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border bg-background font-sans text-foreground shadow-2xl">
      <div className="flex items-center gap-3 bg-primary px-4 py-3 text-primary-foreground">
        <div className="flex size-8 items-center justify-center rounded-full bg-white/15">
          <ShieldCheck className="size-4" />
        </div>
        <div className="flex-1">
          <p className="text-sm font-semibold leading-tight">{t.assistant}</p>
          <p className="text-[11px] opacity-80">
            VerifiCargo · {lang === "pt" ? "Português" : "Español"}
            {session?.country ? ` · ${session.country}` : ""}
          </p>
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

      {demo && scenarios.length > 0 && (
        <div className="space-y-1 border-b bg-muted/40 px-3 py-2">
          <label className="flex items-center gap-2 text-[11px] text-muted-foreground">
            {t.demo}
            <select
              className="min-w-0 flex-1 rounded-md border bg-background px-1.5 py-1 text-xs text-foreground"
              value={scenario?.id ?? ""}
              onChange={(e) => {
                const s = scenarios.find((x) => x.id === e.target.value)
                if (s) start(s)
              }}
            >
              {scenarios.map((s) => (
                <option key={s.id} value={s.id}>{s.title}</option>
              ))}
            </select>
          </label>
          {scenario && (
            <p className="text-[11px] leading-snug text-muted-foreground">
              {scenario.hint} · <span className="font-mono">{t.otpHint}</span>
            </p>
          )}
        </div>
      )}

      <div className="flex-1 space-y-3 overflow-y-auto bg-muted/30 p-3">
        <div className="max-w-[90%] rounded-2xl rounded-bl-sm border bg-background px-3 py-2 text-sm">{t.greeting}</div>
        {entries.map((e, i) =>
          e.role === "agent" ? (
            <div key={i} className="max-w-[94%] rounded-2xl rounded-bl-sm border border-amber-300 bg-amber-50 px-3 py-2 text-sm">
              <p className="mb-1 text-xs font-semibold text-amber-900">{t.agentAsks} · {e.handoffId}</p>
              <p>{e.text}</p>
            </div>
          ) : e.role === "notice" ? (
            <p key={i} className="text-center text-xs text-muted-foreground">{e.text}</p>
          ) : e.role === "user" ? (
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
              {showTrace && <TracePanel turn={e.turn} />}
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
        <form className={cn("flex gap-2")} onSubmit={(ev) => { ev.preventDefault(); send(input) }}>
          <Input value={input} onChange={(e) => setInput(e.target.value)}
            placeholder={question ? t.replyPlaceholder : t.placeholder} />
          <Button type="submit" size="icon" disabled={busy || !input.trim()}>
            <Send className="size-4" />
          </Button>
        </form>
      </div>
    </div>
  )
}
