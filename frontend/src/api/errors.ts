import { Code, ConnectError } from "@connectrpc/connect"

export interface ErrorDescription {
  title: string
  message: string
  code: Code | undefined
  /** Retrying the same request might work. */
  retryable: boolean
}

/**
 * One place that turns any failure into words for people (frontend spec section 9.1).
 * Backend messages are free of personal data by design, so they are safe to show.
 */
export function describeError(error: unknown): ErrorDescription {
  const err = ConnectError.from(error)
  switch (err.code) {
    case Code.Unauthenticated:
      return { title: "Please sign in again", message: "Your session has ended.", code: err.code, retryable: false }
    case Code.PermissionDenied:
      return {
        title: "You don't have access to this",
        message: err.rawMessage || "Ask an administrator if you need it.",
        code: err.code,
        retryable: false,
      }
    case Code.InvalidArgument:
    case Code.FailedPrecondition:
    case Code.AlreadyExists:
    case Code.NotFound:
    case Code.Aborted:
      return { title: "That didn't work", message: err.rawMessage, code: err.code, retryable: false }
    case Code.ResourceExhausted:
      return {
        title: "Too many requests",
        message: "Please wait a minute and try again.",
        code: err.code,
        retryable: true,
      }
    default:
      return {
        title: "Something went wrong on our side",
        message: "Please try again. If it keeps happening, contact support.",
        code: err.code,
        retryable: true,
      }
  }
}

export function isPermissionDenied(error: unknown): boolean {
  return error instanceof ConnectError && error.code === Code.PermissionDenied
}
