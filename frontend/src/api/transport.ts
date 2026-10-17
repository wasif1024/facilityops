import type { Interceptor } from "@connectrpc/connect"
import { createGrpcWebTransport } from "@connectrpc/connect-web"

export const apiBaseUrl: string = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000"

/**
 * The single transport for the app: gRPC-Web, the browser form of gRPC.
 *
 * Browsers can't speak native gRPC (they can't read HTTP/2 trailers), so gRPC-Web
 * carries the trailers in the response body instead. The backend's Connect services
 * serve gRPC-Web directly, with no Envoy proxy in between. Messages are binary protobuf,
 * which also keeps photo and voice uploads small. Identity headers come from a callback,
 * so switching user never rebuilds the transport.
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

  return createGrpcWebTransport({
    baseUrl: apiBaseUrl,
    useBinaryFormat: true,
    interceptors: [identity],
  })
}
