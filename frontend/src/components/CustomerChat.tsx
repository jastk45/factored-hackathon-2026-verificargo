import { useCallback, useState } from "react"
import { Code2, CreditCard, ExternalLink } from "lucide-react"
import { Button, buttonVariants } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ChatWidget, OPEN_EVENT } from "@/components/ChatWidget"
import { api, type Scenario, type SessionInfo, type Txn } from "@/lib/api"
import { cn } from "@/lib/utils"

// Home banking de demostración. El asistente es el MISMO componente que el
// widget embebible: acá se monta directo; en cualquier otra página, con una
// línea (<script src=".../widget.js">). Todo el flujo vive dentro del chat.

const T = {
  es: {
    hello: "Hola", accounts: "Tu tarjeta de crédito", recent: "Movimientos recientes",
    notMine: "¿No lo reconocés?", verified: "Identidad verificada",
    date: "Fecha", merchant: "Comercio", amount: "Monto", status: "Estado",
  },
  pt: {
    hello: "Olá", accounts: "Seu cartão de crédito", recent: "Movimentações recentes",
    notMine: "Não reconhece?", verified: "Identidade verificada",
    date: "Data", merchant: "Estabelecimento", amount: "Valor", status: "Situação",
  },
}

const fmtMoney = (v: number, cur: string, lang: "es" | "pt") =>
  `${v.toLocaleString(lang === "pt" ? "pt-BR" : "es-AR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ${cur}`

const SNIPPET = `<script src="${window.location.origin}/widget.js" data-scenario="normal-es"></script>`

export function CustomerChat({ onEscalated }: { onEscalated: () => void }) {
  const [scenario, setScenario] = useState<Scenario | null>(null)
  const [session, setSession] = useState<SessionInfo | null>(null)
  const [txns, setTxns] = useState<Txn[]>([])
  const [chatOpen, setChatOpen] = useState(false)

  const onConversation = useCallback((cid: string, s: SessionInfo, sc: Scenario) => {
    setScenario(sc)
    setSession(s)
    api.transactions(cid).then(setTxns).catch(() => setTxns([]))
  }, [])

  const lang = scenario?.lang ?? "es"
  const t = T[lang]

  function disputeRow(x: Txn) {
    // Sin comercio conocido, el mensaje no lo nombra (antes decía "en —").
    const known = x.merchant && x.merchant !== "—"
    const message = lang === "pt"
      ? `Não reconheço a cobrança de ${fmtMoney(x.amount, x.currency, lang)}${known ? ` em ${x.merchant}` : ""} de ${x.date}.`
      : `No reconozco el cargo de ${fmtMoney(x.amount, x.currency, lang)}${known ? ` en ${x.merchant}` : ""} del ${x.date}.`
    window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { message } }))
  }

  return (
    // Con el chat abierto (pantalla ancha) el contenido se corre a la izquierda:
    // el panel flotante tapaba la tabla y el enlace a la página de ejemplo.
    <div className={cn("space-y-4 transition-[padding] duration-200", chatOpen && "chat-open-pad")}>
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
        <CardContent className="flex flex-col gap-4 py-4 md:flex-row md:items-center">
          <div className="min-w-0 flex-1 space-y-2">
            <p className="flex items-center gap-2 text-sm font-medium">
              <Code2 className="size-4" /> El asistente es un widget
            </p>
            <p className="text-xs text-muted-foreground">
              Todo el flujo vive en el chat flotante. Para ponerlo en cualquier página, una línea:
            </p>
            <pre className="whitespace-pre-wrap break-all rounded-md bg-muted p-2 font-mono text-[11px]">{SNIPPET}</pre>
            <p className="text-[11px] text-muted-foreground">
              Opciones: <code>data-scenario</code> (cliente de demo), <code>data-demo="false"</code> (sin selector),{" "}
              <code>data-trace="true"</code> (traza de cada turno), <code>data-open="true"</code>.
            </p>
          </div>
          <a href="/demo-sitio.html" target="_blank" rel="noreferrer"
            className={cn(buttonVariants({ variant: "outline", size: "sm" }), "shrink-0 gap-1.5")}>
            Abrir página de ejemplo <ExternalLink className="size-3.5" />
          </a>
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

      <ChatWidget demo showTrace onConversation={onConversation} onSession={setSession}
        onEscalated={onEscalated} onOpenChange={setChatOpen} />
    </div>
  )
}
