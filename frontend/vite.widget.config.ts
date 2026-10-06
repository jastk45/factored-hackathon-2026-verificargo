import path from "path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"

// Construye dist/widget.js: un solo archivo con React, el chat y sus estilos,
// para pegar en cualquier página con <script src=".../widget.js">.
// Corre después del build de la app (no vacía dist).
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  build: {
    outDir: "dist",
    emptyOutDir: false,
    // Las fuentes van dentro del archivo: el widget no puede pedirlas a la
    // página que lo aloja.
    assetsInlineLimit: 1_000_000,
    lib: {
      entry: path.resolve(__dirname, "src/widget.tsx"),
      name: "VerifiCargoWidget",
      formats: ["iife"],
      fileName: () => "widget.js",
    },
  },
})
