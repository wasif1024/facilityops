import { useMutation } from "@connectrpc/connect-query"
import { createConnectQueryKey } from "@connectrpc/connect-query"
import { useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link } from "react-router"
import { Camera, CheckCircle2, Clock, FileText, Loader2, Mic, Siren, Trash2, UserRoundCheck } from "lucide-react"

import { describeError } from "@/api/errors"
import { categoryLabels } from "@/api/labels"
import { PriorityBadge } from "@/components/app/badges"
import { PageHeader } from "@/components/app/states"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group"
import { VoiceRecorder } from "@/features/report/voice-recorder"
import {
  type IntakeResult,
  IntakeService,
  IntakeStatus,
  RequestSource,
} from "@/gen/facilityops/v1/intake_pb"
import { MAX_MEDIA_BYTES, type PreparedMedia, formatBytes, preparePhoto } from "@/lib/media"

const MAX_TEXT = 10_000
type Mode = "text" | "photo" | "voice"
const LANGUAGES = [
  { value: "en", label: "English" },
  { value: "es", label: "Español" },
  { value: "fr", label: "Français" },
]

function browserLanguage(): string {
  const code = navigator.language.slice(0, 2).toLowerCase()
  return LANGUAGES.some((l) => l.value === code) ? code : "en"
}

/**
 * Report a problem by text, photo or voice (frontend spec 6.1).
 *
 * The requestId is created when the form opens and reused on retry, so a double submit
 * or a flaky network never creates two requests: the backend is idempotent per id.
 */
