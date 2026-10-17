import { useMutation, useQuery } from "@connectrpc/connect-query"
import { skipToken } from "@tanstack/react-query"
import { useState } from "react"
import { Bot, Check, CircleHelp, Loader2, PencilLine, ShieldAlert, X } from "lucide-react"
import { toast } from "sonner"

import { describeError } from "@/api/errors"
import { useInvalidateServices } from "@/api/invalidate"
import {
  actionTypeLabels,
  executedReasonLabels,
  executedStatusLabels,
  humanize,
  incidentInProgress,
  incidentStatusLabels,
  planFlagLabels,
  planRejectionReasons,
  reasonLabel,
} from "@/api/labels"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { StatusBadge } from "@/components/app/badges"
import { TimeAgo } from "@/components/app/time"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Checkbox } from "@/components/ui/checkbox"
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
import { Textarea } from "@/components/ui/textarea"
import {
  type ExecutedAction,
  type Incident,
  IncidentService,
  IncidentStatus,
  PlanDecision,
  type PlanVersion,
} from "@/gen/facilityops/v1/incidents_pb"
import { IntakeService, type TenantNotice } from "@/gen/facilityops/v1/intake_pb"
import { WorkOrderService } from "@/gen/facilityops/v1/operations_pb"
import { NoticeList } from "@/features/incidents/tenant-notices"
import { Input } from "@/components/ui/input"

/** While the team works, check back every few seconds; the worker runs off the request path. */
const POLL_MS = 4000

const money = (value: number | undefined) =>
  value === undefined ? "—" : value.toLocaleString(undefined, { maximumFractionDigits: 2 })

/**
 * The incident team's work on one request (FR-23 to FR-25): the latest plan with its evidence,
 * flags and proposed actions, the facility manager's decision, and what the application did
 * after approval. Needs incidents:read; deciding and escalating need incidents:manage.
 */
