import { StrictMode } from "react"
import { createRoot } from "react-dom/client"

import "./index.css"
import { App } from "@/app"
import { authMode } from "@/auth/config"
import { applyInitialTheme } from "@/components/app/theme"

async function start() {
  applyInitialTheme()
  const root = createRoot(document.getElementById("root")!)
  if (authMode === "entra") {
    // Finish or start the Entra redirect sign-in before rendering anything that calls the API.
    const { ensureSignedIn } = await import("@/auth/msal")
    root.render(<p className="p-10 text-center">Signing in…</p>)
    if (!(await ensureSignedIn())) return // the browser is redirecting to Microsoft sign-in
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void start()
