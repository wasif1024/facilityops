import { useMutation, useQuery } from "@connectrpc/connect-query"
import { useState } from "react"
import { Link, useParams } from "react-router"
import { ArrowLeft, CheckCircle2, Loader2, Play, XCircle } from "lucide-react"
import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts"
import { toast } from "sonner"

import { describeError } from "@/api/errors"
import { useInvalidateServices } from "@/api/invalidate"
import { evaluationModeLabels, evaluationStatusLabels, humanize, metricLabel } from "@/api/labels"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { StatusBadge } from "@/components/app/badges"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { type ChartConfig, ChartContainer, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import {
  type EvaluationRun,
  EvaluationMode,
  EvaluationService,
  EvaluationStatus,
} from "@/gen/facilityops/v1/evaluations_pb"
import { ChartTable } from "@/features/insights/chart-table"
import { dateTime, toDate } from "@/lib/format"

const KEY_METRICS = ["emergency_recall", "category_accuracy", "route_self_agreement", "counterfactual_route_consistency_modal"]
const TREND_METRICS = [
  "emergency_recall",
  "category_accuracy",
  "priority_within_1",
  "injection_resistance",
  "counterfactual_route_consistency_modal",
  "route_self_agreement",
  "automation_rate",
  "automated_category_accuracy",
]

/** Ratios as percentages; counts (e.g. calls per request) as plain numbers. */
function metricValue(name: string, value: number | undefined): string {
  if (value === undefined) return "—"
  if (name === "classifier_calls_per_request" || name.endsWith("_cases") || name === "samples_per_case") {
    return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  }
  return `${(value * 100).toFixed(1)}%`
}

/** Run status with "2/2 gates" underneath; gate names on hover, full detail on the run page. */
function RunStatus({ run }: { run: EvaluationRun }) {
  const gates = Object.entries(run.gates)
  const passed = gates.filter(([, gate]) => gate.passed).length
  return (
    <div className="space-y-1">
      <StatusBadge {...evaluationStatusLabels[run.status]} />
      {gates.length > 0 && (
        <p
          className="text-muted-foreground text-xs"
          title={gates.map(([name, g]) => `${metricLabel(name)}: ${g.passed ? "passed" : "failed"}`).join("\n")}
        >
          {passed}/{gates.length} gates
        </p>
      )}
    </div>
  )
}

/** Evaluation history, trend and starting runs (frontend spec 6.4). */
export function EvaluationsPage() {
  const { can } = useSession()
  const page = usePage()
  const [starting, setStarting] = useState(false)
  const { data, error, isLoading, refetch } = useQuery(EvaluationService.method.listEvaluationRuns, page, {
    // Poll while a run is in progress so it turns into its result without a reload.
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => r.status === EvaluationStatus.RUNNING) ? 5000 : false,
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title="Evaluations"
        description="Quality of the intake pipeline on the synthetic golden set: every CLI, CI and admin run."
        actions={
          can(Permission.EvaluationsRun) && (
            <Button onClick={() => setStarting(true)}>
              <Play aria-hidden /> Start run
            </Button>
          )
        }
      />
      <TrendCard />
      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.runs.length ? (
        <EmptyState title="No evaluation runs yet">Start one, or run python -m evals.run_eval --store.</EmptyState>
      ) : (
        <Card>
          <CardContent className="pt-4">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Started</TableHead>
                  <TableHead>Run</TableHead>
                  <TableHead>Status</TableHead>
                  {KEY_METRICS.map((m) => (
                    <TableHead key={m} className="hidden text-right lg:table-cell">
                      {metricLabel(m)}
                    </TableHead>
                  ))}
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.runs.map((run) => (
                  <TableRow key={run.id}>
                    <TableCell>
                      <TimeAgo value={run.startedAt} />
                    </TableCell>
                    <TableCell>
                      <Link to={`/evaluations/${run.id}`} className="font-medium hover:underline">
                        {evaluationModeLabels[run.mode]} · {run.source}
                      </Link>
                      <div className="text-muted-foreground text-xs">
                        {run.model || "no model"} {run.promptSha && `· prompt ${run.promptSha.slice(0, 7)}`}
                      </div>
                    </TableCell>
                    <TableCell>
                      <RunStatus run={run} />
                    </TableCell>
                    {KEY_METRICS.map((m) => (
                      <TableCell key={m} className="hidden text-right tabular-nums lg:table-cell">
                        {metricValue(m, run.metrics[m])}
                      </TableCell>
                    ))}
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            <Pagination total={data.total} />
          </CardContent>
        </Card>
      )}
      {starting && <StartRunDialog onClose={() => setStarting(false)} />}
    </div>
  )
}

function TrendCard() {
  const [metric, setMetric] = useFilterParam("metric", "emergency_recall")
  const { data } = useQuery(EvaluationService.method.getMetricTrend, { metric, limit: 30 })
  const points = (data?.points ?? []).map((p) => ({
    when: dateTime(toDate(p.startedAt)),
    value: p.value === undefined ? null : p.value * (metric === "classifier_calls_per_request" ? 1 : 100),
    prompt: p.promptSha.slice(0, 7),
    mode: evaluationModeLabels[p.mode],
  }))
  const config = { value: { label: metricLabel(metric), color: "var(--chart-1)" } } satisfies ChartConfig
  // Ratios on a full 0-100% axis, so a flat 100% doesn't look like a fluctuation.
  const isRatio = metric !== "classifier_calls_per_request"

  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-start justify-between gap-2">
        <div>
          <CardTitle className="text-base">Trend</CardTitle>
          <CardDescription>Hover a point to see the prompt version: a prompt change shows up as a step.</CardDescription>
        </div>
        <Select value={metric} onValueChange={setMetric}>
          <SelectTrigger className="w-60" aria-label="Metric">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {TREND_METRICS.map((m) => (
              <SelectItem key={m} value={m}>
                {metricLabel(m)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </CardHeader>
      <CardContent>
        {points.length < 2 ? (
          <p className="text-muted-foreground text-sm">Not enough runs with this metric yet.</p>
        ) : (
          <ChartContainer config={config} className="h-56 w-full">
            <LineChart data={points} accessibilityLayer>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="when" tickLine={false} axisLine={false} hide />
              <YAxis
                tickLine={false}
                axisLine={false}
                width={40}
                domain={isRatio ? [0, 100] : ["auto", "auto"]}
                tickFormatter={(v: number) => (isRatio ? `${v}%` : String(v))}
              />
              <ChartTooltip
                content={
                  <ChartTooltipContent
                    labelFormatter={(_, payload) => {
                      const point = payload?.[0]?.payload as (typeof points)[number] | undefined
                      return point ? `${point.when} · ${point.mode} · prompt ${point.prompt || "—"}` : ""
                    }}
                  />
                }
              />
              <Line dataKey="value" type="monotone" stroke="var(--color-value)" strokeWidth={2} dot={{ r: 4 }} />
            </LineChart>
          </ChartContainer>
        )}
        {points.length >= 2 && (
          <ChartTable
            caption={`${metricLabel(metric)} per run`}
            rows={points.map((p) => ({
              when: p.when,
              mode: p.mode,
              prompt: p.prompt || "—",
              value: p.value === null ? "—" : isRatio ? `${p.value.toFixed(1)}%` : p.value,
            }))}
            columns={[
              { key: "when", label: "Run" },
              { key: "mode", label: "Mode" },
              { key: "prompt", label: "Prompt" },
              { key: "value", label: metricLabel(metric) },
            ]}
          />
        )}
      </CardContent>
    </Card>
  )
}

function StartRunDialog({ onClose }: { onClose: () => void }) {
  const [mode, setMode] = useState<EvaluationMode>(EvaluationMode.OFFLINE)
  const invalidate = useInvalidateServices()
  const start = useMutation(EvaluationService.method.startEvaluationRun, {
    onSuccess: async () => {
      toast.success("Evaluation started", { description: "It will update here when it finishes." })
      await invalidate(EvaluationService)
      onClose()
    },
    onError: (error) => {
      const { title, message } = describeError(error)
      toast.error(title, { description: message })
    },
  })
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Start an evaluation run</DialogTitle>
          <DialogDescription>Runs on the server against the golden set. Only one run at a time.</DialogDescription>
        </DialogHeader>
        <RadioGroup value={String(mode)} onValueChange={(v) => setMode(Number(v))} className="gap-3">
          <Label className="flex items-start gap-2 font-normal">
            <RadioGroupItem value={String(EvaluationMode.OFFLINE)} className="mt-0.5" />
            <span>
              <span className="font-medium">Offline</span> · emergency screen only, seconds, no Azure cost
            </span>
          </Label>
          <Label className="flex items-start gap-2 font-normal">
            <RadioGroupItem value={String(EvaluationMode.LIVE)} className="mt-0.5" />
            <span>
              <span className="font-medium">Live</span> · runs the classifier on every case, about a minute, uses Azure
            </span>
          </Label>
        </RadioGroup>
        <DialogFooter>
          <Button onClick={() => start.mutate({ mode })} disabled={start.isPending}>
            {start.isPending && <Loader2 aria-hidden className="animate-spin" />} Start
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

export function EvaluationDetailPage() {
  const { runId = "" } = useParams()
  const [failedOnly, setFailedOnly] = useState(false)
  const { data, error, isLoading, refetch } = useQuery(
    EvaluationService.method.getEvaluationRun,
    { runId, failedOnly },
    { refetchInterval: (query) => (query.state.data?.run?.status === EvaluationStatus.RUNNING ? 5000 : false) },
  )
  const run = data?.run

  return (
    <div className="space-y-4">
      <Button variant="ghost" size="sm" asChild>
        <Link to="/evaluations">
          <ArrowLeft aria-hidden /> Back
        </Link>
      </Button>
      {isLoading ? (
        <ListSkeleton rows={4} />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : run ? (
        <>
          <PageHeader
            title={`${evaluationModeLabels[run.mode]} evaluation`}
            description={
              <>
                {dateTime(toDate(run.startedAt))} · {run.source} · {run.model || "no model"}
                {run.promptSha && ` · prompt ${run.promptSha}`} · dataset {run.dataset} ({run.caseCount} cases)
              </>
            }
            actions={<StatusBadge {...evaluationStatusLabels[run.status]} />}
          />
          {run.errorType && <ErrorState error={new Error(run.errorType)} />}
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {Object.entries(run.metrics)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([name, value]) => {
                const gate = run.gates[name]
                return (
                  <Card key={name}>
                    <CardContent className="pt-4">
                      <p className="text-muted-foreground flex items-center gap-1 text-xs">
                        {gate &&
                          (gate.passed ? (
                            <CheckCircle2 aria-label="Gate passed" className="size-3 text-emerald-600" />
                          ) : (
                            <XCircle aria-label="Gate failed" className="size-3 text-red-600" />
                          ))}
                        {metricLabel(name)}
                      </p>
                      <p className="text-xl font-semibold tabular-nums">{metricValue(name, value)}</p>
                      {gate && <p className="text-muted-foreground text-xs">gate ≥ {metricValue(name, gate.threshold)}</p>}
                    </CardContent>
                  </Card>
                )
              })}
          </div>
          <Card>
            <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2">
              <CardTitle className="text-base">Cases</CardTitle>
              <div className="flex items-center gap-2">
                <Switch id="failed-only" checked={failedOnly} onCheckedChange={setFailedOnly} />
                <Label htmlFor="failed-only" className="font-normal">
                  Failed only
                </Label>
              </div>
            </CardHeader>
            <CardContent>
              {!data.cases.length ? (
                <EmptyState title={failedOnly ? "Every case passed" : "No case results"} />
              ) : (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Case</TableHead>
                      <TableHead>Expected</TableHead>
                      <TableHead>Predicted</TableHead>
                      <TableHead>Checks failed</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.cases.map((c) => (
                      <TableRow key={c.caseId}>
                        <TableCell>
                          <div className="font-mono text-sm">{c.caseId}</div>
                          <div className="text-muted-foreground text-xs">{c.tags.join(", ")}</div>
                        </TableCell>
                        <TableCell className="text-sm">
                          {c.expected?.emergency
                            ? `Emergency (${humanize(c.expected.emergencyType)})`
                            : `${humanize(c.expected?.category ?? "")}${c.expected?.priority ? ` · P${c.expected.priority}` : ""}${c.expected?.humanReview ? " · review" : ""}`}
                        </TableCell>
                        <TableCell className="text-sm">
                          {c.predicted?.emergency
                            ? `Emergency (${humanize(c.predicted.emergency)})`
                            : c.predicted?.error
                              ? `Error: ${c.predicted.error}`
                              : c.predicted?.category
                                ? `${humanize(c.predicted.category)}${c.predicted.priority ? ` · P${c.predicted.priority}` : ""}`
                                : "Not classified"}
                        </TableCell>
                        <TableCell>
                          {c.passed ? (
                            <StatusBadge label="Passed" tone="success" />
                          ) : (
                            <div className="flex flex-wrap gap-1">
                              {c.failures.map((f) => (
                                <StatusBadge key={f} label={humanize(f)} tone="danger" />
                              ))}
                            </div>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </div>
  )
}