export function IncidentPanel({ requestId, canEscalate = true }: { requestId: string; canEscalate?: boolean }) {
  const { can } = useSession()
  const readable = can(Permission.IncidentsRead)
  const list = useQuery(IncidentService.method.listIncidents, readable ? { requestId, limit: 1 } : skipToken)
  const incident = list.data?.incidents[0]
  const detail = useQuery(
    IncidentService.method.getIncident,
    incident ? { incidentId: incident.id } : skipToken,
    { refetchInterval: (q) => (q.state.data && incidentInProgress.has(q.state.data.incident?.status ?? 0) ? POLL_MS : false) },
  )

  if (!readable || list.isLoading) return null
  if (!incident) {
    return canEscalate && can(Permission.IncidentsManage) ? <EscalateCard requestId={requestId} /> : null
  }
  const current = detail.data?.incident ?? incident
  const [plan, ...older] = detail.data?.plans ?? []
  const actions = detail.data?.actions ?? []
  const notices = detail.data?.notices ?? []

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          <Bot aria-hidden className="size-4" /> Incident team
          <StatusBadge {...incidentStatusLabels[current.status]} />
        </CardTitle>
        <p className="text-muted-foreground text-sm">
          {reasonLabel(current.escalationReason)}
          {current.reasonCode && <> · {reasonLabel(current.reasonCode)}</>} · <TimeAgo value={current.createdAt} />
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        {incidentInProgress.has(current.status) && (
          <p className="text-muted-foreground flex items-center gap-2 text-sm" role="status">
            <Loader2 aria-hidden className="size-4 animate-spin" />
            The AI specialists are looking up the asset, manuals, safety rules, parts and technicians. This takes a
            minute or two.
          </p>
        )}
        {current.status === IncidentStatus.NEEDS_REVIEW && (
          <p className="text-sm">The team couldn't finish, so this incident needs a person. Handle the work order as usual.</p>
        )}
        {plan && <PlanView incident={current} plan={plan} executed={actions.filter((a) => a.planId === plan.id)} />}
        {plan && current.status === IncidentStatus.APPROVED && (
          <NoticeSection
            incident={current}
            plan={plan}
            notices={notices}
            language={detail.data?.tenantLanguage ?? "en"}
          />
        )}
        {older.length > 0 && (
          <p className="text-muted-foreground text-xs">
            Earlier versions:{" "}
            {older.map((p) => `v${p.version} ${humanize(p.status).toLowerCase()}${p.feedback ? ` ("${p.feedback}")` : ""}`).join("; ")}
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function PlanView({ incident, plan, executed }: { incident: Incident; plan: PlanVersion; executed: ExecutedAction[] }) {
  const { can } = useSession()
  const decidable = incident.status === IncidentStatus.PLAN_READY && plan.status === "proposed"
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="font-medium">Plan v{plan.version}</span>
        <StatusBadge label={humanize(plan.status)} tone={plan.status === "approved" ? "success" : "neutral"} />
        {plan.flags.map((f) => (
          <StatusBadge key={f} {...(planFlagLabels[f] ?? { label: humanize(f), tone: "neutral" })} />
        ))}
        <span className="text-muted-foreground">Estimated cost {money(plan.estimatedTotalCost)}</span>
      </div>
      <p className="leading-relaxed">{plan.summary}</p>
      {plan.safetyBlock && (
        <Alert variant="destructive">
          <ShieldAlert aria-hidden />
          <AlertTitle>Work is blocked for safety</AlertTitle>
          <AlertDescription>{plan.safetyNotes || "The safety specialist blocked this work."}</AlertDescription>
        </Alert>
      )}

      <section aria-label="Evidence">
        <h3 className="mb-2 text-sm font-medium">Evidence</h3>
        <ul className="space-y-2 text-sm">
          {plan.claims.map((claim, i) => (
            <li key={i} className="border-l-2 pl-3">
              <p>{claim.statement}</p>
              <p className="text-muted-foreground text-xs">
                {humanize(claim.specialist)} ·{" "}
                {claim.verified ? (
                  claim.citations.map((c) => `${c.sourceId}${c.section ? ` (${c.section})` : ""}`).join(", ")
                ) : (
                  <span className="text-amber-700 dark:text-amber-300">Unverified: no source cited</span>
                )}
              </p>
            </li>
          ))}
        </ul>
      </section>

      <section aria-label="Proposed actions">
        <h3 className="mb-2 text-sm font-medium">Proposed actions</h3>
        <ul className="space-y-2 text-sm">
          {plan.actions.map((action, i) => {
            const result = executed.find((e) => e.position === i)
            const target = action.technicianId || (action.partSku && `${action.partSku} × ${action.quantity ?? 1}`)
            return (
              <li key={i} className="flex flex-col gap-1 rounded-md border p-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <p className="font-medium">
                    {actionTypeLabels[action.type] ?? humanize(action.type)}
                    {target && <span className="text-muted-foreground font-normal"> · {target}</span>}
                  </p>
                  <p className="text-muted-foreground">{action.description}</p>
                </div>
                <div className="flex shrink-0 flex-col items-start gap-1 sm:items-end">
                  <span className="text-muted-foreground text-xs">{money(result?.cost ?? action.estimatedCost)}</span>
                  {result && (
                    <StatusBadge
                      {...(executedStatusLabels[result.status] ?? { label: humanize(result.status), tone: "neutral" })}
                    />
                  )}
                  {result?.reasonCode && (
                    <span className="text-muted-foreground text-xs">
                      {executedReasonLabels[result.reasonCode] ?? humanize(result.reasonCode)}
                    </span>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      </section>

      {plan.openQuestions.length > 0 && (
        <section aria-label="Open questions">
          <h3 className="mb-2 flex items-center gap-1 text-sm font-medium">
            <CircleHelp aria-hidden className="size-4" /> Open questions
          </h3>
          <ul className="list-disc space-y-1 pl-5 text-sm">
            {plan.openQuestions.map((q, i) => (
              <li key={i}>{q}</li>
            ))}
          </ul>
        </section>
      )}

      {plan.feedback && <p className="text-muted-foreground text-sm">Revision asked for: “{plan.feedback}”</p>}
      {plan.decisionReason && (
        <p className="text-muted-foreground text-sm">Rejected: {reasonLabel(plan.decisionReason)}</p>
      )}
      {decidable && can(Permission.IncidentsManage) && <DecisionBar incident={incident} plan={plan} />}
    </div>
  )
}

function useIncidentMutation<M extends Parameters<typeof useMutation>[0]>(method: M, success: string) {
  const invalidate = useInvalidateServices()
  return useMutation(method, {
    onSuccess: async () => {
      toast.success(success)
      await invalidate(IncidentService, WorkOrderService, IntakeService)
    },
    onError: (error) => {
      const { title, message } = describeError(error)
      toast.error(title, { description: message })
    },
  })
}

type Dialogs = "approve" | "revise" | "reject" | null

/** The human decision (FR-24). The backend enforces the same gate; the buttons only explain it. */
function DecisionBar({ incident, plan }: { incident: Incident; plan: PlanVersion }) {
  const [open, setOpen] = useState<Dialogs>(null)
  const [confirmCost, setConfirmCost] = useState(false)
  const [feedback, setFeedback] = useState("")
  const [reason, setReason] = useState("")
  const decide = useIncidentMutation(IncidentService.method.decideIncidentPlan, "Decision recorded")
  const highCost = plan.flags.includes("high_cost")
  const base = { incidentId: incident.id, planId: plan.id }
  const close = () => setOpen(null)

  return (
    <div className="space-y-2 border-t pt-4">
      <div className="flex flex-wrap gap-2">
        <Button onClick={() => setOpen("approve")} disabled={plan.safetyBlock || decide.isPending}>
          <Check aria-hidden /> Approve plan
        </Button>
        <Button variant="outline" onClick={() => setOpen("revise")} disabled={decide.isPending}>
          <PencilLine aria-hidden /> Ask for a revision
        </Button>
        <Button variant="outline" onClick={() => setOpen("reject")} disabled={decide.isPending}>
          <X aria-hidden /> Reject
        </Button>
      </div>
      {plan.safetyBlock && (
        <p className="text-muted-foreground text-xs">
          A plan with a safety block can't be approved. Clear the cause (for example renew the permit), then ask for a
          revision.
        </p>
      )}

      <Dialog open={open === "approve"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Approve this plan?</DialogTitle>
            <DialogDescription>
              The application then carries out the actions: it approves the work order, assigns the named technician,
              raises purchase and visit requests, and records lockout. Nothing is sent to vendors automatically.
            </DialogDescription>
          </DialogHeader>
          {highCost && (
            <div className="flex items-start gap-2">
              <Checkbox id="confirm-cost" checked={confirmCost} onCheckedChange={(v) => setConfirmCost(v === true)} />
              <Label htmlFor="confirm-cost" className="leading-snug font-normal">
                I confirm the estimated cost of {money(plan.estimatedTotalCost)}, which is above the approval threshold.
              </Label>
            </div>
          )}
          <DialogFooter>
            <Button
              disabled={(highCost && !confirmCost) || decide.isPending}
              onClick={() =>
                decide.mutate(
                  { ...base, decision: PlanDecision.APPROVE, confirmHighCost: highCost && confirmCost },
                  { onSuccess: close },
                )
              }
            >
              {decide.isPending && <Loader2 aria-hidden className="animate-spin" />} Approve
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={open === "revise"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Ask for a revision</DialogTitle>
            <DialogDescription>
              The team investigates again with your note and its previous plan. Don't include personal details.
            </DialogDescription>
          </DialogHeader>
          <Label htmlFor="feedback">What should change?</Label>
          <Textarea
            id="feedback"
            value={feedback}
            maxLength={2000}
            onChange={(e) => setFeedback(e.target.value)}
            placeholder="For example: the gas permit was renewed today (P-90); plan the dispatch."
          />
          <DialogFooter>
            <Button
              disabled={!feedback.trim() || decide.isPending}
              onClick={() => decide.mutate({ ...base, decision: PlanDecision.REVISE, feedback }, { onSuccess: close })}
            >
              Send to the team
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={open === "reject"} onOpenChange={(o) => !o && close()}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Reject this plan</DialogTitle>
            <DialogDescription>The work order stays as it is; handle it yourself from the work orders page.</DialogDescription>
          </DialogHeader>
          <Select value={reason} onValueChange={setReason}>
            <SelectTrigger aria-label="Reason">
              <SelectValue placeholder="Choose a reason" />
            </SelectTrigger>
            <SelectContent>
              {planRejectionReasons.map((r) => (
                <SelectItem key={r.value} value={r.value}>
                  {r.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <DialogFooter>
            <Button
              variant="destructive"
              disabled={!reason || decide.isPending}
              onClick={() =>
                decide.mutate({ ...base, decision: PlanDecision.REJECT, rejectionReason: reason }, { onSuccess: close })
              }
            >
              Reject
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

const languageNames = new Intl.DisplayNames(["en"], { type: "language" })

/**
 * FR-61: the communicator's English draft, which a facility manager approves (editing it if needed).
 * The application then machine-translates the approved text into the tenant's language.
 */
function NoticeSection({
  incident,
  plan,
  notices,
  language,
}: {
  incident: Incident
  plan: PlanVersion
  notices: TenantNotice[]
  language: string
}) {
  const { can } = useSession()
  const [subject, setSubject] = useState(plan.tenantNotice?.subject ?? "")
  const [body, setBody] = useState(plan.tenantNotice?.body ?? "")
  const send = useIncidentMutation(IncidentService.method.sendTenantNotice, "Notice sent to the tenant")
  const target = language === "en" ? null : (languageNames.of(language) ?? language)
  const personal = /\[PII_\d{4}\]/.test(subject + body)

  return (
    <section aria-label="Tenant notice" className="space-y-3 border-t pt-4">
      <h3 className="text-sm font-medium">Notice to the tenant</h3>
      {notices.length > 0 && <NoticeList notices={notices} />}
      {can(Permission.IncidentsManage) && (
        <div className="space-y-2">
          <p className="text-muted-foreground text-xs">
            {plan.tenantNotice ? "Drafted by the AI communicator. " : "No draft from the team: write one. "}
            Check it before sending; you approve the English text.
            {target ? ` The tenant will read it machine-translated into ${target}.` : ""}
          </p>
          <Label htmlFor="notice-subject">Subject</Label>
          <Input id="notice-subject" value={subject} maxLength={120} onChange={(e) => setSubject(e.target.value)} />
          <Label htmlFor="notice-body">Message</Label>
          <Textarea id="notice-body" value={body} maxLength={1500} rows={6} onChange={(e) => setBody(e.target.value)} />
          {personal && (
            <p className="text-destructive text-xs">Remove the [PII_…] placeholders: notices never include personal details.</p>
          )}
          <Button
            disabled={!subject.trim() || !body.trim() || personal || send.isPending}
            onClick={() => send.mutate({ incidentId: incident.id, subject, body })}
          >
            {send.isPending && <Loader2 aria-hidden className="animate-spin" />}
            {notices.length ? "Send another notice" : "Approve and send"}
          </Button>
        </div>
      )}
    </section>
  )
}

/** Send a request to the incident team by hand (spec 4.3). Emergencies are refused by the backend. */
export function EscalateButton({ requestId, size }: { requestId: string; size?: "sm" | "default" }) {
  const escalate = useIncidentMutation(IncidentService.method.escalateRequest, "Sent to the incident team")
  return (
    <Button variant="outline" size={size} disabled={escalate.isPending} onClick={() => escalate.mutate({ requestId })}>
      {escalate.isPending ? <Loader2 aria-hidden className="animate-spin" /> : <Bot aria-hidden />} Send to incident team
    </Button>
  )
}

function EscalateCard({ requestId }: { requestId: string }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 pt-4 text-sm sm:flex-row sm:items-center sm:justify-between">
        <p className="text-muted-foreground">
          Complex problem? The AI incident team can investigate it and propose a plan for you to approve.
        </p>
        <EscalateButton requestId={requestId} />
      </CardContent>
    </Card>
  )
}
