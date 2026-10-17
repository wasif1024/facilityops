import { useMutation, useQuery } from "@connectrpc/connect-query"
import { useState } from "react"
import { AlertTriangle, Loader2 } from "lucide-react"
import { toast } from "sonner"

import { describeError } from "@/api/errors"
import { useInvalidateServices } from "@/api/invalidate"
import {
  categoryLabels,
  dismissalReasonLabels,
  priorityLabels,
  reasonLabel,
  reviewCaseStatusLabels,
  selectableCategories,
} from "@/api/labels"
import { PriorityBadge, StatusBadge } from "@/components/app/badges"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { RevealButton, useReveal } from "@/components/app/redacted-text"
import { TranslatedText } from "@/components/app/translated-text"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Sheet, SheetContent, SheetDescription, SheetFooter, SheetHeader, SheetTitle } from "@/components/ui/sheet"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { IntakeService, MaintenanceCategory } from "@/gen/facilityops/v1/intake_pb"
import {
  DismissalReason,
  type ReviewCase,
  ReviewCaseStatus,
  ReviewQueueService,
  ReviewResolution,
  WorkOrderService,
} from "@/gen/facilityops/v1/operations_pb"

/** Emergencies are handed off before any processing, so no text is stored for them. */
function noTextReason(item: ReviewCase): string {
  return item.status === ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE ||
    item.reasonCode.startsWith("emergency_")
    ? "Emergency reports go straight to the on-call team before any processing, so no text is stored here."
    : "No text was captured."
}

const dismissalReasons = Object.values(DismissalReason).filter(
  (v): v is DismissalReason => typeof v === "number" && v !== DismissalReason.UNSPECIFIED,
)

/** Facility manager: requests the workflow won't decide on its own (frontend spec 6.3). */
export function ReviewQueuePage() {
  const page = usePage()
  const [view, setView] = useFilterParam("view", "open")
  const { data, error, isLoading, refetch } = useQuery(ReviewQueueService.method.listReviewCases, {
    ...page,
    includeResolved: view === "all",
    emergencyOnly: view === "emergencies",
  })
  const [selected, setSelected] = useState<ReviewCase | null>(null)

  return (
    <div>
      <PageHeader
        title="Review queue"
        description="Requests that need a person: emergencies first, then oldest first."
        actions={
          <ToggleGroup type="single" variant="outline" value={view} onValueChange={(v) => v && setView(v)}>
            <ToggleGroupItem value="open">Open</ToggleGroupItem>
            <ToggleGroupItem value="emergencies">Emergencies</ToggleGroupItem>
            <ToggleGroupItem value="all">Include resolved</ToggleGroupItem>
          </ToggleGroup>
        }
      />
      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.cases.length ? (
        <EmptyState title={view === "open" ? "The queue is empty" : "Nothing here"}>
          {view === "open" && "New cases appear here when the workflow needs a decision."}
        </EmptyState>
      ) : (
        <>
          <ul className="space-y-3">
            {data.cases.map((item) => (
              <li key={item.id}>
                <CaseCard item={item} onResolve={() => setSelected(item)} />
              </li>
            ))}
          </ul>
          <Pagination total={data.total} />
        </>
      )}
      <ResolveSheet item={selected} onClose={() => setSelected(null)} />
    </div>
  )
}

function CaseCard({ item, onResolve }: { item: ReviewCase; onResolve: () => void }) {
  const emergency = item.status === ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE
  const resolved = item.status === ReviewCaseStatus.RESOLVED
  return (
    <Card className={emergency ? "border-red-300 dark:border-red-900" : undefined}>
      <CardContent className="flex flex-col gap-3 pt-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="min-w-0 space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-sm">
            <StatusBadge {...reviewCaseStatusLabels[item.status]} />
            <span className="font-medium">{reasonLabel(item.reasonCode)}</span>
            <span className="text-muted-foreground">
              · <TimeAgo value={item.createdAt} />
            </span>
          </div>
          <TranslatedText
            text={item.redactedDescription || noTextReason(item)}
            translated={item.translatedDescription}
            sourceLanguage={item.sourceLanguage}
            className="text-sm"
          />
          {item.suggestedPriority !== undefined && (
            <p className="text-muted-foreground flex flex-wrap items-center gap-2 text-xs">
              AI suggestion: {categoryLabels[item.suggestedCategory]} <PriorityBadge priority={item.suggestedPriority} />
            </p>
          )}
          {resolved && (
            <p className="text-muted-foreground text-xs">
              {item.resolution === ReviewResolution.WORK_ORDER_CREATED
                ? "Resolved: work order created"
                : `Dismissed: ${dismissalReasonLabels[item.dismissalReason]}`}{" "}
              · <TimeAgo value={item.resolvedAt} />
            </p>
          )}
        </div>
        {!resolved && (
          <Button onClick={onResolve} variant={emergency ? "destructive" : "default"} className="shrink-0">
            {emergency && <AlertTriangle aria-hidden />} Resolve
          </Button>
        )}
      </CardContent>
    </Card>
  )
}

