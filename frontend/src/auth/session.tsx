import { useQuery } from "@connectrpc/connect-query"
import { useQueryClient } from "@tanstack/react-query"
import { createContext, type ReactNode, useCallback, useContext, useMemo, useRef, useState } from "react"

import { authMode, clearDevUser, loadDevUser, saveDevUser } from "@/auth/config"
import type { PermissionId } from "@/auth/permissions"
import { AccountService } from "@/gen/facilityops/v1/account_pb"

/** Who is calling the API. The transport reads this on every request. */
interface IdentityValue {
  /** Development mode only: who is signed in, or null before anyone has chosen an identity. */
  devUser: string | null
  switchDevUser: (user: string) => void
  signOutDev: () => void
  getAuthHeaders: () => Promise<Record<string, string>>
}

const IdentityContext = createContext<IdentityValue | null>(null)

/** `initialDevUser` is for tests; the app reads the stored identity. */
export function IdentityProvider({ children, initialDevUser }: { children: ReactNode; initialDevUser?: string }) {
  const queryClient = useQueryClient()
  const [devUser, setDevUser] = useState<string | null>(() => initialDevUser ?? loadDevUser())
  // The transport keeps one stable callback; a ref lets it see the current user.
  const devUserRef = useRef(devUser)

  const switchDevUser = useCallback(
    (user: string) => {
      saveDevUser(user)
      devUserRef.current = user
      setDevUser(user)
      // Never show one user's cached data to another.
      queryClient.clear()
    },
    [queryClient],
  )

  const signOutDev = useCallback(() => {
    clearDevUser()
    devUserRef.current = null
    setDevUser(null)
    queryClient.clear()
  }, [queryClient])

  const getAuthHeaders = useCallback(async (): Promise<Record<string, string>> => {
    if (authMode === "entra") {
      // Loaded only in Entra builds, so development bundles don't carry MSAL.
      const { accessToken } = await import("@/auth/msal")
      return { Authorization: `Bearer ${await accessToken()}` }
    }
    // Never fall back to a default identity: without a choice, no request is sent (the sign-in page shows).
    return devUserRef.current ? { "X-Dev-User": devUserRef.current } : {}
  }, [])

  const value = useMemo(
    () => ({ devUser, switchDevUser, signOutDev, getAuthHeaders }),
    [devUser, switchDevUser, signOutDev, getAuthHeaders],
  )
  return <IdentityContext.Provider value={value}>{children}</IdentityContext.Provider>
}

export function useIdentity(): IdentityValue {
  const value = useContext(IdentityContext)
  if (!value) throw new Error("useIdentity must be used inside IdentityProvider")
  return value
}

export interface Session {
  userId: string
  displayName: string
  roles: string[]
  permissions: ReadonlySet<string>
}

interface SessionValue {
  session: Session | undefined
  isLoading: boolean
  error: unknown
  can: (permission: PermissionId) => boolean
  canAny: (...permissions: PermissionId[]) => boolean
  refresh: () => void
  signOut: () => void
}

const SessionContext = createContext<SessionValue | null>(null)

/**
 * The session is AccountService.GetMe: identity, roles and permissions (frontend spec 4.2).
 * The UI shows only what these permissions allow; the backend enforces everything again.
 */
export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const { signOutDev } = useIdentity()
  const me = useQuery(AccountService.method.getMe, {}, { retry: false, staleTime: 60_000 })

  const value = useMemo<SessionValue>(() => {
    const session = me.data
      ? {
          userId: me.data.userId,
          displayName: me.data.displayName,
          roles: me.data.roles,
          permissions: new Set(me.data.permissions),
        }
      : undefined
    return {
      session,
      isLoading: me.isLoading,
      error: me.error,
      can: (permission) => session?.permissions.has(permission) ?? false,
      canAny: (...permissions) => permissions.some((p) => session?.permissions.has(p)),
      refresh: () => void me.refetch(),
      signOut: () => {
        queryClient.clear()
        if (authMode === "entra") void import("@/auth/msal").then((msal) => msal.signOut())
        else signOutDev()
      },
    }
  }, [me, queryClient, signOutDev])

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionValue {
  const value = useContext(SessionContext)
  if (!value) throw new Error("useSession must be used inside SessionProvider")
  return value
}
