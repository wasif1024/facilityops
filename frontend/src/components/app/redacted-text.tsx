import { createClient } from "@connectrpc/connect"
import { useTransport } from "@connectrpc/connect-query"
import { Eye, EyeOff, Loader2 } from "lucide-react"
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react"
import { toast } from "sonner"

import { describeError } from "@/api/errors"
import { Button } from "@/components/ui/button"
import { IntakeService } from "@/gen/facilityops/v1/intake_pb"

const PLACEHOLDER = /(\[PII_\d{4}\])/
const IS_PLACEHOLDER = /^\[PII_\d{4}\]$/
const REVEAL_FOR_MS = 2 * 60 * 1000

/**
 * Redacted text with [PII_0001] placeholders shown as neutral chips, or, once revealed,
 * the original values highlighted in place (frontend spec section 7).
 */
export function RedactedText({
  text,
  revealed,
  className,
  lang,
}: {
  text: string
  revealed?: Record<string, string>
  className?: string
  /** Language of the text, for screen readers and hyphenation. */
  lang?: string
}) {
  const parts = text.split(PLACEHOLDER)
  return (
    <p className={className} lang={lang}>
      {parts.map((part, i) => {
        if (!IS_PLACEHOLDER.test(part)) return <Fragment key={i}>{part}</Fragment>
        const value = revealed?.[part]
        return value ? (
          <mark key={i} className="rounded bg-amber-200 px-1 text-amber-950 dark:bg-amber-800 dark:text-amber-50">
            {value}
          </mark>
        ) : (
          <span
            key={i}
            className="bg-muted text-muted-foreground mx-0.5 rounded px-1.5 py-0.5 text-xs whitespace-nowrap"
            title="Personal detail, hidden to protect privacy"
          >
            personal detail
          </span>
        )
      })}
    </p>
  )
}

/**
 * Reveal the values behind the placeholders of one request.
 *
 * Deliberately not a cached query: values live only in this component's state, are
 * never written to storage or the URL, and are cleared after two minutes or on unmount.
 * Every reveal is recorded in the backend's audit log.
 */
export function useReveal(requestId: string) {
  const transport = useTransport()
  const client = useMemo(() => createClient(IntakeService, transport), [transport])
  const [values, setValues] = useState<Record<string, string> | undefined>()
  const [pending, setPending] = useState(false)
  const timer = useRef<number | undefined>(undefined)

  const hide = useCallback(() => {
    window.clearTimeout(timer.current)
    setValues(undefined)
  }, [])

  const reveal = useCallback(async () => {
    setPending(true)
    try {
      const response = await client.revealRequestDetails({ requestId })
      setValues({ ...response.values })
      window.clearTimeout(timer.current)
      timer.current = window.setTimeout(() => setValues(undefined), REVEAL_FOR_MS)
    } catch (error) {
      const { title, message } = describeError(error)
      toast.error(title, { description: message })
    } finally {
      setPending(false)
    }
  }, [client, requestId])

  useEffect(() => hide, [hide, requestId])

  return { values, pending, reveal, hide }
}

export function RevealButton({ reveal }: { reveal: ReturnType<typeof useReveal> }) {
  if (reveal.values) {
    return (
      <Button variant="outline" size="sm" onClick={reveal.hide}>
        <EyeOff aria-hidden /> Hide personal details
      </Button>
    )
  }
  return (
    <div className="flex flex-col items-start gap-1">
      <Button variant="outline" size="sm" onClick={() => void reveal.reveal()} disabled={reveal.pending}>
        {reveal.pending ? <Loader2 aria-hidden className="animate-spin" /> : <Eye aria-hidden />} Reveal personal details
      </Button>
      <span className="text-muted-foreground text-xs">Recorded in the audit log · hidden again after 2 minutes</span>
    </div>
  )
}
