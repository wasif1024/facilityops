import { useState } from "react"
import { Briefcase, Building2, Home, Loader2 } from "lucide-react"

import { type Realm, availableRealms, signIn } from "@/auth/msal"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"

/**
 * Production sign-in (frontend spec 4.1). Residents use Entra External ID, where they
 * can create an account; staff use their organisation's work account. Passwords,
 * verification and bot protection are handled on Microsoft's hosted pages.
 */
export function SignInPage() {
  const [pending, setPending] = useState<Realm | null>(null)
  const [error, setError] = useState<string | null>(null)

  const go = async (realm: Realm) => {
    setPending(realm)
    setError(null)
    try {
      await signIn(realm)
    } catch (error) {
      console.error("Sign-in failed to start", error)
      setPending(null)
      setError("Sign-in couldn't start. Please try again.")
    }
  }

  return (
    <main className="bg-muted/40 flex min-h-svh items-center justify-center p-4">
      <div className="w-full max-w-md space-y-6">
        <div className="flex items-center justify-center gap-2 text-xl font-semibold">
          <Building2 aria-hidden className="text-primary size-6" /> FacilityOps AI
        </div>
        {availableRealms.includes("resident") && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Home aria-hidden className="size-4" /> Residents
              </CardTitle>
              <CardDescription>Report problems in your building and follow them.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button className="w-full" disabled={pending !== null} onClick={() => void go("resident")}>
                {pending === "resident" && <Loader2 aria-hidden className="animate-spin" />}
                Sign in or create an account
              </Button>
            </CardContent>
          </Card>
        )}
        {availableRealms.includes("staff") && (
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Briefcase aria-hidden className="size-4" /> Staff
              </CardTitle>
              <CardDescription>Facility managers, technicians and administrators.</CardDescription>
            </CardHeader>
            <CardContent>
              <Button variant="outline" className="w-full" disabled={pending !== null} onClick={() => void go("staff")}>
                {pending === "staff" && <Loader2 aria-hidden className="animate-spin" />}
                Sign in with your work account
              </Button>
            </CardContent>
          </Card>
        )}
        {availableRealms.length === 0 && (
          <p className="text-muted-foreground text-center text-sm">
            Sign-in is not configured. Set the VITE_ENTRA_* or VITE_EXTERNAL_* settings (see .env.example).
          </p>
        )}
        {error && <p className="text-destructive text-center text-sm">{error}</p>}
      </div>
    </main>
  )
}