export function ReportPage() {
  const queryClient = useQueryClient()
  const [mode, setMode] = useState<Mode>("text")
  const [text, setText] = useState("")
  const [language, setLanguage] = useState(browserLanguage)
  const [media, setMedia] = useState<PreparedMedia | null>(null)
  const [photoError, setPhotoError] = useState<string | null>(null)
  const [requestId, setRequestId] = useState(() => crypto.randomUUID())
  const [result, setResult] = useState<IntakeResult | null>(null)

  const submit = useMutation(IntakeService.method.submitIntake, {
    onSuccess: (response) => {
      setResult(response.result ?? null)
      void queryClient.invalidateQueries({
        queryKey: createConnectQueryKey({ schema: IntakeService, cardinality: undefined }),
      })
    },
  })

  useEffect(() => () => void (media && URL.revokeObjectURL(media.previewUrl)), [media])

  const changeMode = (next: string) => {
    if (!next) return
    setMode(next as Mode)
    setMedia(null)
    setPhotoError(null)
  }

  const choosePhoto = async (file: File | undefined) => {
    setPhotoError(null)
    if (!file) return
    if (!file.type.startsWith("image/")) {
      setPhotoError("Please choose a photo (JPEG, PNG or WebP).")
      return
    }
    try {
      const prepared = await preparePhoto(file)
      if (prepared.bytes.byteLength > MAX_MEDIA_BYTES) {
        setPhotoError("That photo is too large even after resizing. Please try another.")
        return
      }
      setMedia(prepared)
    } catch {
      setPhotoError("We couldn't read that photo. Please try another one.")
    }
  }

  const startOver = () => {
    setResult(null)
    setText("")
    setMedia(null)
    setRequestId(crypto.randomUUID())
    submit.reset()
  }

  const canSubmit =
    !submit.isPending && (mode === "text" ? text.trim().length > 0 && text.length <= MAX_TEXT : media !== null)

  const send = () => {
    submit.mutate({
      requestId,
      source: mode === "photo" ? RequestSource.PHOTO : mode === "voice" ? RequestSource.VOICE : RequestSource.TEXT,
      text: text.trim(),
      language,
      ...(media ? { media: media.bytes, mediaType: media.mediaType } : {}),
    })
  }

  if (result) return <ResultPanel result={result} onReportAnother={startOver} />

  const error = submit.error ? describeError(submit.error) : null

  return (
    <div className="mx-auto max-w-2xl">
      <PageHeader
        title="Report a problem"
        description="Tell us what's wrong. Describe it, add a photo or record a voice note in English, Spanish or French."
      />
      <Card>
        <CardContent className="space-y-6">
          <ToggleGroup
            type="single"
            value={mode}
            onValueChange={changeMode}
            variant="outline"
            className="w-full"
            aria-label="How do you want to report it?"
          >
            <ToggleGroupItem value="text" className="flex-1">
              <FileText aria-hidden /> Describe it
            </ToggleGroupItem>
            <ToggleGroupItem value="photo" className="flex-1">
              <Camera aria-hidden /> Add a photo
            </ToggleGroupItem>
            <ToggleGroupItem value="voice" className="flex-1">
              <Mic aria-hidden /> Voice note
            </ToggleGroupItem>
          </ToggleGroup>

          {mode === "photo" && (
            <div className="space-y-2">
              <Label htmlFor="photo">Photo</Label>
              {media ? (
                <div className="space-y-2">
                  <img src={media.previewUrl} alt="Your photo" className="max-h-72 rounded-lg border object-contain" />
                  <div className="flex items-center gap-3">
                    <span className="text-muted-foreground text-sm">{formatBytes(media.bytes.byteLength)}</span>
                    <Button type="button" variant="ghost" size="sm" onClick={() => setMedia(null)}>
                      <Trash2 aria-hidden /> Choose another
                    </Button>
                  </div>
                </div>
              ) : (
                // capture="environment" opens the rear camera on phones.
                <Input
                  id="photo"
                  type="file"
                  accept="image/*"
                  capture="environment"
                  onChange={(event) => void choosePhoto(event.target.files?.[0])}
                />
              )}
              {photoError && <p className="text-destructive text-sm">{photoError}</p>}
            </div>
          )}

          {mode === "voice" && (
            <div className="space-y-2">
              <Label>Voice note</Label>
              <VoiceRecorder value={media} onChange={setMedia} />
            </div>
          )}

          <div className="space-y-2">
            <Label htmlFor="description">{mode === "text" ? "What's the problem?" : "Anything to add? (optional)"}</Label>
            <Textarea
              id="description"
              rows={mode === "text" ? 6 : 3}
              value={text}
              maxLength={MAX_TEXT}
              onChange={(event) => setText(event.target.value)}
              placeholder={
                mode === "text" ? "For example: the heating in my bedroom stopped working this morning." : "Your unit, when it started…"
              }
            />
            <p className="text-muted-foreground text-right text-xs">
              {text.length.toLocaleString()} / {MAX_TEXT.toLocaleString()}
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="language">Language</Label>
            <Select value={language} onValueChange={setLanguage}>
              <SelectTrigger id="language" className="w-48">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LANGUAGES.map((l) => (
                  <SelectItem key={l.value} value={l.value}>
                    {l.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <Alert>
            <Siren aria-hidden />
            <AlertTitle>Is anyone in danger?</AlertTitle>
            <AlertDescription>
              For fire, gas smells or flooding, call your local emergency number first. Reports mentioning them go
              straight to the on-call team.
            </AlertDescription>
          </Alert>

          {error && (
            <Alert variant="destructive">
              <AlertTitle>{error.title}</AlertTitle>
              <AlertDescription>
                {error.message} {error.retryable && "Sending again is safe: it won't create a duplicate."}
              </AlertDescription>
            </Alert>
          )}

          <Button className="w-full" size="lg" disabled={!canSubmit} onClick={send}>
            {submit.isPending && <Loader2 aria-hidden className="animate-spin" />}
            {submit.isPending ? "Sending…" : error?.retryable ? "Send again" : "Send report"}
          </Button>
        </CardContent>
      </Card>
    </div>
  )
}

function ResultPanel({ result, onReportAnother }: { result: IntakeResult; onReportAnother: () => void }) {
  const isEmergency = result.status === IntakeStatus.EMERGENCY_HANDOFF
  const [acknowledged, setAcknowledged] = useState(!isEmergency)

  return (
    <div className="mx-auto max-w-2xl space-y-4" aria-live="assertive">
      {isEmergency ? (
        <Card className="border-red-300 bg-red-50 dark:border-red-900 dark:bg-red-950" role="alert">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-red-900 dark:text-red-100">
              <Siren aria-hidden /> We've alerted the on-call team
            </CardTitle>
            <CardDescription className="text-red-900/80 dark:text-red-100/80">
              Your report mentions a possible emergency, so it went straight to a person, not to our automated system.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4 text-red-950 dark:text-red-50">
            <p className="font-medium">
              If anyone is in danger, leave the area and call your local emergency number now.
            </p>
            {!acknowledged && (
              <Button variant="destructive" onClick={() => setAcknowledged(true)}>
                I understand
              </Button>
            )}
          </CardContent>
        </Card>
      ) : (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              {result.status === IntakeStatus.WORK_ORDER_CREATED ? (
                <CheckCircle2 aria-hidden className="text-emerald-600" />
              ) : result.status === IntakeStatus.PENDING_APPROVAL ? (
                <Clock aria-hidden className="text-sky-600" />
              ) : (
                <UserRoundCheck aria-hidden className="text-amber-600" />
              )}
              {result.status === IntakeStatus.WORK_ORDER_CREATED
                ? "Thanks, a work order is on its way"
                : result.status === IntakeStatus.PENDING_APPROVAL
                  ? "Thanks, a facility manager will review this shortly"
                  : "Thanks, a person will review your request"}
            </CardTitle>
            <CardDescription>
              {result.status === IntakeStatus.WORK_ORDER_CREATED
                ? "A technician will be assigned. You can follow progress in My requests."
                : "We'll update it in My requests as soon as there's news."}
            </CardDescription>
          </CardHeader>
          {result.status === IntakeStatus.WORK_ORDER_CREATED || result.status === IntakeStatus.PENDING_APPROVAL ? (
            <CardContent className="flex flex-wrap items-center gap-3 text-sm">
              <span>{categoryLabels[result.category]}</span>
              <PriorityBadge priority={result.priority} />
            </CardContent>
          ) : null}
        </Card>
      )}

      {acknowledged && (
        <div className="flex flex-wrap gap-2">
          <Button onClick={onReportAnother}>Report another problem</Button>
          <Button variant="outline" asChild>
            <Link to="/requests/mine">See my requests</Link>
          </Button>
        </div>
      )}
    </div>
  )
}
