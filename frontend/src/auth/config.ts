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

/**
 * Demo accounts offered on the development sign-in page. Roles live in the database, not here:
 * migration 0004 creates these accounts with their roles (backend/app/services/demo_accounts.py),
 * and any other name starts as a tenant.
 */
export const DEMO_ACCOUNTS = [
  { user: "alice", role: "Tenant", description: "Report problems by text, photo, voice or scan, and follow them" },
  { user: "fm", role: "Facility manager", description: "Review queue, approvals, assigning technicians" },
  { user: "tech", role: "Technician", description: "Work orders assigned to you" },
  { user: "local-developer", role: "Administrator", description: "Users and roles, audit log, monitoring" },
] as const

export const SUGGESTED_DEV_USERS = DEMO_ACCOUNTS.map((account) => account.user)

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

/** The chosen development identity, or null when nobody is signed in (show the sign-in page). */
export function loadDevUser(): string | null {
  const stored = read(STORAGE_KEY)
  return stored && DEV_USER_PATTERN.test(stored) ? stored : null
}

export function clearDevUser(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY)
  } catch {
    // Nothing stored.
  }
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
