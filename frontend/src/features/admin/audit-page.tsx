import type { JsonValue } from "@bufbuild/protobuf"
import { useQuery } from "@connectrpc/connect-query"

import { humanize, roleLabel } from "@/api/labels"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { type AuditEvent, UserAdminService } from "@/gen/facilityops/v1/users_pb"
import { shortId } from "@/lib/format"

const actions = [
  { value: "all", label: "All actions" },
  { value: "user.provisioned", label: "First sign-in" },
  { value: "user.created", label: "User added" },
  { value: "user.updated", label: "User updated" },
  { value: "user.roles_changed", label: "Roles changed" },
  { value: "user.role_granted_by_cli", label: "Role granted (CLI)" },
  { value: "pii.revealed", label: "Personal details revealed" },
]

const actionLabel = (action: string) => actions.find((a) => a.value === action)?.label ?? humanize(action)

/** Audit details are structured, non-personal data; render them as readable phrases. */
function describeDetails(event: AuditEvent): string {
  // protobuf-es maps google.protobuf.Struct to a plain JSON object.
  const details: Record<string, JsonValue> = event.details ?? {}
  const parts: string[] = []
  for (const [key, value] of Object.entries(details)) {
    if (Array.isArray(value)) {
      if (value.length === 0) continue
      const items = value.map((v) => (key.includes("role") || key === "added" || key === "removed" ? roleLabel(String(v)) : String(v)))
      parts.push(`${humanize(key)}: ${items.join(", ")}`)
    } else if (value && typeof value === "object") {
      const change = value as Record<string, JsonValue>
      parts.push("from" in change ? `${humanize(key)}: ${change.from} → ${change.to}` : humanize(key))
    } else if (value !== null && value !== true) {
      parts.push(`${humanize(key)}: ${key.includes("role") ? roleLabel(String(value)) : String(value)}`)
    } else if (value === true) {
      parts.push(`${humanize(key)} changed`)
    }
  }
  return parts.join(" · ") || "—"
}

/** Who changed access or viewed personal data, newest first (frontend spec 6.4). */
export function AuditPage() {
  const page = usePage()
  const [action, setAction] = useFilterParam("action", "all")
  const { data, error, isLoading, refetch } = useQuery(UserAdminService.method.listAuditEvents, {
    ...page,
    action: action === "all" ? "" : action,
  })

  return (
    <div>
      <PageHeader
        title="Audit log"
        description="Access changes and personal-data reveals. Entries never contain personal data."
        actions={
          <Select value={action} onValueChange={setAction}>
            <SelectTrigger className="w-56" aria-label="Filter by action">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {actions.map((a) => (
                <SelectItem key={a.value} value={a.value}>
                  {a.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        }
      />
      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.events.length ? (
        <EmptyState title="No audit events" />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Action</TableHead>
                <TableHead className="hidden md:table-cell">Actor</TableHead>
                <TableHead className="hidden md:table-cell">Target</TableHead>
                <TableHead>Details</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.events.map((event) => (
                <TableRow key={event.id}>
                  <TableCell>
                    <TimeAgo value={event.createdAt} />
                  </TableCell>
                  <TableCell className="font-medium">{actionLabel(event.action)}</TableCell>
                  <TableCell className="hidden font-mono text-xs md:table-cell">
                    {event.actorUserId ? shortId(event.actorUserId) : "system"}
                  </TableCell>
                  <TableCell className="hidden font-mono text-xs md:table-cell">
                    {humanize(event.targetType)} {shortId(event.targetId)}
                  </TableCell>
                  <TableCell className="min-w-48 text-sm whitespace-normal">{describeDetails(event)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <Pagination total={data.total} />
        </>
      )}
    </div>
  )
}
