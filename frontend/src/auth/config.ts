/**
 * Identity mode, fixed at build time (frontend spec section 4).
 *
 * dev:   no sign-in; an identity switcher sends X-Dev-User (backend AUTH_ENABLED=false).
 * entra: Microsoft Entra ID through MSAL; requests carry a bearer token.
 *
 * Because this is a build-time constant, the dev switcher is dropped from entra builds.
 */
export type AuthMode = "dev" | "entra"

export const authMode: AuthMode = import.meta.env.VITE_AUTH_MODE === "entra" ? "entra" : "dev"

export const DEV_USER_PATTERN = /^[a-z0-9._-]{1,64}$/
export const DEFAULT_DEV_USER = "local-developer"

/** Handy starting identities for a fresh database; grant roles with the backend CLI. */
export const SUGGESTED_DEV_USERS = ["local-developer", "alice", "fm", "tech"]

const STORAGE_KEY = "facilityops.devUser"
const RECENT_KEY = "facilityops.recentDevUsers"

// Browser storage can be unavailable (private mode, blocked storage); never let that break sign-in.
function read(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

function write(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // A convenience only; the app works without it.
  }
}

export function loadDevUser(): string {
  const stored = read(STORAGE_KEY)
  return stored && DEV_USER_PATTERN.test(stored) ? stored : DEFAULT_DEV_USER
}

export function saveDevUser(user: string): void {
  write(STORAGE_KEY, user)
  const recent = [user, ...loadRecentDevUsers().filter((u) => u !== user)].slice(0, 6)
  write(RECENT_KEY, JSON.stringify(recent))
}

export function loadRecentDevUsers(): string[] {
  try {
    const parsed: unknown = JSON.parse(read(RECENT_KEY) ?? "[]")
    return Array.isArray(parsed) ? parsed.filter((u): u is string => typeof u === "string" && DEV_USER_PATTERN.test(u)) : []
  } catch {
    return []
  }
}
