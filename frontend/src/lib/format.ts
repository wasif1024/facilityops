import type { Timestamp } from "@bufbuild/protobuf/wkt"
import { timestampDate } from "@bufbuild/protobuf/wkt"

export function toDate(timestamp: Timestamp | undefined): Date | undefined {
  return timestamp ? timestampDate(timestamp) : undefined
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" })
const steps: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000],
  ["month", 2_592_000],
  ["day", 86_400],
  ["hour", 3_600],
  ["minute", 60],
]

export function timeAgo(date: Date | undefined, now: Date = new Date()): string {
  if (!date) return "—"
  const seconds = Math.round((date.getTime() - now.getTime()) / 1000)
  for (const [unit, size] of steps) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit)
  }
  return "just now"
}

export function dateTime(date: Date | undefined): string {
  return date ? date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }) : "—"
}

const integer = new Intl.NumberFormat()

/** Counts and token totals; protobuf int64 arrives as bigint. */
export function count(value: number | bigint | undefined): string {
  return value === undefined ? "—" : integer.format(value)
}

export function percent(value: number | undefined, digits = 0): string {
  return value === undefined
    ? "—"
    : new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: digits }).format(value)
}

export function milliseconds(value: number | undefined): string {
  if (value === undefined) return "—"
  return value >= 1000 ? `${(value / 1000).toFixed(1)} s` : `${value} ms`
}

export function shortId(id: string): string {
  return id ? id.slice(0, 8) : "—"
}
