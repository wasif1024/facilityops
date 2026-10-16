import { useEffect, useRef, useState } from "react"
import { Mic, Square, Trash2 } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  MAX_MEDIA_BYTES,
  MAX_RECORDING_SECONDS,
  type PreparedMedia,
  VOICE_TYPES,
  baseMediaType,
  blobToMedia,
  formatBytes,
  recorderMimeType,
} from "@/lib/media"

type State = { kind: "idle" } | { kind: "recording"; seconds: number } | { kind: "denied" }

/**
 * Record a voice note with MediaRecorder, play it back, then send. If the microphone is
 * unavailable, explain why and offer a file upload instead (frontend spec 6.1).
 */
export function VoiceRecorder({
  value,
  onChange,
}: {
  value: PreparedMedia | null
  onChange: (media: PreparedMedia | null) => void
}) {
  const [state, setState] = useState<State>({ kind: "idle" })
  const [error, setError] = useState<string | null>(null)
  const recorder = useRef<MediaRecorder | null>(null)
  const ticker = useRef<number | undefined>(undefined)
  const supported = recorderMimeType() !== undefined

  useEffect(
    () => () => {
      window.clearInterval(ticker.current)
      recorder.current?.stream.getTracks().forEach((track) => track.stop())
    },
    [],
  )

  const stop = () => {
    window.clearInterval(ticker.current)
    recorder.current?.stop()
  }

  const start = async () => {
    setError(null)
    let stream: MediaStream
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    } catch {
      setState({ kind: "denied" })
      return
    }
    const mimeType = recorderMimeType()
    const chunks: Blob[] = []
    const mediaRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
    mediaRecorder.ondataavailable = (event) => chunks.push(event.data)
    mediaRecorder.onstop = async () => {
      stream.getTracks().forEach((track) => track.stop())
      const blob = new Blob(chunks, { type: mediaRecorder.mimeType })
      setState({ kind: "idle" })
      if (blob.size > MAX_MEDIA_BYTES) {
        setError("That recording is too large. Please keep it under two minutes.")
        return
      }
      onChange(await blobToMedia(blob, mimeType ?? "audio/webm"))
    }
    recorder.current = mediaRecorder
    mediaRecorder.start()
    setState({ kind: "recording", seconds: 0 })
    ticker.current = window.setInterval(() => {
      setState((current) => {
        if (current.kind !== "recording") return current
        const seconds = current.seconds + 1
        if (seconds >= MAX_RECORDING_SECONDS) stop()
        return { kind: "recording", seconds }
      })
    }, 1000)
  }

  const uploadFile = async (file: File | undefined) => {
    setError(null)
    if (!file) return
    if (!VOICE_TYPES.includes(baseMediaType(file.type))) {
      setError("Please choose an audio file (MP3, WAV, M4A, OGG or WebM).")
      return
    }
    if (file.size > MAX_MEDIA_BYTES) {
      setError(`That file is ${formatBytes(file.size)}; the limit is 8 MB.`)
      return
    }
    onChange(await blobToMedia(file, file.type))
  }

  if (value) {
    return (
      <div className="flex flex-wrap items-center gap-3 rounded-lg border p-3">
        <audio controls src={value.previewUrl} className="max-w-full" />
        <span className="text-muted-foreground text-sm">{formatBytes(value.bytes.byteLength)}</span>
        <Button type="button" variant="ghost" size="sm" onClick={() => onChange(null)}>
          <Trash2 aria-hidden /> Record again
        </Button>
      </div>
    )
  }

  return (
    <div className="space-y-3">
      {supported && state.kind !== "denied" && (
        <div className="flex items-center gap-3">
          {state.kind === "recording" ? (
            <>
              <Button type="button" variant="destructive" onClick={stop}>
                <Square aria-hidden /> Stop
              </Button>
              <span className="flex items-center gap-2 text-sm" aria-live="polite">
                <span className="size-2 animate-pulse rounded-full bg-red-500" aria-hidden />
                Recording {formatSeconds(state.seconds)} / {formatSeconds(MAX_RECORDING_SECONDS)}
              </span>
            </>
          ) : (
            <Button type="button" onClick={() => void start()}>
              <Mic aria-hidden /> Start recording
            </Button>
          )}
        </div>
      )}
      {(state.kind === "denied" || !supported) && (
        <Alert>
          <AlertTitle>Microphone not available</AlertTitle>
          <AlertDescription>
            {supported
              ? "Allow microphone access for this site in your browser settings, or upload a recording instead."
              : "This browser can't record audio. You can upload a recording instead."}
          </AlertDescription>
        </Alert>
      )}
      <div className="space-y-1">
        <label htmlFor="voice-file" className="text-muted-foreground text-sm">
          Or upload a recording
        </label>
        <Input id="voice-file" type="file" accept="audio/*" onChange={(event) => void uploadFile(event.target.files?.[0])} />
      </div>
      {error && <p className="text-destructive text-sm">{error}</p>}
    </div>
  )
}

function formatSeconds(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`
}
