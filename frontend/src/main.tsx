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
    // Finish a redirect sign-in if one is in progress; otherwise show the sign-in page.
    const [{ completeSignIn }, { SignInPage }] = await Promise.all([
      import("@/auth/msal"),
      import("@/auth/sign-in-page"),
    ])
    root.render(<p className="p-10 text-center">Signing in…</p>)
    if (!(await completeSignIn())) {
      root.render(<SignInPage />)
      return
    }
  }
  root.render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void start()
