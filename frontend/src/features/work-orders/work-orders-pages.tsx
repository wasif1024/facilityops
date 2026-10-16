import { useMutation, useQuery } from "@connectrpc/connect-query"
import { skipToken } from "@tanstack/react-query"
import { useMemo, useState } from "react"
import { Link, useParams } from "react-router"
import { ArrowLeft, Check, Loader2, UserPlus, X } from "lucide-react"
import { toast } from "sonner"

import { describeError } from "@/api/errors"
import { useInvalidateServices } from "@/api/invalidate"
import { categoryLabels, rejectionReasonLabels, workOrderStatusLabels } from "@/api/labels"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { PriorityBadge, StatusBadge } from "@/components/app/badges"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { RedactedText, RevealButton, useReveal } from "@/components/app/redacted-text"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { IntakeService } from "@/gen/facilityops/v1/intake_pb"
import {
  RejectionReason,
  type WorkOrder,
  WorkOrderService,
  WorkOrderStatus,
} from "@/gen/facilityops/v1/operations_pb"

const tabs = [
  { value: "pending", label: "Pending approval", status: WorkOrderStatus.PENDING_APPROVAL, managerOnly: true },
  { value: "open", label: "Open", status: WorkOrderStatus.OPEN, managerOnly: false },
  { value: "completed", label: "Completed", status: WorkOrderStatus.COMPLETED, managerOnly: false },
  { value: "rejected", label: "Rejected", status: WorkOrderStatus.REJECTED, managerOnly: true },
]

const rejectionReasons = Object.values(RejectionReason).filter(
  (v): v is RejectionReason => typeof v === "number" && v !== RejectionReason.UNSPECIFIED,
)

function excerpt(text: string): string {
  return text.length > 100 ? `${text.slice(0, 100).trimEnd()}…` : text
}

/** Technician names for display and the assign picker (needs work_orders:assign). */
function useTechnicians() {
  const { can } = useSession()
  const { data } = useQuery(WorkOrderService.method.listTechnicians, can(Permission.WorkOrdersAssign) ? {} : skipToken)
  return useMemo(() => data?.technicians ?? [], [data])
}

/**
 * Work orders (frontend spec 6.2, 6.3). Technicians only ever see their own assignments
 * (the backend enforces it); managers see everything and get approval tabs.
 */
