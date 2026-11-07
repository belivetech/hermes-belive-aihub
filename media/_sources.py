"""Source images for edits and image-to-video: ``data:`` URLs, HTTPS URLs and local files.

The gateway accepts PNG, JPEG and WebP up to 7 MiB each, judged by the leading bytes, so the same
check runs here before any paid request. That check is also what keeps a local-file source from
becoming an upload channel: a key, token or ``.env`` file is not an image, so it is refused and never
sent.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from ._api import BeLiveError

MAX_IMAGE_BYTES = 7 * 1024 * 1024
_DATA_URL = re.compile(r"^data:image/(?:png|jpeg|webp);base64,([A-Za-z0-9+/=\s]+)$", re.IGNORECASE)


def sniff_image(data: bytes) -> str | None:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _checked(data: bytes) -> tuple[bytes, str]:
    mime = sniff_image(data)
    if mime is None:
        raise BeLiveError("BeLive AI Hub source images must be PNG, JPEG or WebP")
    if len(data) > MAX_IMAGE_BYTES:
        raise BeLiveError("BeLive AI Hub source images may be at most 7 MiB")
    return data, mime


def _local(ref: str) -> bytes:
    path = Path(ref).expanduser()
    if not path.is_file():
        raise BeLiveError("BeLive AI Hub source image is not a readable file, https URL or image data: URL")
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise BeLiveError("BeLive AI Hub source images may be at most 7 MiB")
    return path.read_bytes()


def image_bytes(ref: str) -> tuple[bytes, str]:
    """``(bytes, mime)`` of one source image. An HTTPS URL is fetched through Hermes's guarded
    downloader (public hosts only, redirects re-checked) and the cached copy removed afterwards."""
    ref = ref.strip()
    match = _DATA_URL.match(ref)
    if match:
        try:
            return _checked(base64.b64decode(match.group(1), validate=False))
        except ValueError:
            raise BeLiveError("BeLive AI Hub source image data: URL is not valid base64") from None
    if ref.lower().startswith("http://"):
        raise BeLiveError("BeLive AI Hub source image URLs must use https")
    if ref.lower().startswith("https://"):
        from agent.image_gen_provider import save_url_image
        try:
            path = save_url_image(ref, prefix="belive-aihub-source", max_bytes=MAX_IMAGE_BYTES)
        except Exception:
            raise BeLiveError("BeLive AI Hub could not download the source image URL") from None
        try:
            return _checked(path.read_bytes())
        finally:
            path.unlink(missing_ok=True)
    return _checked(_local(ref))


def image_data_url(ref: str) -> str:
    """A ``data:`` URL for a local or data source; an HTTPS URL passes through after Hermes's SSRF check."""
    ref = ref.strip()
    if ref.lower().startswith("https://"):
        from tools.url_safety import is_safe_url
        if not is_safe_url(ref):
            raise BeLiveError("BeLive AI Hub refuses a source image URL that is not a public https address")
        return ref
    data, mime = image_bytes(ref)
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