function ResolveSheet({ item, onClose }: { item: ReviewCase | null; onClose: () => void }) {
  return (
    <Sheet open={item !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-lg">
        {item && <ResolveForm key={item.id} item={item} onDone={onClose} />}
      </SheetContent>
    </Sheet>
  )
}

function ResolveForm({ item, onDone }: { item: ReviewCase; onDone: () => void }) {
  const invalidate = useInvalidateServices()
  const reveal = useReveal(item.requestId)
  const [resolution, setResolution] = useState<"work_order" | "dismiss">("work_order")
  const [category, setCategory] = useState<MaintenanceCategory>(
    item.suggestedCategory || MaintenanceCategory.OTHER,
  )
  const [priority, setPriority] = useState(item.suggestedPriority ?? 3)
  const [reason, setReason] = useState<DismissalReason>(DismissalReason.UNSPECIFIED)

  const resolve = useMutation(ReviewQueueService.method.resolveReviewCase, {
    onSuccess: async () => {
      toast.success(resolution === "work_order" ? "Work order created" : "Case dismissed")
      await invalidate(ReviewQueueService, IntakeService, WorkOrderService)
      onDone()
    },
    onError: async (error) => {
      const { title, message } = describeError(error)
      toast.error(title, { description: message })
      // e.g. someone else resolved it first: show the current state.
      await invalidate(ReviewQueueService)
    },
  })

  const submit = () =>
    resolve.mutate(
      resolution === "work_order"
        ? { caseId: item.id, resolution: ReviewResolution.WORK_ORDER_CREATED, category, priority }
        : { caseId: item.id, resolution: ReviewResolution.DISMISSED, dismissalReason: reason },
    )

  const ready = resolution === "work_order" || reason !== DismissalReason.UNSPECIFIED

  return (
    <>
      <SheetHeader>
        <SheetTitle>Resolve review case</SheetTitle>
        <SheetDescription>{reasonLabel(item.reasonCode)}</SheetDescription>
      </SheetHeader>
      <div className="space-y-6 px-4">
        <div className="space-y-3 rounded-lg border p-3">
          <TranslatedText
            text={item.redactedDescription || noTextReason(item)}
            translated={item.translatedDescription}
            sourceLanguage={item.sourceLanguage}
            revealed={reveal.values}
            className="text-sm"
          />
          {item.redactedDescription.includes("[PII_") && <RevealButton reveal={reveal} />}
        </div>

        <RadioGroup value={resolution} onValueChange={(v) => setResolution(v as typeof resolution)} className="gap-3">
          <Label className="flex items-center gap-2 font-normal">
            <RadioGroupItem value="work_order" /> Create a work order
          </Label>
          <Label className="flex items-center gap-2 font-normal">
            <RadioGroupItem value="dismiss" /> Dismiss
          </Label>
        </RadioGroup>

        {resolution === "work_order" ? (
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="category">Category</Label>
              <Select value={String(category)} onValueChange={(v) => setCategory(Number(v))}>
                <SelectTrigger id="category">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {selectableCategories.map((c) => (
                    <SelectItem key={c} value={String(c)}>
                      {categoryLabels[c]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-2">
              <Label htmlFor="priority">Priority</Label>
              <Select value={String(priority)} onValueChange={(v) => setPriority(Number(v))}>
                <SelectTrigger id="priority">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {[1, 2, 3, 4, 5].map((p) => (
                    <SelectItem key={p} value={String(p)}>
                      P{p} · {priorityLabels[p].label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <p className="text-muted-foreground text-xs sm:col-span-2">
              Your decision counts as the approval, so the work order starts open.
            </p>
          </div>
        ) : (
          <div className="space-y-2">
            <Label htmlFor="reason">Reason</Label>
            <Select value={String(reason)} onValueChange={(v) => setReason(Number(v))}>
              <SelectTrigger id="reason">
                <SelectValue placeholder="Choose a reason" />
              </SelectTrigger>
              <SelectContent>
                {dismissalReasons.map((r) => (
                  <SelectItem key={r} value={String(r)}>
                    {dismissalReasonLabels[r]}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-muted-foreground text-xs">
              Reasons come from a fixed list so the audit trail never collects personal data.
            </p>
          </div>
        )}
        {item.status === ReviewCaseStatus.EMERGENCY_ON_CALL_QUEUE && (
          <Badge variant="destructive">Emergency already handed to the on-call team</Badge>
        )}
      </div>
      <SheetFooter>
        <Button onClick={submit} disabled={!ready || resolve.isPending}>
          {resolve.isPending && <Loader2 aria-hidden className="animate-spin" />}
          {resolution === "work_order" ? "Create work order" : "Dismiss case"}
        </Button>
      </SheetFooter>
    </>
  )
}
