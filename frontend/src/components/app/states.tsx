import type { ReactNode } from "react"
import { Inbox, RefreshCw, ShieldOff } from "lucide-react"

import { describeError } from "@/api/errors"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: ReactNode
  actions?: ReactNode
}) {
  return (
    <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
      <div className="space-y-1">
        <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
        {description && <p className="text-muted-foreground max-w-prose text-sm">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  )
}

/** Every list has an explicit empty state: one sentence and, where it helps, the next action. */
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed p-10 text-center">
      <Inbox aria-hidden className="text-muted-foreground size-8" />
      <p className="font-medium">{title}</p>
      {children && <div className="text-muted-foreground text-sm">{children}</div>}
    </div>
  )
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const { title, message, retryable } = describeError(error)
  return (
    <Alert variant="destructive">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription className="flex flex-wrap items-center justify-between gap-3">
        <span>{message}</span>
        {onRetry && retryable && (
          <Button size="sm" variant="outline" onClick={onRetry}>
            <RefreshCw aria-hidden /> Try again
          </Button>
        )}
      </AlertDescription>
    </Alert>
  )
}

export function ListSkeleton({ rows = 5 }: { rows?: number }) {
  return (
    <div className="space-y-2" aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className="h-12 w-full" />
      ))}
    </div>
  )
}

export function NoAccess() {
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-3 py-20 text-center">
      <ShieldOff aria-hidden className="text-muted-foreground size-10" />
      <h1 className="text-xl font-semibold">You don't have access to this page</h1>
      <p className="text-muted-foreground text-sm">
        Your roles don't include the permission it needs. Ask an administrator if you should have it.
      </p>
    </div>
  )
}

export function NotFound() {
  return (
    <div className="mx-auto max-w-md py-20 text-center">
      <h1 className="text-xl font-semibold">Page not found</h1>
    </div>
  )
}
