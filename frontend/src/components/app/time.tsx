import type { Timestamp } from "@bufbuild/protobuf/wkt"

import { dateTime, timeAgo, toDate } from "@/lib/format"

/** Relative time ("12 minutes ago") with the exact time on hover and for screen readers. */
export function TimeAgo({ value }: { value: Timestamp | undefined }) {
  const date = toDate(value)
  if (!date) return <span className="text-muted-foreground">—</span>
  return (
    <time dateTime={date.toISOString()} title={dateTime(date)} className="whitespace-nowrap">
      {timeAgo(date)}
    </time>
  )
}
