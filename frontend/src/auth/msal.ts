import {
  type AccountInfo,
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser"

/**
 * Microsoft Entra ID sign-in for production builds (VITE_AUTH_MODE=entra).
 *
 * Tokens stay in MSAL's sessionStorage cache; nothing sensitive goes to localStorage.
 * The SPA needs its own app registration with a redirect URI for this origin and
 * permission to the API scope.
 */
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID ?? ""
const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID ?? ""
const apiScope = import.meta.env.VITE_ENTRA_API_SCOPE ?? ""

let instance: PublicClientApplication | null = null

export function msalInstance(): PublicClientApplication {
  if (!instance) {
    if (!clientId || !tenantId || !apiScope) {
      throw new Error("Set VITE_ENTRA_CLIENT_ID, VITE_ENTRA_TENANT_ID and VITE_ENTRA_API_SCOPE for Entra sign-in")
    }
    instance = new PublicClientApplication({
      auth: {
        clientId,
        authority: `https://login.microsoftonline.com/${tenantId}`,
        redirectUri: window.location.origin,
      },
      cache: { cacheLocation: "sessionStorage" },
    })
  }
  return instance
}

/** Finish a redirect sign-in if one is in progress, or start one. Returns the signed-in account. */
export async function ensureSignedIn(): Promise<AccountInfo | null> {
  const msal = msalInstance()
  await msal.initialize()
  const result = await msal.handleRedirectPromise()
  const account = result?.account ?? msal.getAllAccounts()[0] ?? null
  if (account) {
    msal.setActiveAccount(account)
    return account
  }
  await msal.loginRedirect({ scopes: [apiScope] })
  return null
}

export async function accessToken(): Promise<string> {
  const msal = msalInstance()
  const account = msal.getActiveAccount()
  if (!account) throw new Error("Not signed in")
  try {
    return (await msal.acquireTokenSilent({ scopes: [apiScope], account })).accessToken
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await msal.acquireTokenRedirect({ scopes: [apiScope], account })
    }
    throw error
  }
}

export async function signOut(): Promise<void> {
  await msalInstance().logoutRedirect()
}
