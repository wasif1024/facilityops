import { Code, ConnectError, type Transport } from "@connectrpc/connect"
import { TransportProvider, createConnectQueryKey } from "@connectrpc/connect-query"
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { type ReactNode, useMemo, useState } from "react"
import { RouterProvider, type createBrowserRouter } from "react-router"
import { Building2 } from "lucide-react"

import { createApiTransport } from "@/api/transport"
import { authMode } from "@/auth/config"
import { DevSignInPage } from "@/auth/dev-sign-in-page"
import { IdentityProvider, SessionProvider, useIdentity, useSession } from "@/auth/session"
import { ErrorState } from "@/components/app/states"
import { ThemeProvider } from "@/components/app/theme"
import { Skeleton } from "@/components/ui/skeleton"
import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { AccountService } from "@/gen/facilityops/v1/account_pb"
import { router as browserRouter } from "@/routes"

type DataRouter = ReturnType<typeof createBrowserRouter>

const RETRYABLE = new Set([Code.Unavailable, Code.Internal, Code.Unknown, Code.DeadlineExceeded])

function createQueryClient(): QueryClient {
  const meKey = createConnectQueryKey({ schema: AccountService.method.getMe, cardinality: undefined })
  // A permission change on the server (e.g. an admin removed a role) refreshes the session.
  // GetMe itself is skipped: a disabled account answers permission_denied and would loop.
  const refreshSessionOn = (error: unknown, isGetMe: boolean) => {
    if (!isGetMe && error instanceof ConnectError && error.code === Code.PermissionDenied) {
      void client.invalidateQueries({ queryKey: meKey })
    }
  }
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({
      onError: (error, query) => refreshSessionOn(error, JSON.stringify(query.queryKey).includes('"GetMe"')),
    }),
    mutationCache: new MutationCache({ onError: (error) => refreshSessionOn(error, false) }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        // Retry only failures that might be temporary (spec 9.1).
        retry: (attempt, error) => attempt < 2 && error instanceof ConnectError && RETRYABLE.has(error.code),
      },
    },
  })
  return client
}

function Transport({ override, children }: { override?: Transport; children: ReactNode }) {
  const { getAuthHeaders } = useIdentity()
  const transport = useMemo(() => override ?? createApiTransport(getAuthHeaders), [override, getAuthHeaders])
  return <TransportProvider transport={transport}>{children}</TransportProvider>
}

/** Nothing renders until we know who the user is and what they may do. */
function SessionGate({ router }: { router: DataRouter }) {
  const { session, isLoading, error, refresh } = useSession()
  if (isLoading) {
    return (
      <div className="mx-auto max-w-md space-y-3 p-10" aria-busy="true">
        <div className="flex items-center gap-2 font-semibold">
          <Building2 aria-hidden className="size-5" /> FacilityOps AI
        </div>
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }
  if (error || !session) {
    return (
      <div className="mx-auto max-w-lg space-y-4 p-10">
        <div className="flex items-center gap-2 font-semibold">
          <Building2 aria-hidden className="size-5" /> FacilityOps AI
        </div>
        <ErrorState error={error ?? new Error("No session")} onRetry={refresh} />
        <p className="text-muted-foreground text-sm">
          Is the backend running on {import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"}? See
          docs/commands.md.
        </p>
      </div>
    )
  }
  return <RouterProvider router={router} />
}

/** Development mode: nobody is signed in until a demo account is chosen. No default identity. */
function DevSignInGate({ router, children }: { router: DataRouter; children: ReactNode }) {
  const { devUser } = useIdentity()
  // After choosing an account, start on its own landing page, never on a page left in the URL.
  if (authMode === "dev" && !devUser) return <DevSignInPage onSignedIn={() => void router.navigate("/", { replace: true })} />
  return children
}

/**
 * `transport`, `router` and `devUser` are for tests: an in-memory backend, a memory router
 * and an already chosen development identity.
 */
export function App({
  transport,
  router = browserRouter,
  devUser,
}: { transport?: Transport; router?: DataRouter; devUser?: string } = {}) {
  const [queryClient] = useState(createQueryClient)
  return (
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <IdentityProvider initialDevUser={devUser}>
          <Transport override={transport}>
            <TooltipProvider>
              <DevSignInGate router={router}>
                <SessionProvider>
                  <SessionGate router={router} />
                </SessionProvider>
              </DevSignInGate>
            </TooltipProvider>
          </Transport>
        </IdentityProvider>
        <Toaster richColors closeButton />
      </QueryClientProvider>
    </ThemeProvider>
  )
}
