"""Checks on uploaded files before they go to any Azure service.

The declared media type is chosen by the client, so it proves nothing. These checks look at
the bytes: the file signature must match the declared type, images must decode to a sane
size, and PDFs must not be encrypted. Each failure is a reason code; the coordinator sends
the request to human review rather than rejecting it, so a tenant is never left without an
answer. Nothing here does I/O.
"""

from __future__ import annotations

import io
import re
import warnings

from backend.app.domain.intake import RequestSource

# Image analysis services need at least 50 x 50 pixels; anything smaller shows nothing useful.
MIN_IMAGE_SIDE = 50
# Bounds decoding cost. 40 megapixels covers phone cameras; larger images are rejected before
# any pixel data is decoded (a "decompression bomb" is a small file with a huge canvas).
MAX_IMAGE_PIXELS = 40_000_000
# Only the first MAX_DOCUMENT_PAGES pages are read; a scan with more frames than this is
# refused outright, since it is unlikely to be a maintenance request.
MAX_TIFF_FRAMES = 20

# Signature family each declared type must match.
_EXPECTED_FAMILY = {
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
    "image/tiff": "tiff",
    "application/pdf": "pdf",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mpeg_audio",
    "audio/aac": "mpeg_audio",
    "audio/mp4": "mp4",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/flac": "flac",
}
# Some declared types legitimately arrive in more than one container.
_ALSO_ACCEPTED = {"audio/aac": {"mp4"}, "audio/mp4": {"mpeg_audio"}}
_PILLOW_FORMAT = {
    "jpeg": "JPEG",
    "png": "PNG",
    "gif": "GIF",
    "webp": "WEBP",
    "bmp": "BMP",
    "tiff": "TIFF",
}
# The trailer of an encrypted PDF names an /Encrypt dictionary.
_PDF_ENCRYPT = re.compile(rb"/Encrypt\b")


def sniff(data: bytes) -> str | None:
    """The file family from its leading bytes, or None when unrecognised."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "webp"
    if head.startswith(b"RIFF") and head[8:12] == b"WAVE":
        return "wav"
    if head.startswith(b"BM"):
        return "bmp"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return "tiff"
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"OggS"):
        return "ogg"
    if head.startswith(b"fLaC"):
        return "flac"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "webm"
    if head[4:8] == b"ftyp":
        return "mp4"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0):
        return "mpeg_audio"
    return None


def is_image(media_type: str) -> bool:
    return media_type.startswith("image/")


def check_media(source: RequestSource, media: bytes, media_type: str) -> str | None:
    """None when the file is what it claims to be and safe to process; otherwise a reason code."""
    expected = _EXPECTED_FAMILY.get(media_type)
    actual = sniff(media)
    if expected is None or actual is None:
        return "media_type_mismatch"
    if actual != expected and actual not in _ALSO_ACCEPTED.get(media_type, set()):
        return "media_type_mismatch"
    if actual in _PILLOW_FORMAT:
        return _check_image(media, _PILLOW_FORMAT[actual], allow_pages=source is RequestSource.DOCUMENT)
    if actual == "pdf" and _PDF_ENCRYPT.search(media):
        return "document_encrypted"
    return None


def _check_image(media: bytes, pillow_format: str, *, allow_pages: bool) -> str | None:
    from PIL import Image

    try:
        with warnings.catch_warnings():
            # Pillow warns before its own bomb limit; ours is lower and checked below.
            warnings.simplefilter("ignore", Image.DecompressionBombWarning)
            # Opening reads the header only; no pixel data is decoded.
            with Image.open(io.BytesIO(media), formats=[pillow_format]) as image:
                width, height = image.size
                frames = getattr(image, "n_frames", 1)
    except Image.DecompressionBombError:
        return "image_too_large"
    except Exception:
        return "media_corrupt"
    if min(width, height) < MIN_IMAGE_SIDE:
        return "image_too_small"
    if width * height > MAX_IMAGE_PIXELS:
        return "image_too_large"
    # Photos with extra frames (animated GIF, multi-page TIFF) are analysed on the first one.
    if allow_pages and pillow_format == "TIFF" and frames > MAX_TIFF_FRAMES:
        return "document_too_many_pages"
    return None