export function WorkOrdersPage() {
  const { can } = useSession()
  const manager = can(Permission.WorkOrdersReadAll)
  const visibleTabs = tabs.filter((t) => manager || !t.managerOnly)
  const [tab, setTab] = useFilterParam("tab", manager ? "pending" : "open")
  const status = (visibleTabs.find((t) => t.value === tab) ?? visibleTabs[0]).status
  const page = usePage()
  const { data, error, isLoading, refetch } = useQuery(WorkOrderService.method.listWorkOrders, {
    ...page,
    status,
    assignedToMe: !manager,
  })
  const technicians = useTechnicians()
  const nameOf = (id: string) => technicians.find((t) => t.userId === id)?.displayName

  return (
    <div>
      <PageHeader
        title={manager ? "Work orders" : "My work orders"}
        description={manager ? "Approve, reject and assign work." : "Work assigned to you, most urgent first."}
      />
      <Tabs value={tab} onValueChange={setTab} className="mb-4">
        <TabsList>
          {visibleTabs.map((t) => (
            <TabsTrigger key={t.value} value={t.value}>
              {t.label}
            </TabsTrigger>
          ))}
        </TabsList>
      </Tabs>
      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.workOrders.length ? (
        <EmptyState title="No work orders here" />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Priority</TableHead>
                <TableHead>Work</TableHead>
                <TableHead className="hidden sm:table-cell">Category</TableHead>
                {manager && <TableHead className="hidden md:table-cell">Assigned to</TableHead>}
                <TableHead className="hidden sm:table-cell">Created</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.workOrders.map((wo) => (
                <TableRow key={wo.id}>
                  <TableCell>
                    <PriorityBadge priority={wo.priority} />
                  </TableCell>
                  <TableCell className="max-w-md">
                    <Link to={`/work-orders/${wo.id}`} className="hover:underline">
                      <RedactedText text={excerpt(wo.redactedDescription) || "(no text)"} />
                    </Link>
                  </TableCell>
                  <TableCell className="hidden sm:table-cell">{categoryLabels[wo.category]}</TableCell>
                  {manager && (
                    <TableCell className="hidden md:table-cell">
                      {wo.assignedTechnicianId ? nameOf(wo.assignedTechnicianId) ?? "A technician" : (
                        <span className="text-muted-foreground">Unassigned</span>
                      )}
                    </TableCell>
                  )}
                  <TableCell className="hidden sm:table-cell">
                    <TimeAgo value={wo.createdAt} />
                  </TableCell>
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

export function WorkOrderDetailPage() {
  const { workOrderId = "" } = useParams()
  const { session, can } = useSession()
  const { data, error, isLoading, refetch } = useQuery(WorkOrderService.method.getWorkOrder, { workOrderId })
  const technicians = useTechnicians()
  const wo = data?.workOrder
  const reveal = useReveal(wo?.requestId ?? "")

  const mine = wo?.assignedTechnicianId === session?.userId
  const canReveal = can(Permission.PiiRevealAll) || (can(Permission.PiiRevealAssigned) && mine)

  return (
    <div className="mx-auto max-w-3xl">
      <Button variant="ghost" size="sm" asChild className="mb-4">
        <Link to="/work-orders">
          <ArrowLeft aria-hidden /> Back
        </Link>
      </Button>
      {isLoading ? (
        <ListSkeleton rows={3} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : wo ? (
        <div className="space-y-4">
          <PageHeader
            title={`${categoryLabels[wo.category]} work order`}
            description={<>Created <TimeAgo value={wo.createdAt} /></>}
            actions={<WorkOrderActions workOrder={wo} mine={mine} />}
          />
          <Card>
            <CardHeader>
              <CardTitle className="flex flex-wrap items-center gap-2 text-base">
                <StatusBadge {...workOrderStatusLabels[wo.status]} />
                <PriorityBadge priority={wo.priority} />
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <RedactedText
                text={wo.redactedDescription || "No description."}
                revealed={reveal.values}
                className="leading-relaxed whitespace-pre-wrap"
              />
              {canReveal && wo.redactedDescription.includes("[PII_") && <RevealButton reveal={reveal} />}
              <dl className="text-muted-foreground grid gap-2 text-sm sm:grid-cols-2">
                <div>
                  <dt className="inline font-medium">Assigned to: </dt>
                  <dd className="inline">
                    {wo.assignedTechnicianId
                      ? mine
                        ? "You"
                        : technicians.find((t) => t.userId === wo.assignedTechnicianId)?.displayName ?? "A technician"
                      : "Nobody yet"}
                  </dd>
                </div>
                {wo.rejectionReason !== RejectionReason.UNSPECIFIED && (
                  <div>
                    <dt className="inline font-medium">Rejected: </dt>
                    <dd className="inline">{rejectionReasonLabels[wo.rejectionReason]}</dd>
                  </div>
                )}
                {wo.completedAt && (
                  <div>
                    <dt className="inline font-medium">Completed: </dt>
                    <dd className="inline">
                      <TimeAgo value={wo.completedAt} />
                    </dd>
                  </div>
                )}
              </dl>
            </CardContent>
          </Card>
        </div>
      ) : null}
    </div>
  )
}

function useWorkOrderMutation<M extends Parameters<typeof useMutation>[0]>(method: M, success: string) {
  const invalidate = useInvalidateServices()
  return useMutation(method, {
    onSuccess: async () => {
      toast.success(success)
      await invalidate(WorkOrderService, IntakeService)
    },
    onError: (error) => {
      const { title, message } = describeError(error)
      toast.error(title, { description: message })
    },
  })
}

function WorkOrderActions({ workOrder, mine }: { workOrder: WorkOrder; mine: boolean }) {
  const { can } = useSession()
  const approve = useWorkOrderMutation(WorkOrderService.method.approveWorkOrder, "Work order approved")
  const complete = useWorkOrderMutation(WorkOrderService.method.completeWorkOrder, "Marked as completed")
  const pending = workOrder.status === WorkOrderStatus.PENDING_APPROVAL
  const open = workOrder.status === WorkOrderStatus.OPEN
  const id = { workOrderId: workOrder.id }

  return (
    <>
      {pending && can(Permission.WorkOrdersApprove) && (
        <>
          <Button onClick={() => approve.mutate(id)} disabled={approve.isPending}>
            {approve.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />} Approve
          </Button>
          <RejectDialog workOrder={workOrder} />
        </>
      )}
      {(pending || open) && can(Permission.WorkOrdersAssign) && <AssignDialog workOrder={workOrder} />}
      {open && mine && can(Permission.WorkOrdersUpdateAssigned) && (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button disabled={complete.isPending}>
              <Check aria-hidden /> Mark as completed
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Mark this work order as completed?</AlertDialogTitle>
              <AlertDialogDescription>The tenant will see it as done. This can't be undone.</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={() => complete.mutate(id)}>Mark completed</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </>
  )
}

function RejectDialog({ workOrder }: { workOrder: WorkOrder }) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState(RejectionReason.UNSPECIFIED)
  const reject = useWorkOrderMutation(WorkOrderService.method.rejectWorkOrder, "Work order rejected")
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <X aria-hidden /> Reject
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject work order</DialogTitle>
            <DialogDescription>The request will be closed. Choose why.</DialogDescription>
          </DialogHeader>
          <Select value={String(reason)} onValueChange={(v) => setReason(Number(v))}>
            <SelectTrigger aria-label="Reason">
              <SelectValue placeholder="Choose a reason" />
            </SelectTrigger>
            <SelectContent>
              {rejectionReasons.map((r) => (
                <SelectItem key={r} value={String(r)}>
                  {rejectionReasonLabels[r]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button
              variant="destructive"
              disabled={reason === RejectionReason.UNSPECIFIED || reject.isPending}
              onClick={() => reject.mutate({ workOrderId: workOrder.id, reason }, { onSuccess: () => setOpen(false) })}
            >
              Reject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}

function AssignDialog({ workOrder }: { workOrder: WorkOrder }) {
  const [open, setOpen] = useState(false)
  const technicians = useTechnicians()
  const [technicianId, setTechnicianId] = useState(workOrder.assignedTechnicianId)
  const assign = useWorkOrderMutation(WorkOrderService.method.assignWorkOrder, "Technician assigned")
  return (
    <>
      <Button variant="outline" onClick={() => setOpen(true)}>
        <UserPlus aria-hidden /> {workOrder.assignedTechnicianId ? "Reassign" : "Assign"}
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Assign a technician</DialogTitle>
            <DialogDescription>Open work orders per technician help you balance the load.</DialogDescription>
          </DialogHeader>
          {technicians.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No active technicians yet. An administrator can grant the technician role.
            </p>
          ) : (
            <div className="space-y-2">
              <Label htmlFor="technician">Technician</Label>
              <Select value={technicianId} onValueChange={setTechnicianId}>
                <SelectTrigger id="technician">
                  <SelectValue placeholder="Choose a technician" />
                </SelectTrigger>
                <SelectContent>
                  {technicians.map((t) => (
                    <SelectItem key={t.userId} value={t.userId}>
                      {t.displayName} · {t.openWorkOrders} open
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <DialogFooter>
            <Button
              disabled={!technicianId || assign.isPending}
              onClick={() =>
                assign.mutate(
                  { workOrderId: workOrder.id, technicianUserId: technicianId },
                  { onSuccess: () => setOpen(false) },
                )
              }
            >
              Assign
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
