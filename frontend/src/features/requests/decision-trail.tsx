import { useQuery } from "@connectrpc/connect-query"
import { useState } from "react"
import { useParams } from "react-router"
import { ArrowLeft, Bot, ChevronDown, ChevronRight, ListChecks, Loader2 } from "lucide-react"

import { humanize, reasonLabel, type Tone } from "@/api/labels"
import { StatusBadge } from "@/components/app/badges"
import { ErrorState, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { type DecisionStep, IntakeService, type ModelCall } from "@/gen/facilityops/v1/intake_pb"

const STEP_LABELS: Record<string, string> = {
  "intake.emergency_screen": "Emergency screen",
  "intake.file_check": "File check",
  "intake.media_transcription": "Read the attachment",
  "intake.image_safety": "Image safety",
  "intake.pii_redaction": "Personal data redaction",
  "intake.pii_redaction_result": "Redaction result",
  "intake.content_safety": "Content Safety and Prompt Shields",
  "intake.classification": "AI classification",
  "intake.classification_confirmation": "Confirmation by agreement",
  "intake.routing": "Routing decision",
  "intake.translation": "Translation for staff",
  "intake.outcome": "Outcome",
  "intake.escalation": "Escalated to the incident team",
  "incident.escalated": "Escalated to the incident team",
  "incident.investigation_started": "Incident team: investigation started",
  "incident.needs_review": "Incident sent to a person",
}

const OUTCOMES: Record<string, { label: string; tone: Tone }> = {
  passed: { label: "Passed", tone: "success" },
  flagged: { label: "Flagged", tone: "warning" },
  failed: { label: "Failed", tone: "danger" },
  skipped: { label: "Skipped", tone: "neutral" },
  decided: { label: "Decided", tone: "info" },
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "—"
  if (Array.isArray(value)) return value.map(formatValue).join(", ") || "none"
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .map(([k, v]) => `${humanize(k)} ${formatValue(v)}`)
      .join(", ")
  }
  if (typeof value === "boolean") return value ? "yes" : "no"
  return String(value)
}

function pretty(text: string): string {
  try {
    return JSON.stringify(JSON.parse(text), null, 2)
  } catch {
    return text
  }
}

function Step({ step }: { step: DecisionStep }) {
  const outcome = OUTCOMES[step.outcome] ?? { label: humanize(step.outcome), tone: "neutral" as Tone }
  const details = Object.entries(step.details ?? {})
  return (
    <li className="space-y-1">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{STEP_LABELS[step.step] ?? humanize(step.step.replace("intake.", ""))}</span>
        <StatusBadge {...outcome} />
        {step.reasonCode && <span className="text-sm">{reasonLabel(step.reasonCode)}</span>}
      </div>
      <p className="text-muted-foreground text-xs">
        {[step.service, step.version, step.durationMs ? `${step.durationMs} ms` : null].filter(Boolean).join(" · ")}
      </p>
      {details.length > 0 && (
        <dl className="text-muted-foreground grid gap-x-4 text-xs sm:grid-cols-[auto_1fr]">
          {details.map(([key, value]) => (
            <div key={key} className="contents">
              <dt>{humanize(key)}</dt>
              <dd className="text-foreground break-words">{formatValue(value)}</dd>
            </div>
          ))}
        </dl>
      )}
    </li>
  )
}

function ModelCallItem({ call, contentIncluded }: { call: ModelCall; contentIncluded: boolean }) {
  const [open, setOpen] = useState(false)
  const [showInstructions, setShowInstructions] = useState(false)
  return (
    <li className="space-y-2 rounded-lg border p-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Bot aria-hidden className="size-4" />
        <span className="font-medium">{call.agentName}</span>
        <StatusBadge label={call.status === "succeeded" ? "Succeeded" : "Failed"} tone={call.status === "succeeded" ? "success" : "danger"} />
        <span className="text-muted-foreground text-xs">
          <TimeAgo value={call.startedAt} />
        </span>
      </div>
      <p className="text-muted-foreground text-xs">
        {[
          call.model && `model ${call.model}`,
          call.promptSha && `prompt ${call.promptSha}`,
          call.inputTokens !== undefined && `${call.inputTokens} in / ${call.outputTokens ?? 0} out tokens`,
          `${call.durationMs} ms`,
          call.errorType && `error ${call.errorType}`,
        ]
          .filter(Boolean)
          .join(" · ")}
      </p>
      {call.traceId && <p className="text-muted-foreground font-mono text-xs">trace {call.traceId}</p>}
      {contentIncluded && (call.inputText || call.outputText) ? (
        <div className="space-y-2">
          <Button variant="ghost" size="sm" className="-ml-2" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
            {open ? <ChevronDown aria-hidden /> : <ChevronRight aria-hidden />} What was sent and what it answered
          </Button>
          {open && (
            <div className="space-y-3 text-xs">
              {call.instructions && (
                <div className="space-y-1">
                  <button
                    type="button"
                    className="text-muted-foreground underline underline-offset-2"
                    onClick={() => setShowInstructions((v) => !v)}
                  >
                    {showInstructions ? "Hide" : "Show"} system instructions (prompt {call.promptSha})
                  </button>
                  {showInstructions && (
                    <pre className="bg-muted max-h-64 overflow-auto rounded p-2 whitespace-pre-wrap">{call.instructions}</pre>
                  )}
                </div>
              )}
              <div className="space-y-1">
                <p className="font-medium">Sent to the model (redacted)</p>
                <pre className="bg-muted overflow-auto rounded p-2 whitespace-pre-wrap">{call.inputText}</pre>
              </div>
              <div className="space-y-1">
                <p className="font-medium">Model answer</p>
                <pre className="bg-muted overflow-auto rounded p-2 whitespace-pre-wrap">{pretty(call.outputText)}</pre>
              </div>
            </div>
          )}
        </div>
      ) : null}
    </li>
  )
}

