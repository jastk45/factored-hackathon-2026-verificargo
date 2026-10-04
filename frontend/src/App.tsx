import { useEffect, useState } from "react"
import { ShieldCheck } from "lucide-react"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AgentConsole } from "@/components/AgentConsole"
import { CustomerChat } from "@/components/CustomerChat"
import { EvalView } from "@/components/EvalView"
import { api } from "@/lib/api"

export default function App() {
  const [tab, setTab] = useState("cliente")
  const [queueVersion, setQueueVersion] = useState(0)
  const [provider, setProvider] = useState<string>("")

  useEffect(() => {
    api.health().then((h) => setProvider(h.llm_provider)).catch(() => setProvider("sin conexión"))
  }, [])

  return (
    <div className="min-h-screen bg-muted/30">
      <header className="border-b bg-background">
        <div className="mx-auto flex max-w-7xl items-center gap-3 px-4 py-3">
          <div className="flex size-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            <ShieldCheck className="size-5" />
          </div>
          <div>
            <h1 className="text-lg font-semibold leading-tight">VerifiCargo</h1>
            <p className="text-xs text-muted-foreground">Disputas de cargos con tarjeta · LATAM Bank · es / pt</p>
          </div>
          <span className="ml-auto rounded-full border px-2.5 py-0.5 text-xs text-muted-foreground">
            extracción: {provider === "none" ? "reglas (sin modelo)" : provider || "…"}
          </span>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-4 py-4">
        <Tabs value={tab} onValueChange={(v) => setTab(String(v))}>
          <TabsList>
            <TabsTrigger value="cliente">Cliente</TabsTrigger>
            <TabsTrigger value="agente">Agente humano</TabsTrigger>
            <TabsTrigger value="eval">Evaluación</TabsTrigger>
          </TabsList>
          <TabsContent value="cliente" keepMounted>
            <CustomerChat onEscalated={() => setQueueVersion((v) => v + 1)} />
          </TabsContent>
          <TabsContent value="agente">
            <AgentConsole refreshKey={queueVersion} />
          </TabsContent>
          <TabsContent value="eval">
            <EvalView />
          </TabsContent>
        </Tabs>
      </main>
    </div>
  )
}
