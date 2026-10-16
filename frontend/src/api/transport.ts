import type { Interceptor } from "@connectrpc/connect"
import { createConnectTransport } from "@connectrpc/connect-web"

export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

/**
 * The single Connect transport for the app.
 *
 * Binary protobuf keeps photo and voice uploads small (JSON would base64 them).
 * Identity headers come from a callback, so switching user never rebuilds the transport.
 */
export function createApiTransport(getAuthHeaders: () => Promise<Record<string, string>>) {
  const identity: Interceptor = (next) => async (request) => {
    for (const [name, value] of Object.entries(await getAuthHeaders())) {
      request.header.set(name, value)
    }
    // Lets a support engineer find this call in the backend traces.
    request.header.set("X-Request-ID", crypto.randomUUID())
    return next(request)
  }

  return createConnectTransport({
    baseUrl: apiBaseUrl,
    useBinaryFormat: true,
    interceptors: [identity],
  })
}
