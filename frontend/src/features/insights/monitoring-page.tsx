import { useQuery } from "@connectrpc/connect-query"
import { Link } from "react-router"
import { Permission } from "@/auth/permissions"
import { useSession } from "@/auth/session"
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from "recharts"

import { categoryLabels, humanize, reasonLabel } from "@/api/labels"
import { MaintenanceCategory } from "@/gen/facilityops/v1/intake_pb"
import { StatusBadge } from "@/components/app/badges"
import { Pagination, useFilterParam, usePage } from "@/components/app/pagination"
import { EmptyState, ErrorState, ListSkeleton, PageHeader } from "@/components/app/states"
import { TimeAgo } from "@/components/app/time"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { type ChartConfig, ChartContainer, ChartLegend, ChartLegendContent, ChartTooltip, ChartTooltipContent } from "@/components/ui/chart"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Switch } from "@/components/ui/switch"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AgentRunStatus, MonitoringService } from "@/gen/facilityops/v1/monitoring_pb"
import { count, milliseconds, percent } from "@/lib/format"
import { ChartTable } from "@/features/insights/chart-table"
import { StatTile } from "@/features/insights/stat-tile"

const windows = ["1", "7", "30", "90"]

/** Reports key categories by name ("hvac"); show the same wording as everywhere else. */
function categoryName(name: string): string {
  const value = MaintenanceCategory[name.toUpperCase() as keyof typeof MaintenanceCategory]
  return value === undefined ? humanize(name) : categoryLabels[value]
}

// Fixed slot per outcome: a colour always means the same outcome, whatever the filter.
const intakeStatusConfig = {
  work_order_created: { label: "Work order", color: "var(--chart-1)" },
  pending_approval: { label: "Pending approval", color: "var(--chart-2)" },
  human_review: { label: "Human review", color: "var(--chart-3)" },
  emergency_handoff: { label: "Emergency", color: "var(--chart-4)" },
  dismissed: { label: "Dismissed", color: "var(--chart-5)" },
} satisfies ChartConfig

// A 2px surface-coloured gap separates stacked segments.
const SEGMENT_GAP = { stroke: "var(--background)", strokeWidth: 2 }

/** Pivot [{day, key, count}] rows into one row per day for a stacked bar chart. */
function pivot<T>(rows: T[], day: (r: T) => string, key: (r: T) => string, value: (r: T) => number) {
  const byDay = new Map<string, Record<string, number | string>>()
  for (const row of rows) {
    const entry = byDay.get(day(row)) ?? { day: day(row) }
    entry[key(row)] = ((entry[key(row)] as number | undefined) ?? 0) + value(row)
    byDay.set(day(row), entry)
  }
  return [...byDay.values()]
}

