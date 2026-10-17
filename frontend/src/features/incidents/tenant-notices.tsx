import { Mail } from "lucide-react"

import { TranslatedText } from "@/components/app/translated-text"
import { TimeAgo } from "@/components/app/time"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import type { TenantNotice } from "@/gen/facilityops/v1/intake_pb"

/**
 * Notices from facility management (FR-61). The tenant reads them in their language, labelled as a
 * machine translation, with the English a person approved one click away.
 */
export function NoticeList({ notices }: { notices: TenantNotice[] }) {
  return (
    <ul className="space-y-4">
      {notices.map((n) => (
        <li key={n.id} className="space-y-1 border-l-2 pl-3">
          <TranslatedText
            text={n.subject}
            translated={n.translatedSubject}
            sourceLanguage="en"
            translatedLanguage={n.language}
            className="font-medium"
          />
          <TranslatedText
            text={n.body}
            translated={n.translatedBody}
            sourceLanguage="en"
            translatedLanguage={n.language}
            className="text-sm leading-relaxed whitespace-pre-wrap"
          />
          <p className="text-muted-foreground text-xs">
            <TimeAgo value={n.createdAt} />
          </p>
        </li>
      ))}
    </ul>
  )
}

export function NoticesCard({ notices }: { notices: TenantNotice[] }) {
  if (!notices.length) return null
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Mail aria-hidden className="size-4" /> Messages from facility management
        </CardTitle>
      </CardHeader>
      <CardContent>
        <NoticeList notices={notices} />
      </CardContent>
    </Card>
  )
}
