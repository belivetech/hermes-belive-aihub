"""Shared transport, credentials and live catalogue reads for the BeLive AI Hub media backends.

Every request goes to one of the two official HTTPS bases, carries only ``BELIVE_TOKENHUB_API_KEY``
(read through Hermes's profile secret scope), refuses redirects and is never retried here.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from agent.secret_scope import get_secret_str

__version__ = "0.2.0"
PLUGIN = "belive-aihub-media"
PROVIDER = "belive-aihub"
KEY_ENV = "BELIVE_TOKENHUB_API_KEY"
BASE_ENV = "BELIVE_TOKENHUB_BASE_URL"
BASE_URL = "https://tokenhub-api.belive.sg/api/v1"
STAGING_URL = "https://api-stg-tokenhub.belive.sg/api/v1"
USER_AGENT = f"hermes-belive-aihub-media/{__version__}"
CATALOGUE_TIMEOUT = 8.0
_MAX_JSON_BYTES = 8 * 1024 * 1024
_MAX_ERROR_BYTES = 16 * 1024
_CATALOGUE_TTL = 300.0
_FAILURE_TTL = 60.0


class BeLiveError(Exception):
    """A failure whose message is safe to show: no key, URL, header or request body."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A redirect must never carry the workspace key to another origin.
        return None


def api_key() -> str:
    return (get_secret_str(KEY_ENV) or "").strip()


def has_key() -> bool:
    try:
        return bool(api_key())
    except Exception:
        return False


def require_key() -> str:
    key = api_key()
    if not key:
        raise BeLiveError(f"BeLive AI Hub needs {KEY_ENV}; set it in the active profile's .env")
    return key


def base_url() -> str:
    base = (get_secret_str(BASE_ENV) or "").strip().rstrip("/") or BASE_URL
    if base not in (BASE_URL, STAGING_URL):
        raise BeLiveError(f"{BASE_ENV} must be an official BeLive AI Hub API base URL")
    return base


def _detail(body: bytes) -> str:
    try:
        parsed = json.loads(body)
        error = parsed.get("error") if isinstance(parsed, dict) else None
        raw = error.get("message") if isinstance(error, dict) else error
        if not isinstance(raw, str) and isinstance(parsed, dict):
            raw = parsed.get("message")
        text = raw if isinstance(raw, str) else ""
    except ValueError:
        return ""
    return " ".join("".join(c if c.isprintable() else " " for c in text).split())[:300]


def send(
    method: str, url: str, *, key: str | None = None, body: bytes | None = None,
    content_type: str | None = None, accept: str = "application/json",
    headers: dict[str, str] | None = None, timeout: float, max_bytes: int = _MAX_JSON_BYTES,
    label: str, allow_redirect: bool = False,
) -> tuple[int, Any, bytes]:
    """One request: ``(status, headers, body)``. Non-2xx raises :class:`BeLiveError`; with
    ``allow_redirect`` a 3xx is returned (never followed) so the caller can read ``Location``."""
    request_headers = {"Accept": accept, "User-Agent": USER_AGENT, **(headers or {})}
    if key:
        request_headers["Authorization"] = f"Bearer {key}"
    if content_type:
        request_headers["Content-Type"] = content_type
    request = Request(url, data=body, method=method, headers=request_headers)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            status, response_headers = getattr(response, "status", 200), response.headers
            data = response.read(max_bytes + 1)
    except HTTPError as exc:
        if allow_redirect and 300 <= exc.code < 400:
            return exc.code, exc.headers, b""
        try:
            detail = _detail(exc.read(_MAX_ERROR_BYTES))
        except Exception:
            detail = ""
        raise BeLiveError(
            f"BeLive AI Hub {label} returned HTTP {exc.code}" + (f": {detail}" if detail else ""),
            exc.code) from None
    except TimeoutError:
        raise BeLiveError(f"BeLive AI Hub {label} timed out after {int(timeout)} s") from None
    except (URLError, OSError):
        raise BeLiveError(f"BeLive AI Hub {label} failed: network error") from None
    if len(data) > max_bytes:
        raise BeLiveError(f"BeLive AI Hub {label} response is larger than {max_bytes // 1048576} MiB")
    return status, response_headers, data


