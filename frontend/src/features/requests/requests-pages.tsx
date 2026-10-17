import { useQuery } from "@connectrpc/connect-query"
import { Link, useParams } from "react-router"
import { ArrowLeft } from "lucide-react"

import {
  categoryLabels,
  intakeStatusLabel,
  reasonLabel,
  sourceLabels,
  workOrderStatusFromName,
} from "@/api/labels"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { PriorityBadge, StatusBadge } from "@/components/app/badges"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { RedactedText, RevealButton, useReveal } from "@/components/app/redacted-text"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { type IntakeRequestSummary, IntakeService, IntakeStatus } from "@/gen/facilityops/v1/intake_pb"

type Audience = "tenant" | "staff"

/** Emergencies are handed off before any processing, so they have no stored text. */
function describeRequest(request: IntakeRequestSummary): string {
  if (request.redactedDescription) return excerpt(request.redactedDescription)
  if (request.status === IntakeStatus.EMERGENCY_HANDOFF) return "Emergency report, sent straight to the on-call team"
  return `${sourceLabels[request.source]} request`
}

function excerpt(text: string, length = 110): string {
  return text.length > length ? `${text.slice(0, length).trimEnd()}…` : text
}

function RequestStatus({ request, audience }: { request: IntakeRequestSummary; audience: Audience }) {
  const status = intakeStatusLabel(request.status, audience)
  const workOrder = workOrderStatusFromName(request.workOrderStatus)
  return (
    <div className="flex flex-wrap gap-1">
      <StatusBadge {...status} />
      {workOrder && request.status !== IntakeStatus.DISMISSED && <StatusBadge label={`Work order: ${workOrder.label}`} tone={workOrder.tone} />}
    </div>
  )
}

