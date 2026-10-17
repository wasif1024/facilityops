import io

import pytest
from PIL import Image

from backend.app.domain.intake import RequestSource
from backend.app.domain.media import MAX_IMAGE_PIXELS, check_media, sniff

from .test_media_intake import PDF, WAV, image_bytes


@pytest.mark.parametrize(
    ("data", "family"),
    [
        (image_bytes("PNG"), "png"),
        (image_bytes("JPEG"), "jpeg"),
        (image_bytes("GIF"), "gif"),
        (image_bytes("WEBP"), "webp"),
        (image_bytes("BMP"), "bmp"),
        (image_bytes("TIFF"), "tiff"),
        (PDF, "pdf"),
        (WAV, "wav"),
        (b"OggS\x00\x02", "ogg"),
        (b"fLaC\x00\x00", "flac"),
        (b"\x1aE\xdf\xa3\x01", "webm"),
        (b"\x00\x00\x00\x20ftypM4A ", "mp4"),
        (b"ID3\x04\x00", "mpeg_audio"),
        (b"\xff\xf1\x50\x80", "mpeg_audio"),
        (b"MZ\x90\x00", None),
        (b"", None),
    ],
)
def test_sniff_recognises_files_by_their_bytes(data, family):
    assert sniff(data) == family


@pytest.mark.parametrize(
    ("source", "data", "media_type"),
    [
        (RequestSource.PHOTO, image_bytes("JPEG"), "image/jpeg"),
        (RequestSource.PHOTO, image_bytes("WEBP"), "image/webp"),
        (RequestSource.DOCUMENT, PDF, "application/pdf"),
        (RequestSource.DOCUMENT, image_bytes("TIFF"), "image/tiff"),
        (RequestSource.VOICE, WAV, "audio/x-wav"),
        (RequestSource.VOICE, b"\x00\x00\x00\x20ftypM4A ", "audio/aac"),
    ],
)
def test_genuine_files_pass(source, data, media_type):
    assert check_media(source, data, media_type) is None


def test_huge_canvas_is_rejected_from_the_header_alone():
    # 7,000 x 7,000 = 49 MP; a solid colour compresses to a few kilobytes.
    side = int((MAX_IMAGE_PIXELS + 9_000_000) ** 0.5)
    output = io.BytesIO()
    Image.new("1", (side, side)).save(output, "PNG")
    assert len(output.getvalue()) < 1_000_000

    assert check_media(RequestSource.PHOTO, output.getvalue(), "image/png") == "image_too_large"


def test_scan_with_too_many_pages_is_refused():
    frames = [Image.new("L", (100, 100)) for _ in range(21)]
    output = io.BytesIO()
    frames[0].save(output, "TIFF", save_all=True, append_images=frames[1:])

    assert check_media(RequestSource.DOCUMENT, output.getvalue(), "image/tiff") == "document_too_many_pages"
