import { useState } from "react"
import { Briefcase, Building2, ChevronRight, Home, ShieldCheck, Wrench } from "lucide-react"

import { DEMO_ACCOUNTS, DEV_USER_PATTERN } from "@/auth/config"
import { useIdentity } from "@/auth/session"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"

const ICONS = {
  Tenant: Home,
  "Facility manager": Briefcase,
  Technician: Wrench,
  Administrator: ShieldCheck,
} as const

/**
 * Local development only (VITE_AUTH_MODE=dev): choose a demo account instead of signing in.
 * There are no passwords here; what each account may do comes from its roles in the database,
 * and the backend checks those permissions on every call. Production uses Microsoft Entra
 * sign-in instead (sign-in-page.tsx), and this page is not part of that build path.
 */
export function DevSignInPage({ onSignedIn }: { onSignedIn?: () => void }) {
  const { switchDevUser } = useIdentity()
  const signIn = (user: string) => {
    onSignedIn?.()
    switchDevUser(user)
  }
  const [other, setOther] = useState("")
  const valid = DEV_USER_PATTERN.test(other)

  return (
    <main className="bg-muted/40 flex min-h-svh items-center justify-center p-4">
      <div className="w-full max-w-lg space-y-6">
        <div className="space-y-2 text-center">
          <div className="flex items-center justify-center gap-2 text-xl font-semibold">
            <Building2 aria-hidden className="text-primary size-6" /> FacilityOps AI
          </div>
          <Badge variant="secondary">Development mode · no passwords</Badge>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Choose a demo account</CardTitle>
            <CardDescription>
              Each role sees only what its permissions allow, and the backend enforces them on every request.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {DEMO_ACCOUNTS.map((account) => {
              const Icon = ICONS[account.role]
              return (
                <Button
                  key={account.user}
                  variant="outline"
                  className="h-auto w-full justify-start gap-3 py-3 text-left whitespace-normal"
                  onClick={() => signIn(account.user)}
                >
                  <Icon aria-hidden className="size-5 shrink-0" />
                  <span className="flex-1">
                    <span className="block font-medium">
                      {account.role} <span className="text-muted-foreground font-normal">· {account.user}</span>
                    </span>
                    <span className="text-muted-foreground block text-xs font-normal">{account.description}</span>
                  </span>
                  <ChevronRight aria-hidden className="text-muted-foreground size-4 shrink-0" />
                </Button>
              )
            })}

            <form
              className="flex gap-2 pt-2"
              onSubmit={(event) => {
                event.preventDefault()
                if (valid) signIn(other)
              }}
            >
              <Input
                aria-label="Another account name"
                placeholder="Another name (starts as a tenant)"
                value={other}
                onChange={(event) => setOther(event.target.value.toLowerCase())}
                aria-invalid={other !== "" && !valid}
              />
              <Button type="submit" disabled={!valid}>
                Continue
              </Button>
            </form>
          </CardContent>
        </Card>

        <p className="text-muted-foreground text-center text-xs">
          These demo accounts are created by the database migrations for learning only. Remove them with{" "}
          <code>scripts/db.sh remove-demo-accounts</code> before real use. In production, people sign in with Microsoft
          Entra ID.
        </p>
      </div>
    </main>
  )
}