function RequestList({ requests, audience }: { requests: IntakeRequestSummary[]; audience: Audience }) {
  const detail = (r: IntakeRequestSummary) => `/requests/${r.requestId}`
  return (
    <>
      {/* Cards on phones: tenants mostly report and check from a phone. */}
      <ul className="space-y-3 md:hidden">
        {requests.map((request) => (
          <li key={request.requestId}>
            <Link to={detail(request)} className="block">
              <Card className="hover:bg-accent/40 transition-colors">
                <CardContent className="space-y-2 pt-4 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <RequestStatus request={request} audience={audience} />
                    <TimeAgo value={request.createdAt} />
                  </div>
                  <RedactedText text={describeRequest(request)} />
                </CardContent>
              </Card>
            </Link>
          </li>
        ))}
      </ul>
      <Table className="hidden md:table">
        <TableHeader>
          <TableRow>
            <TableHead>Submitted</TableHead>
            <TableHead>Request</TableHead>
            <TableHead>Category</TableHead>
            <TableHead>Priority</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {requests.map((request) => (
            <TableRow key={request.requestId}>
              <TableCell>
                <TimeAgo value={request.createdAt} />
              </TableCell>
              <TableCell className="max-w-md min-w-48 whitespace-normal">
                <Link to={detail(request)} className="hover:underline">
                  <RedactedText text={describeRequest(request)} />
                </Link>
              </TableCell>
              <TableCell>{categoryLabels[request.category]}</TableCell>
              <TableCell>
                <PriorityBadge priority={request.priority} />
              </TableCell>
              <TableCell>
                <RequestStatus request={request} audience={audience} />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </>
  )
}

/** Tenant: their own requests (frontend spec 6.1). */
export function MyRequestsPage() {
  const page = usePage()
  const { data, error, isLoading, refetch } = useQuery(IntakeService.method.listMyRequests, page)
  return (
    <div>
      <PageHeader
        title="My requests"
        description="Everything you've reported and where it stands."
        actions={
          <Button asChild>
            <Link to="/report">Report a problem</Link>
          </Button>
        }
      />
      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.requests.length ? (
        <EmptyState title="You haven't reported anything yet">
          <Link to="/report" className="underline">
            Report a problem
          </Link>{" "}
          and it will show up here.
        </EmptyState>
      ) : (
        <>
          <RequestList requests={data.requests} audience="tenant" />
          <Pagination total={data.total} />
        </>
      )}
    </div>
  )
}

const statusFilters: { value: string; label: string; status?: IntakeStatus }[] = [
  { value: "all", label: "All statuses" },
  { value: "review", label: "Human review", status: IntakeStatus.HUMAN_REVIEW },
  { value: "approval", label: "Pending approval", status: IntakeStatus.PENDING_APPROVAL },
  { value: "work_order", label: "Work order", status: IntakeStatus.WORK_ORDER_CREATED },
  { value: "emergency", label: "Emergency handoff", status: IntakeStatus.EMERGENCY_HANDOFF },
  { value: "dismissed", label: "Dismissed", status: IntakeStatus.DISMISSED },
]

/** Facility manager: every request (frontend spec 6.3). */
export function AllRequestsPage() {
  const page = usePage()
  const [filter, setFilter] = useFilterParam("status", "all")
  const status = statusFilters.find((f) => f.value === filter)?.status
  const { data, error, isLoading, refetch } = useQuery(IntakeService.method.listRequests, { ...page, status })
  return (
    <div>
      <PageHeader
        title="All requests"
        description="Every request and how the workflow routed it."
        actions={
          <Select value={filter} onValueChange={setFilter}>
            <SelectTrigger className="w-48" aria-label="Filter by status">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {statusFilters.map((f) => (
                <SelectItem key={f.value} value={f.value}>
                  {f.label}
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
      ) : !data?.requests.length ? (
        <EmptyState title="No requests match this filter" />
      ) : (
        <>
          <RequestList requests={data.requests} audience="staff" />
          <Pagination total={data.total} />
        </>
      )}
    </div>
  )
}

/** One request with its routing timeline; staff may reveal personal details (spec 6.1, 6.3, 7). */
export function RequestDetailPage() {
  const { requestId = "" } = useParams()
  const { can, canAny } = useSession()
  const audience: Audience = can(Permission.IntakeReadAll) ? "staff" : "tenant"
  const { data, error, isLoading, refetch } = useQuery(IntakeService.method.getRequest, { requestId })
  const reveal = useReveal(requestId)
  const request = data?.request

  return (
    <div className="mx-auto max-w-3xl">
      <Button variant="ghost" size="sm" asChild className="mb-4">
        <Link to={audience === "staff" ? "/requests" : "/requests/mine"}>
          <ArrowLeft aria-hidden /> Back
        </Link>
      </Button>
      {isLoading ? (
        <ListSkeleton rows={3} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : request ? (
        <div className="space-y-4">
          <PageHeader title="Request" description={<TimeAgo value={request.createdAt} />} />
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                <RequestStatus request={request} audience={audience} />
                <span className="text-muted-foreground text-sm font-normal">via {sourceLabels[request.source]}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <RedactedText
                text={
                  request.redactedDescription ||
                  (request.status === IntakeStatus.EMERGENCY_HANDOFF
                    ? "Emergency reports go straight to the on-call team before any processing, so no text is stored."
                    : "No text was captured.")
                }
                revealed={reveal.values}
                className="leading-relaxed whitespace-pre-wrap"
              />
              {canAny(Permission.PiiRevealAll) && request.redactedDescription.includes("[PII_") && (
                <RevealButton reveal={reveal} />
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle className="text-base">What happened</CardTitle>
            </CardHeader>
            <CardContent>
              <ol className="space-y-3 border-l pl-4 text-sm">
                <li>Submitted <TimeAgo value={request.createdAt} /></li>
                <li>
                  {intakeStatusLabel(request.status, audience).label}
                  {audience === "staff" && request.reasonCode && (
                    <span className="text-muted-foreground"> · {reasonLabel(request.reasonCode)}</span>
                  )}
                </li>
                {request.priority !== undefined && (
                  <li className="flex flex-wrap items-center gap-2">
                    Classified as {categoryLabels[request.category]} <PriorityBadge priority={request.priority} />
                  </li>
                )}
                {request.workOrderStatus && (
                  <li>
                    Work order {workOrderStatusFromName(request.workOrderStatus)?.label.toLowerCase()}
                    {audience === "staff" && request.workOrderId && (
                      <>
                        {" · "}
                        <Link to={`/work-orders/${request.workOrderId}`} className="underline">
                          open work order
                        </Link>
                      </>
                    )}
                  </li>
                )}
              </ol>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  )
}
