// Widget embebible de VerifiCargo: una línea en cualquier página.
//
//   <script src="https://<servidor>/widget.js" data-scenario="normal-es"></script>
//
// Atributos opcionales:
//   data-api       servidor de la API (por defecto, el mismo que sirve el script)
//   data-scenario  cliente de demostración con el que arranca
//   data-demo      "false" oculta el selector de clientes de demo
//   data-trace     "true" muestra la traza de cada turno
//   data-open      "true" abre el chat al cargar
//
// La página puede abrirlo con un mensaje: window.VerifiCargo.open("texto").
//
// Se monta en un Shadow DOM: los estilos de la página no rompen el chat y los
// del chat no tocan la página. Las reglas @font-face y @property de Tailwind
// no funcionan dentro de un shadow root, así que esas dos se registran en el
// documento (son globales e inofensivas).

import { createRoot } from "react-dom/client"
import { ChatWidget, OPEN_EVENT } from "@/components/ChatWidget"
import { setApiBase } from "@/lib/api"
import css from "./index.css?inline"

declare global {
  interface Window {
    VerifiCargo?: { open: (message?: string) => void }
  }
}

const script = document.currentScript as HTMLScriptElement | null
const cfg = script?.dataset ?? {}
setApiBase(cfg.api ?? (script ? new URL(script.src).origin : ""))

function documentLevelRules(source: string): string {
  const rules: string[] = []
  for (const at of ["@font-face", "@property"]) {
    let from = 0
    while ((from = source.indexOf(at, from)) !== -1) {
      const end = source.indexOf("}", from)
      rules.push(source.slice(from, end + 1))
      from = end + 1
    }
  }
  return rules.join("\n")
}

function mount() {
  if (document.getElementById("verificargo-widget")) return
  const global = document.createElement("style")
  global.textContent = documentLevelRules(css)
  document.head.appendChild(global)

  const host = document.createElement("div")
  host.id = "verificargo-widget"
  document.body.appendChild(host)
  const shadow = host.attachShadow({ mode: "open" })
  const style = document.createElement("style")
  // Las variables del tema viven en :root; dentro del shadow root, en :host.
  style.textContent = css.split(":root").join(":host")
  shadow.appendChild(style)
  const app = document.createElement("div")
  shadow.appendChild(app)

  createRoot(app).render(
    <ChatWidget
      scenarioId={cfg.scenario}
      demo={cfg.demo !== "false"}
      showTrace={cfg.trace === "true"}
      defaultOpen={cfg.open === "true"}
    />,
  )
}

window.VerifiCargo = {
  open: (message?: string) => window.dispatchEvent(new CustomEvent(OPEN_EVENT, { detail: { message } })),
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", mount)
else mount()
