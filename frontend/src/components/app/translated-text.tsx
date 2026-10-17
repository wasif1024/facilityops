import { useState } from "react"
import { Languages } from "lucide-react"

import { RedactedText } from "@/components/app/redacted-text"

const languageNames = new Intl.DisplayNames(["en"], { type: "language" })

function languageName(code: string): string {
  try {
    return languageNames.of(code) ?? code
  } catch {
    return code
  }
}

function shorten(text: string, length?: number): string {
  return length && text.length > length ? `${text.slice(0, length).trimEnd()}…` : text
}

/**
 * Request text for staff (FR-60). When the request wasn't in English, the machine translation is
 * shown first, clearly labelled, with the tenant's original one click away. Both are redacted and
 * share the same [PII_nnnn] placeholders, so revealed values apply to either.
 */
export function TranslatedText({
  text,
  translated,
  sourceLanguage,
  translatedLanguage = "en",
  revealed,
  className,
  excerpt,
}: {
  text: string
  translated?: string
  sourceLanguage?: string
  /** Language of the translation: English for staff (FR-60), the tenant's for notices (FR-61). */
  translatedLanguage?: string
  revealed?: Record<string, string>
  className?: string
  /** Shorten both texts to this many characters (lists). */
  excerpt?: number
}) {
  const [showOriginal, setShowOriginal] = useState(false)
  if (!translated) return <RedactedText text={shorten(text, excerpt)} revealed={revealed} className={className} />

  const language = languageName(sourceLanguage ?? "")
  return (
    <div className="space-y-1">
      <RedactedText
        text={shorten(showOriginal ? text : translated, excerpt)}
        revealed={revealed}
        className={className}
        lang={showOriginal ? sourceLanguage : translatedLanguage}
      />
      <p className="text-muted-foreground flex flex-wrap items-center gap-1 text-xs">
        <Languages aria-hidden className="size-3.5" />
        {showOriginal ? `Original (${language})` : `Machine translation from ${language}`}
        <span aria-hidden>·</span>
        <button
          type="button"
          className="hover:text-foreground underline underline-offset-2"
          onClick={(event) => {
            // Inside list rows the text sits in a link; toggling must not navigate.
            event.preventDefault()
            event.stopPropagation()
            setShowOriginal((value) => !value)
          }}
        >
          {showOriginal ? "Show translation" : "Show original"}
        </button>
      </p>
    </div>
  )
}
