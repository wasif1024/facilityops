import {
  type AccountInfo,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser"

/**
 * Microsoft sign-in for production builds (VITE_AUTH_MODE=entra), with two doors:
 *
 * - residents: Microsoft Entra External ID (https://<subdomain>.ciamlogin.com). Self-service
 *   sign-up, email verification and password reset happen on Microsoft's hosted pages.
 * - staff: the organisation's workforce Entra ID tenant.
 *
 * Each realm has its own SPA app registration and API scope. Tokens stay in MSAL's
 * sessionStorage cache; nothing sensitive goes to localStorage.
 */
export type Realm = "resident" | "staff"

interface RealmConfig {
  clientId: string
  authority: string
  scope: string
  knownAuthorities?: string[]
}

const env = import.meta.env

const realmConfigs: Partial<Record<Realm, RealmConfig>> = {}
if (
  env.VITE_EXTERNAL_CLIENT_ID &&
  env.VITE_EXTERNAL_TENANT_SUBDOMAIN &&
  env.VITE_EXTERNAL_TENANT_ID &&
  env.VITE_EXTERNAL_API_SCOPE
) {
  realmConfigs.resident = {
    clientId: env.VITE_EXTERNAL_CLIENT_ID,
    // The tenant id, not the bare host: with https://<subdomain>.ciamlogin.com/ MSAL
    // resolves the domain-name metadata and fails with endpoints_resolution_error.
    authority: `https://${env.VITE_EXTERNAL_TENANT_SUBDOMAIN}.ciamlogin.com/${env.VITE_EXTERNAL_TENANT_ID}`,
    scope: env.VITE_EXTERNAL_API_SCOPE,
    knownAuthorities: [`${env.VITE_EXTERNAL_TENANT_SUBDOMAIN}.ciamlogin.com`],
  }
}
if (env.VITE_ENTRA_CLIENT_ID && env.VITE_ENTRA_TENANT_ID && env.VITE_ENTRA_API_SCOPE) {
  realmConfigs.staff = {
    clientId: env.VITE_ENTRA_CLIENT_ID,
    authority: `https://login.microsoftonline.com/${env.VITE_ENTRA_TENANT_ID}`,
    scope: env.VITE_ENTRA_API_SCOPE,
  }
}

export const availableRealms = (Object.keys(realmConfigs) as Realm[]).sort()

const REALM_KEY = "facilityops.realm"
const instances: Partial<Record<Realm, PublicClientApplication>> = {}

function storedRealm(): Realm | null {
  try {
    const value = sessionStorage.getItem(REALM_KEY)
    return value === "resident" || value === "staff" ? value : null
  } catch {
    return null
  }
}

async function instanceFor(realm: Realm): Promise<PublicClientApplication> {
  const config = realmConfigs[realm]
  if (!config) throw new Error(`Sign-in for ${realm}s is not configured`)
  if (!instances[realm]) {
    const msal = new PublicClientApplication({
      auth: {
        clientId: config.clientId,
        authority: config.authority,
        knownAuthorities: config.knownAuthorities,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: "sessionStorage" },
    })
    await msal.initialize()
    instances[realm] = msal
  }
  return instances[realm]
}

/**
 * Finish a redirect sign-in if one is in progress. Returns the account when the user is
 * signed in, or null when the sign-in page should be shown.
 */
export async function completeSignIn(): Promise<AccountInfo | null> {
  const realm = storedRealm()
  if (!realm || !realmConfigs[realm]) return null
  const msal = await instanceFor(realm)
  const result = await msal.handleRedirectPromise()
  const account = result?.account ?? msal.getAllAccounts()[0] ?? null
  if (account) msal.setActiveAccount(account)
  return account
}

/** Go to Microsoft's sign-in page. Residents can also create an account there. */
export async function signIn(realm: Realm): Promise<void> {
  try {
    sessionStorage.setItem(REALM_KEY, realm)
  } catch {
    // Without sessionStorage the redirect can't be completed; MSAL will report it.
  }
  const msal = await instanceFor(realm)
  await msal.loginRedirect({ scopes: [realmConfigs[realm]!.scope] })
}

export async function accessToken(): Promise<string> {
  const realm = storedRealm()
  if (!realm) throw new Error("Not signed in")
  const msal = await instanceFor(realm)
  const account = msal.getActiveAccount()
  if (!account) throw new Error("Not signed in")
  const scopes = [realmConfigs[realm]!.scope]
  try {
    return (await msal.acquireTokenSilent({ scopes, account })).accessToken
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) await msal.acquireTokenRedirect({ scopes, account })
    throw error
  }
}

export async function signOut(): Promise<void> {
  const realm = storedRealm()
  try {
    sessionStorage.removeItem(REALM_KEY)
  } catch {
    // Nothing to clear.
  }
  if (realm && realmConfigs[realm]) await (await instanceFor(realm)).logoutRedirect()
}