def send_json(
    method: str, path: str, *, key: str, payload: dict | None = None, form: tuple[bytes, str] | None = None,
    headers: dict[str, str] | None = None, timeout: float, label: str,
) -> tuple[int, Any, Any]:
    """``send`` against the official base, with a JSON body or a prepared multipart form, answered as JSON."""
    body, content_type = form if form else (
        (json.dumps(payload).encode(), "application/json") if payload is not None else (None, None))
    status, response_headers, data = send(
        method, base_url() + path, key=key, body=body, content_type=content_type, headers=headers,
        timeout=timeout, label=label)
    try:
        return status, response_headers, json.loads(data)
    except ValueError:
        raise BeLiveError(f"BeLive AI Hub {label} returned a response that is not JSON") from None


def multipart(fields: dict[str, str], files: list[tuple[str, str, str, bytes]]) -> tuple[bytes, str]:
    """A multipart/form-data body: text ``fields`` and ``(name, filename, content type, bytes)`` files."""
    boundary = "belive-" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    for name, filename, content_type, data in files:
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                      f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n').encode()
                     + data + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# --- Live catalogues -------------------------------------------------------------------------

_cache: dict[tuple, tuple[float, Any]] = {}
_lock = threading.Lock()


def _ready_rows(payload: Any, route: str) -> list[dict]:
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        raise BeLiveError("BeLive AI Hub returned an unexpected model catalogue")
    rows, seen = [], set()
    for row in data:
        model_id = row.get("id") if isinstance(row, dict) else None
        endpoints = row.get("endpoints") if isinstance(row, dict) else None
        if (not isinstance(model_id, str) or "/" not in model_id or any(c.isspace() for c in model_id)
                or model_id in seen or not isinstance(endpoints, list)):
            continue
        if any(isinstance(e, dict) and e.get("route") == route and e.get("status") == "ready"
               for e in endpoints):
            seen.add(model_id)
            rows.append(row)
    return rows


def catalogue(path: str, route: str) -> list[dict]:
    """Ready rows of one live catalogue for the active profile's key. Cached per base, key and path
    for five minutes; a failure is remembered for one minute so a picker does not stall on each paint."""
    key, base = require_key(), base_url()
    cache_key = (base, hashlib.sha256(key.encode()).hexdigest()[:16], path, route)
    with _lock:
        hit = _cache.get(cache_key)
    if hit and time.monotonic() < hit[0]:
        if isinstance(hit[1], BeLiveError):
            raise hit[1]
        return hit[1]
    try:
        _, _, payload = send_json("GET", path, key=key, timeout=CATALOGUE_TIMEOUT, label="model discovery")
        result: Any = _ready_rows(payload, route)
    except BeLiveError as exc:
        with _lock:
            _cache[cache_key] = (time.monotonic() + _FAILURE_TTL, exc)
        raise
    with _lock:
        _cache[cache_key] = (time.monotonic() + _CATALOGUE_TTL, result)
    return result


def find_model(rows: list[dict], model_id: str | None, kind: str) -> dict:
    """The catalogue row named ``model_id``, or an error that lists what the account can use."""
    for row in rows:
        if row["id"] == model_id:
            return row
    offered = ", ".join(row["id"] for row in rows[:12]) or "none"
    raise BeLiveError(f"BeLive AI Hub lists no {kind} model named {model_id!r}. Listed: {offered}")


def default_id(rows: list[dict], preferred: str) -> str:
    """The preferred model when the account lists it, else the first listed row."""
    ids = [row["id"] for row in rows]
    return preferred if preferred in ids or not ids else ids[0]


def str_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def input_modalities(row: dict) -> list[str]:
    architecture = row.get("architecture")
    return str_list(architecture.get("input_modalities") if isinstance(architecture, dict) else None)


def generation_id(headers: Any) -> str | None:
    """The gateway's id for this request, as shown in Activity (``x-generation-id``)."""
    value = headers.get("x-generation-id") if headers is not None else None
    return value if isinstance(value, str) and value else None
