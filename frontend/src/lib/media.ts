/** Limits and types accepted by the backend (backend/app/domain/intake.py). */
export const MAX_MEDIA_BYTES = 8 * 1024 * 1024
export const PHOTO_TYPES = ["image/jpeg", "image/png", "image/webp", "image/gif", "image/bmp", "image/tiff"]
export const VOICE_TYPES = [
  "audio/wav",
  "audio/x-wav",
  "audio/mpeg",
  "audio/mp4",
  "audio/aac",
  "audio/ogg",
  "audio/webm",
  "audio/flac",
]
/** Scanned documents: PDF or TIFF as they are, or a phone photo of a page. Only the first pages are read. */
export const DOCUMENT_TYPES = ["application/pdf", "image/tiff", "image/jpeg", "image/png"]
export const MAX_DOCUMENT_PAGES = 5
export const MAX_PHOTO_SIDE = 2048
/** Phone scans keep more detail than photos: small print must stay readable. */
export const MAX_SCAN_SIDE = 3000
export const MAX_RECORDING_SECONDS = 120

export interface PreparedMedia {
  bytes: Uint8Array
  mediaType: string
  /** Object URL for previews; revoke when done. */
  previewUrl: string
  /** Shown instead of an image preview for PDF and TIFF files. */
  fileName?: string
}

/** "audio/webm;codecs=opus" -> "audio/webm": the backend validates the base type. */
export function baseMediaType(type: string): string {
  return type.split(";")[0].trim().toLowerCase()
}

/**
 * Phone photos are often 12+ megapixels. Anything larger than MAX_PHOTO_SIDE on its long
 * side is redrawn as JPEG, which keeps uploads small and well under the 8 MiB limit.
 */
export async function preparePhoto(
  file: File,
  { maxSide = MAX_PHOTO_SIDE, keepTypes = PHOTO_TYPES }: { maxSide?: number; keepTypes?: string[] } = {},
): Promise<PreparedMedia> {
  const type = baseMediaType(file.type)
  const bitmap = await createImageBitmap(file)
  const scale = Math.min(1, maxSide / Math.max(bitmap.width, bitmap.height))

  if (scale === 1 && keepTypes.includes(type) && file.size <= MAX_MEDIA_BYTES) {
    bitmap.close()
    return { bytes: new Uint8Array(await file.arrayBuffer()), mediaType: type, previewUrl: URL.createObjectURL(file) }
  }

  const canvas = document.createElement("canvas")
  canvas.width = Math.round(bitmap.width * scale)
  canvas.height = Math.round(bitmap.height * scale)
  canvas.getContext("2d")?.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
  bitmap.close()
  const blob = await new Promise<Blob>((resolve, reject) =>
    canvas.toBlob((b) => (b ? resolve(b) : reject(new Error("Could not process the photo"))), "image/jpeg", 0.85),
  )
  return {
    bytes: new Uint8Array(await blob.arrayBuffer()),
    mediaType: "image/jpeg",
    previewUrl: URL.createObjectURL(blob),
  }
}

/**
 * A scanned document. PDF and TIFF are sent as they are (browsers can't redraw them, and
 * the backend reads them page by page); a photo of a page is resized like a photo, but
 * with more detail kept so the text stays readable.
 */
export async function prepareDocument(file: File): Promise<PreparedMedia> {
  const type = baseMediaType(file.type)
  if (type === "application/pdf" || type === "image/tiff") {
    if (file.size > MAX_MEDIA_BYTES) throw new Error("too-large")
    return {
      bytes: new Uint8Array(await file.arrayBuffer()),
      mediaType: type,
      previewUrl: URL.createObjectURL(file),
      fileName: file.name,
    }
  }
  if (!type.startsWith("image/")) throw new Error("unsupported")
  return preparePhoto(file, { maxSide: MAX_SCAN_SIDE, keepTypes: ["image/jpeg", "image/png"] })
}

/** The recording format this browser supports: WebM/Opus (Chromium, Firefox) or MP4/AAC (Safari). */
export function recorderMimeType(): string | undefined {
  if (typeof MediaRecorder === "undefined") return undefined
  return ["audio/webm;codecs=opus", "audio/webm", "audio/mp4", "audio/ogg;codecs=opus"].find((type) =>
    MediaRecorder.isTypeSupported(type),
  )
}

export async function blobToMedia(blob: Blob, fallbackType: string): Promise<PreparedMedia> {
  return {
    bytes: new Uint8Array(await blob.arrayBuffer()),
    mediaType: baseMediaType(blob.type || fallbackType),
    previewUrl: URL.createObjectURL(blob),
  }
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