/** Agent health and intake outcomes (frontend spec 6.4). */
export function MonitoringPage() {
  const [days, setDays] = useFilterParam("days", "7")
  const [withEvals, setWithEvals] = useFilterParam("evals", "")

  return (
    <div>
      <PageHeader
        title="Monitoring"
        description="How requests are routed and how the AI agents behave. Counts and timings only, never content."
        actions={
          <div className="flex flex-wrap items-center gap-4">
            <div className="flex items-center gap-2">
              <Switch id="evals" checked={withEvals === "1"} onCheckedChange={(on) => setWithEvals(on ? "1" : "")} />
              <Label htmlFor="evals" className="text-sm font-normal">
                Include evaluation traffic
              </Label>
            </div>
            <Select value={days} onValueChange={setDays}>
              <SelectTrigger className="w-36" aria-label="Time window">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {windows.map((d) => (
                  <SelectItem key={d} value={d}>
                    Last {d === "1" ? "24 hours" : `${d} days`}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        }
      />
      <Tabs defaultValue="intake">
        <TabsList className="mb-4">
          <TabsTrigger value="intake">Intake</TabsTrigger>
          <TabsTrigger value="agents">Agents</TabsTrigger>
          <TabsTrigger value="runs">Recent runs</TabsTrigger>
        </TabsList>
        <TabsContent value="intake">
          <IntakeReport days={Number(days)} />
        </TabsContent>
        <TabsContent value="agents">
          <AgentReport days={Number(days)} includeEvaluations={withEvals === "1"} />
        </TabsContent>
        <TabsContent value="runs">
          <RecentRuns />
        </TabsContent>
      </Tabs>
    </div>
  )
}

function IntakeReport({ days }: { days: number }) {
  const { data, error, isLoading, refetch } = useQuery(MonitoringService.method.getIntakeReport, { days })
  if (isLoading) return <ListSkeleton rows={4} />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data || data.totalRequests === 0) return <EmptyState title="No requests in this window" />

  const daily = pivot(data.daily, (r) => r.day, (r) => r.status, (r) => r.count)
  const categories = Object.entries(data.byCategory).sort((a, b) => b[1] - a[1])
  const maxCategory = Math.max(...categories.map(([, n]) => n), 1)

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-6">
        <StatTile label="Requests" value={count(data.totalRequests)} />
        <StatTile label="Automated" value={percent(data.automationRate)} hint="work order or approval" />
        <StatTile label="Human review" value={percent(data.humanReviewRate)} />
        <StatTile label="Emergencies" value={count(data.emergencyHandoffs)} />
        <StatTile label="Pending approvals" value={count(data.pendingApprovals)} hint="right now" />
        <StatTile label="Avg confidence" value={percent(data.averageConfidence)} />
      </div>
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Outcomes per day</CardTitle>
        </CardHeader>
        <CardContent>
          <ChartContainer config={intakeStatusConfig} className="h-64 w-full">
            <BarChart data={daily} accessibilityLayer>
              <CartesianGrid vertical={false} />
              <XAxis dataKey="day" tickLine={false} axisLine={false} />
              <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} />
              <ChartTooltip content={<ChartTooltipContent />} />
              <ChartLegend content={<ChartLegendContent />} />
              {Object.keys(intakeStatusConfig).map((key) => (
                <Bar key={key} dataKey={key} stackId="status" fill={`var(--color-${key})`} {...SEGMENT_GAP} />
              ))}
            </BarChart>
          </ChartContainer>
          <ChartTable
            caption="Intake outcomes per day"
            rows={daily}
            columns={[
              { key: "day", label: "Day" },
              ...Object.entries(intakeStatusConfig).map(([key, c]) => ({ key, label: c.label })),
            ]}
          />
        </CardContent>
      </Card>
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">By category</CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {categories.map(([name, n]) => (
              <div key={name} className="grid grid-cols-[8rem_1fr_3rem] items-center gap-2 text-sm">
                <span>{categoryName(name)}</span>
                <div className="bg-muted h-2 rounded">
                  <div className="h-2 rounded bg-[var(--chart-1)]" style={{ width: `${(n / maxCategory) * 100}%` }} />
                </div>
                <span className="text-right tabular-nums">{n}</span>
              </div>
            ))}
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Top reasons</CardTitle>
            <CardDescription>Why requests were routed the way they were</CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-1 text-sm">
              {data.topReasonCodes.map((r) => (
                <li key={r.reasonCode} className="flex justify-between gap-2">
                  <span>{reasonLabel(r.reasonCode)}</span>
                  <span className="tabular-nums">{r.count}</span>
                </li>
              ))}
            </ul>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function AgentReport({ days, includeEvaluations }: { days: number; includeEvaluations: boolean }) {
  const { data, error, isLoading, refetch } = useQuery(MonitoringService.method.getAgentReport, {
    days,
    includeEvaluations,
  })
  if (isLoading) return <ListSkeleton rows={4} />
  if (error) return <ErrorState error={error} onRetry={() => void refetch()} />
  if (!data?.agents.length) return <EmptyState title="No agent runs in this window" />

  const agentNames = [...new Set(data.daily.map((d) => d.agentName))]
  const config = Object.fromEntries(
    agentNames.map((name, i) => [`agent${i}`, { label: name, color: `var(--chart-${(i % 5) + 1})` }]),
  ) satisfies ChartConfig
  const runsPerDay = pivot(data.daily, (r) => r.day, (r) => `agent${agentNames.indexOf(r.agentName)}`, (r) => r.runs)

  return (
    <div className="space-y-4">
      <Card>
        <CardContent className="pt-4">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Agent</TableHead>
                <TableHead className="text-right">Runs</TableHead>
                <TableHead className="text-right">Failures</TableHead>
                <TableHead className="text-right">p50 / p95</TableHead>
                <TableHead className="hidden text-right md:table-cell">Tokens</TableHead>
                <TableHead className="hidden text-right md:table-cell">Per run</TableHead>
                <TableHead className="hidden lg:table-cell">Last run</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.agents.map((agent) => (
                <TableRow key={`${agent.agentName}-${agent.model}`}>
                  <TableCell>
                    <div className="font-medium">{agent.agentName}</div>
                    <div className="text-muted-foreground text-xs">{agent.model}</div>
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{count(agent.runs)}</TableCell>
                  <TableCell className="text-right tabular-nums">
                    {count(agent.failures)} ({percent(agent.failureRate, 1)})
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {milliseconds(agent.p50Ms)} / {milliseconds(agent.p95Ms)}
                  </TableCell>
                  <TableCell className="hidden text-right tabular-nums md:table-cell">{count(agent.totalTokens)}</TableCell>
                  <TableCell className="hidden text-right tabular-nums md:table-cell">{count(agent.avgTokensPerRun)}</TableCell>
                  <TableCell className="hidden lg:table-cell">
                    <TimeAgo value={agent.lastRunAt} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
      <div className="grid gap-4 md:grid-cols-[2fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Runs per day</CardTitle>
          </CardHeader>
          <CardContent>
            <ChartContainer config={config} className="h-56 w-full">
              <BarChart data={runsPerDay} accessibilityLayer>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="day" tickLine={false} axisLine={false} />
                <YAxis allowDecimals={false} tickLine={false} axisLine={false} width={32} />
                <ChartTooltip content={<ChartTooltipContent />} />
                {Object.keys(config).map((key) => (
                  <Bar key={key} dataKey={key} stackId="runs" fill={`var(--color-${key})`} {...SEGMENT_GAP} />
                ))}
                {agentNames.length > 1 && <ChartLegend content={<ChartLegendContent />} />}
              </BarChart>
            </ChartContainer>
            <ChartTable
              caption="Agent runs per day"
              rows={runsPerDay}
              columns={[
                { key: "day", label: "Day" },
                ...Object.entries(config).map(([key, c]) => ({ key, label: c.label })),
              ]}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Errors</CardTitle>
          </CardHeader>
          <CardContent>
            {data.errors.length ? (
              <ul className="space-y-1 text-sm">
                {data.errors.map((e) => (
                  <li key={`${e.agentName}-${e.errorType}`} className="flex justify-between gap-2">
                    <span className="font-mono text-xs">{e.errorType || "Unknown"}</span>
                    <span className="tabular-nums">{e.count}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground text-sm">No failures in this window.</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function RecentRuns() {
  const page = usePage()
  const [status, setStatus] = useFilterParam("runStatus", "all")
  const canSeeTrail = useSession().can(Permission.DecisionsRead)
  const { data, error, isLoading, refetch } = useQuery(MonitoringService.method.listAgentRuns, {
    ...page,
    status: status === "failed" ? AgentRunStatus.FAILED : status === "succeeded" ? AgentRunStatus.SUCCEEDED : AgentRunStatus.UNSPECIFIED,
  })
  return (
    <div className="space-y-3">
      <Select value={status} onValueChange={setStatus}>
        <SelectTrigger className="w-40" aria-label="Filter by outcome">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value="all">All outcomes</SelectItem>
          <SelectItem value="succeeded">Succeeded</SelectItem>
          <SelectItem value="failed">Failed</SelectItem>
        </SelectContent>
      </Select>
      {isLoading ? (
        <ListSkeleton />
      ) : error ? (
        <ErrorState error={error} onRetry={() => void refetch()} />
      ) : !data?.runs.length ? (
        <EmptyState title="No agent runs" />
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>When</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Outcome</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead className="hidden text-right md:table-cell">Tokens</TableHead>
                <TableHead className="hidden lg:table-cell">For</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.runs.map((run) => (
                <TableRow key={run.id}>
                  <TableCell>
                    <TimeAgo value={run.startedAt} />
                  </TableCell>
                  <TableCell>{run.agentName}</TableCell>
                  <TableCell>
                    {run.status === AgentRunStatus.FAILED ? (
                      <StatusBadge label={run.errorType || "Failed"} tone="danger" />
                    ) : (
                      <StatusBadge label="Succeeded" tone="success" />
                    )}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">{milliseconds(run.durationMs)}</TableCell>
                  <TableCell className="hidden text-right tabular-nums md:table-cell">{count(run.totalTokens)}</TableCell>
                  <TableCell className="text-muted-foreground hidden text-xs lg:table-cell">
                    {run.evaluationRunId ? (
                      <Link to={`/evaluations/${run.evaluationRunId}`} className="underline">
                        Evaluation run
                      </Link>
                    ) : run.requestId ? (
                      canSeeTrail ? (
                        <Link to={`/decisions/${run.requestId}`} className="underline">
                          Decision trail
                        </Link>
                      ) : (
                        "Intake request"
                      )
                    ) : (
                      "—"
                    )}
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
