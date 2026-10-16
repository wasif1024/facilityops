import { AlertTriangle } from "lucide-react"

import { priorityLabels, type Tone } from "@/api/labels"
import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

const toneClasses: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground border-transparent",
  info: "bg-sky-100 text-sky-900 border-transparent dark:bg-sky-950 dark:text-sky-200",
  success: "bg-emerald-100 text-emerald-900 border-transparent dark:bg-emerald-950 dark:text-emerald-200",
  warning: "bg-amber-100 text-amber-900 border-transparent dark:bg-amber-950 dark:text-amber-200",
  danger: "bg-red-100 text-red-900 border-transparent dark:bg-red-950 dark:text-red-200",
}

/** A status pill. Colour is never the only signal: the text always says it (spec 8). */
export function StatusBadge({ label, tone, className }: { label: string; tone: Tone; className?: string }) {
  return (
    <Badge variant="outline" className={cn("gap-1 font-medium", toneClasses[tone], className)}>
      {tone === "danger" && <AlertTriangle aria-hidden className="size-3" />}
      {label}
    </Badge>
  )
}

export function PriorityBadge({ priority }: { priority: number | undefined }) {
  if (!priority) return <span className="text-muted-foreground">—</span>
  const { label, tone } = priorityLabels[priority] ?? { label: String(priority), tone: "neutral" as Tone }
  return <StatusBadge label={`P${priority} · ${label}`} tone={tone} />
}