/**
 * "Why this decision": every pipeline step, model call and human action for one request.
 * Loaded on demand: viewing model inputs and answers is recorded in the audit log, so the
 * card never refetches by itself.
 */
export function DecisionTrailCard({ requestId, autoLoad = false }: { requestId: string; autoLoad?: boolean }) {
  // autoLoad: the person opened the trail page on purpose, so load straight away (still one audited view).
  const [requested, setRequested] = useState(autoLoad)
  const { data, error, isFetching, refetch } = useQuery(
    IntakeService.method.getDecisionTrail,
    { requestId },
    { enabled: requested, staleTime: Infinity, refetchOnWindowFocus: false, retry: false },
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ListChecks aria-hidden className="size-4" /> Why this decision
        </CardTitle>
        <CardDescription>
          Every check the system ran, what each found, and what the AI was asked and answered.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {!requested ? (
          <div className="flex flex-col items-start gap-1">
            <Button variant="outline" size="sm" onClick={() => setRequested(true)}>
              Show the decision trail
            </Button>
            <span className="text-muted-foreground text-xs">Viewing what the AI was sent is recorded in the audit log</span>
          </div>
        ) : isFetching && !data ? (
          <p className="text-muted-foreground flex items-center gap-2 text-sm">
            <Loader2 aria-hidden className="size-4 animate-spin" /> Loading…
          </p>
        ) : error ? (
          <ErrorState error={error} onRetry={() => void refetch()} />
        ) : data ? (
          <>
            {data.steps.length === 0 ? (
              <p className="text-muted-foreground text-sm">No steps were recorded for this request (submitted before the trail existed).</p>
            ) : (
              <ol className="space-y-4 border-l pl-4 text-sm">
                {data.steps.map((step, index) => (
                  <Step key={`${step.step}-${index}`} step={step} />
                ))}
              </ol>
            )}

            {data.modelCalls.length > 0 && (
              <div className="space-y-2">
                <p className="text-sm font-medium">Model calls</p>
                <ul className="space-y-2">
                  {data.modelCalls.map((call) => (
                    <ModelCallItem key={call.id} call={call} contentIncluded={data.contentIncluded} />
                  ))}
                </ul>
              </div>
            )}

            {data.humanActions.length > 0 && (
              <div className="space-y-2">
                <p className="text-sm font-medium">People</p>
                <ul className="space-y-1 text-sm">
                  {data.humanActions.map((action, index) => (
                    <li key={index} className="flex flex-wrap gap-2">
                      <span>{humanize(action.eventType)}</span>
                      {action.reasonCode && <span className="text-muted-foreground">{reasonLabel(action.reasonCode)}</span>}
                      <span className="text-muted-foreground">
                        <TimeAgo value={action.createdAt} />
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <p className="text-muted-foreground text-xs">
              {data.contentIncluded
                ? `Model inputs and answers contain only redacted text and are kept for ${data.contentRetentionDays} days.`
                : "Model inputs and answers are visible to facility managers."}{" "}
              Step ids match traces in Application Insights.
            </p>
          </>
        ) : null}
      </CardContent>
    </Card>
  )
}

/**
 * The decision trail on its own page (/decisions/:requestId). Needs only decisions:read, so
 * administrators can explain an AI decision from the Monitoring page without being able to
 * open the request itself; model inputs and answers stay hidden from them (FR-72).
 */
export function DecisionTrailPage() {
  const { requestId = "" } = useParams()
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <Button variant="ghost" size="sm" className="-ml-2" onClick={() => window.history.back()}>
        <ArrowLeft aria-hidden /> Back
      </Button>
      <PageHeader title="Decision trail" description={<span className="font-mono text-xs">Request {requestId}</span>} />
      <DecisionTrailCard requestId={requestId} autoLoad />
    </div>
  )
}
