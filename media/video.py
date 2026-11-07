"""``video_generate`` backend: ``POST /videos`` with an Idempotency-Key, poll, then download the MP4.

The submit is sent once. Its key and the job id are saved under the profile's plugin data, so running
the same request again after a timeout or interrupted call resumes that job instead of creating (and
paying for) a second one. Nothing that can create a charge is retried.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urljoin, urlsplit

from agent.video_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    DEFAULT_RESOLUTION,
    VideoGenProvider,
    error_response,
    save_bytes_video,
    save_url_video,
    success_response,
)

from . import _api
from ._api import BeLiveError
from ._sources import image_data_url

DEFAULT_MODEL = "alibaba/wan3.0-video"
# A queued job alone may wait ten minutes before it starts.
POLL_DEADLINE = 900.0
POLL_INTERVAL = 5.0
REQUEST_TIMEOUT = 60.0
DOWNLOAD_TIMEOUT = 300.0
MAX_VIDEO_BYTES = 256 * 1024 * 1024
MAX_POLL_FAILURES = 5
# The gateway keeps an Idempotency-Key for 24 hours; a saved job is dropped a little earlier.
RECORD_TTL = 23 * 3600
_REFERENCES_PER_CALL = 4
_PATH, _ROUTE = "/videos/models", "video_generations"
_RUNNING = {"pending", "in_progress", "queued", "processing"}
_FAILED = {"failed", "cancelled", "expired"}
_sleep = time.sleep


def _ranked(values: list[str], key) -> list[tuple[float, str]]:
    ranked = [(key(v), v) for v in values]
    return [(n, v) for n, v in ranked if n is not None]


def _height(resolution: str) -> float | None:
    text = resolution.strip().lower()
    if text in ("2k", "4k"):
        return {"2k": 1440.0, "4k": 2160.0}[text]
    match = re.fullmatch(r"(\d+)p", text)
    return float(match.group(1)) if match else None


def _ratio(text: str) -> float | None:
    match = re.fullmatch(r"(\d+):(\d+)", text.strip())
    return int(match.group(1)) / int(match.group(2)) if match and int(match.group(2)) else None


def _nearest(wanted: float, options: list[tuple[float, str]]) -> str | None:
    return min(options, key=lambda o: abs(o[0] - wanted))[1] if options else None


def durations(row: dict) -> list[int]:
    value = row.get("supported_durations")
    return sorted(n for n in value if isinstance(n, int) and not isinstance(n, bool)) if isinstance(value, list) else []


def _configured_model() -> str | None:
    try:
        from hermes_cli.config import load_config
        section = load_config().get("video_gen")
        value = section.get("model") if isinstance(section, dict) else None
    except Exception:
        return None
    return value.strip() if isinstance(value, str) and value.strip() else None


def build_body(row: dict, prompt: str, *, image_url: str | None, reference_image_urls: list[str] | None,
               duration: int | None, aspect_ratio: str, resolution: str, negative_prompt: str | None,
               audio: bool | None, seed: int | None) -> tuple[dict[str, Any], list[str]]:
    """The submit body for one model, and the options the model does not take (dropped, not sent).
    Duration, resolution and aspect ratio are clamped to the model's own live values; a request for
    source images the model cannot take is refused here, before anything is charged."""
    params, ignored = _api.str_list(row.get("supported_parameters")), []
    body: dict[str, Any] = {"model": row["id"], "prompt": prompt}
    allowed = durations(row)
    if duration is not None:
        body["duration"] = min(allowed, key=lambda n: (abs(n - duration), n)) if allowed else duration
    resolutions = _api.str_list(row.get("supported_resolutions"))
    wanted_height = _height(resolution)
    if resolutions and "resolution" in params:
        pick = next((r for r in resolutions if r.lower() == resolution.lower()), None)
        body["resolution"] = pick or (
            _nearest(wanted_height, _ranked(resolutions, _height)) if wanted_height else None) or resolutions[0]
    ratios = _api.str_list(row.get("supported_aspect_ratios"))
    if "aspect_ratio" in params and ratios:
        wanted = _ratio(aspect_ratio)
        pick = aspect_ratio if aspect_ratio in ratios else (
            _nearest(wanted, _ranked(ratios, _ratio)) if wanted else None)
        if pick:
            body["aspect_ratio"] = pick
    for value, param, key in ((negative_prompt, "negative_prompt", "negative_prompt"),
                              (audio, "generate_audio", "audio"), (seed, "seed", "seed")):
        if value is None:
            continue
        if param in params:
            body[param] = value
        else:
            ignored.append(key)
    frames = _api.str_list(row.get("supported_frame_images"))
    if image_url:
        if "first_frame" in frames and "frame_images" in params:
            body["frame_images"] = [{"type": "image_url", "image_url": {"url": image_data_url(image_url)},
                                     "frame_type": "first_frame"}]
        elif "input_references" in params and "image" in _api.input_modalities(row):
            reference_image_urls = [image_url, *(reference_image_urls or [])]
        else:
            raise BeLiveError(f"{row['id']} does not take a source image")
    if reference_image_urls:
        if "input_references" not in params:
            raise BeLiveError(f"{row['id']} does not take reference images")
        body["input_references"] = [{"type": "image_url", "image_url": {"url": image_data_url(u)}}
                                    for u in reference_image_urls]
    return body, ignored


def _store_dir() -> Path:
    from plugins.plugin_storage import plugin_data_dir
    path = plugin_data_dir(_api.PLUGIN) / "video-jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_record(fingerprint: str) -> dict | None:
    path = _store_dir() / f"{fingerprint}.json"
    try:
        record = json.loads(path.read_text())
        if (isinstance(record, dict) and isinstance(record.get("idempotency_key"), str)
                and time.time() - float(record.get("created_at", 0)) < RECORD_TTL
                and isinstance(record.get("job_id", ""), str)):
            return record
    except (OSError, ValueError, TypeError):
        pass
    path.unlink(missing_ok=True)
    return None


def _write_record(fingerprint: str, record: dict) -> None:
    path = _store_dir() / f"{fingerprint}.json"
    temporary = path.with_suffix(f".{uuid.uuid4().hex[:8]}.tmp")
    temporary.write_text(json.dumps(record))
    temporary.chmod(0o600)
    temporary.replace(path)


def _drop_record(fingerprint: str) -> None:
    (_store_dir() / f"{fingerprint}.json").unlink(missing_ok=True)


def _is_mp4(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(12)[4:8] == b"ftyp"


class BeLiveVideoGenProvider(VideoGenProvider):
    @property
    def name(self) -> str:
        return _api.PROVIDER

    @property
    def display_name(self) -> str:
        return "BeLive AI Hub"

    def is_available(self) -> bool:
        return _api.has_key()

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": "BeLive AI Hub", "badge": "paid", "tag": "Live AI Hub video catalogue; resumable jobs",
                "env_vars": [{"key": _api.KEY_ENV, "prompt": "BeLive AI Hub workspace inference key",
                              "url": "https://aihub.belive.sg/"}]}

    def _rows(self) -> list[dict]:
        try:
            return _api.catalogue(_PATH, _ROUTE)
        except BeLiveError:
            return []

    def list_models(self) -> list[dict[str, Any]]:
        out = []
        for row in self._rows():
            entry: dict[str, Any] = {"id": row["id"], "display": row.get("name") or row["id"],
                                     "modalities": ["text", "image"] if "image" in _api.input_modalities(row) else ["text"]}
            allowed = durations(row)
            if allowed:
                entry["min_duration"], entry["max_duration"] = allowed[0], allowed[-1]
            out.append(entry)
        return out

    def default_model(self) -> str | None:
        rows = self._rows()
        text_rows = [r for r in rows if "text" in _api.input_modalities(r)]
        return _api.default_id(text_rows or rows, DEFAULT_MODEL) if rows else DEFAULT_MODEL

    def capabilities(self) -> dict[str, Any]:
        row = next((r for r in self._rows() if r["id"] == (_configured_model() or self.default_model())), None)
        if row is None:
            return super().capabilities()
        params, allowed = _api.str_list(row.get("supported_parameters")), durations(row)
        takes_image = "image" in _api.input_modalities(row)
        return {
            "modalities": ["text", "image"] if takes_image else ["text"],
            "aspect_ratios": _api.str_list(row.get("supported_aspect_ratios")) if "aspect_ratio" in params else [],
            "resolutions": _api.str_list(row.get("supported_resolutions")),
            "max_duration": allowed[-1] if allowed else 10, "min_duration": allowed[0] if allowed else 1,
            "supports_audio": "generate_audio" in params,
            "supports_negative_prompt": "negative_prompt" in params,
            "supports_seed": "seed" in params, "supports_upscale": False,
            "max_reference_images": _REFERENCES_PER_CALL if "input_references" in params else 0,
        }

    def generate(self, prompt: str, *, model: str | None = None, image_url: str | None = None,
                 reference_image_urls: list[str] | None = None, duration: int | None = None,
                 aspect_ratio: str = DEFAULT_ASPECT_RATIO, resolution: str = DEFAULT_RESOLUTION,
                 negative_prompt: str | None = None, audio: bool | None = None, seed: int | None = None,
                 **kwargs: Any) -> dict[str, Any]:
        prompt = (prompt or "").strip()
        model_id = (model or "").strip() or self.default_model() or DEFAULT_MODEL

        def fail(message: str, kind: str) -> dict[str, Any]:
            return error_response(error=message, error_type=kind, provider=self.name, model=model_id,
                                  prompt=prompt, aspect_ratio=aspect_ratio)

        if not prompt:
            return fail("prompt is required", "invalid_request")
        try:
            key = _api.require_key()
            row = _api.find_model(_api.catalogue(_PATH, _ROUTE), model_id, "video generation")
            body, ignored = build_body(
                row, prompt, image_url=image_url, reference_image_urls=reference_image_urls, duration=duration,
                aspect_ratio=aspect_ratio, resolution=resolution, negative_prompt=negative_prompt,
                audio=audio, seed=seed)
            job_id, path, job = self._run(key, body)
        except BeLiveError as exc:
            return fail(str(exc), "api_error")
        extra = {"job_id": job_id, "resolution": body.get("resolution"), "ignored_options": ignored}
        for field in ("generation_id", "usage"):
            if job.get(field) is not None:
                extra[field] = job[field]
        return success_response(
            video=str(path), model=model_id, prompt=prompt, modality="image" if image_url else "text",
            aspect_ratio=body.get("aspect_ratio", ""), duration=body.get("duration", 0), provider=self.name,
            extra=extra)

    # -- job lifecycle ------------------------------------------------------------------------

    def _run(self, key: str, body: dict) -> tuple[str, Path, dict]:
        base = _api.base_url()
        digest = hashlib.sha256(json.dumps([base, hashlib.sha256(key.encode()).hexdigest(), body],
                                           sort_keys=True).encode()).hexdigest()
        record = _read_record(digest)
        resumed = record is not None
        if record is None:
            record = {"idempotency_key": str(uuid.uuid4()), "model": body["model"], "created_at": time.time()}
            _write_record(digest, record)
        job_id = record.get("job_id") or ""
        if not job_id:
            job_id = self._submit(key, body, record["idempotency_key"], resumed, digest)
            _write_record(digest, {**record, "job_id": job_id})
        deadline = time.monotonic() + POLL_DEADLINE
        job = self._wait(key, job_id, deadline, digest)
        try:
            path = self._download(key, job_id)
        except BeLiveError as exc:
            raise BeLiveError(f"{exc}. The job completed and is saved; run the same request again to download "
                              "it without a new charge") from None
        _drop_record(digest)
        return job_id, path, job

    def _submit(self, key: str, body: dict, idempotency_key: str, resumed: bool, digest: str) -> str:
        if resumed:
            try:
                _, _, found = _api.send_json("GET", f"/videos?idempotency_key={quote(idempotency_key)}", key=key,
                                             timeout=REQUEST_TIMEOUT, label="video lookup")
            except BeLiveError:
                found = None
            first = found.get("data", [None])[0] if isinstance(found, dict) and isinstance(found.get("data"), list) \
                and found["data"] else found
            if isinstance(first, dict) and isinstance(first.get("id"), str):
                return first["id"]
        try:
            _, _, job = _api.send_json("POST", "/videos", key=key, payload=body, timeout=REQUEST_TIMEOUT,
                                       headers={"Idempotency-Key": idempotency_key}, label="video submit")
        except BeLiveError as exc:
            if exc.status is not None and exc.status < 500 and exc.status != 429:
                _drop_record(digest)  # refused: nothing was created, a rerun may use a new key
                raise
            raise BeLiveError(f"{exc}. The job may have started; run the same request again and the saved "
                              "Idempotency-Key reconciles it instead of creating a second job") from None
        if not isinstance(job, dict) or not isinstance(job.get("id"), str):
            raise BeLiveError("BeLive AI Hub video submit response was not understood")
        return job["id"]

    def _wait(self, key: str, job_id: str, deadline: float, digest: str) -> dict:
        hint = (f"BeLive AI Hub video job {job_id} has not been cancelled and may still be charged. "
                "Run the same request again to resume it; no second job is created")
        failures = 0
        while True:
            pause = POLL_INTERVAL
            try:
                _, _, job = _api.send_json("GET", f"/videos/{quote(job_id, safe='')}", key=key,
                                           timeout=REQUEST_TIMEOUT, label="video status")
                failures = 0
            except BeLiveError as exc:
                if exc.status == 404:
                    _drop_record(digest)
                    raise BeLiveError(f"BeLive AI Hub video job {job_id} was not found for this key; run the "
                                      "request again to start a new job") from None
                failures += 1
                transient = exc.status is None or exc.status == 429 or exc.status >= 500
                if not transient or failures >= MAX_POLL_FAILURES:
                    raise BeLiveError(f"{exc}. {hint}") from None
                pause = max(pause, exc.retry_after or 0.0)
                job = {}
            status = job.get("status") if isinstance(job, dict) else None
            if status == "completed":
                return job
            if status in _FAILED:
                _drop_record(digest)
                error = job.get("error")
                message = error.get("message") if isinstance(error, dict) else error
                raise BeLiveError(f"BeLive AI Hub video job {job_id} {status}"
                                  + (f": {message}" if isinstance(message, str) and message else ""))
            if job and status not in _RUNNING:
                raise BeLiveError(f"BeLive AI Hub video job {job_id} reported an unknown status. {hint}")
            if time.monotonic() >= deadline:
                raise BeLiveError(f"BeLive AI Hub video job {job_id} was still running after "
                                  f"{int(POLL_DEADLINE // 60)} minutes. {hint}")
            _sleep(min(pause, max(0.0, deadline - time.monotonic())))

    def _download(self, key: str, job_id: str) -> Path:
        url = f"{_api.base_url()}/videos/{quote(job_id, safe='')}/content?index=0"
        status, headers, data = _api.send("GET", url, key=key, accept="video/mp4,*/*", timeout=DOWNLOAD_TIMEOUT,
                                          max_bytes=MAX_VIDEO_BYTES, label="video download", allow_redirect=True)
        if status >= 300:
            # The key goes to AI Hub only. A storage link is fetched through Hermes's guarded
            # downloader, which checks every hop and sends no credential.
            target = urljoin(url, headers.get("Location") or "")
            parts = urlsplit(target)
            if parts.scheme != "https" or parts.netloc == urlsplit(url).netloc:
                raise BeLiveError("BeLive AI Hub video download redirected somewhere unusable")
            try:
                path = save_url_video(target, prefix=self.name, timeout=DOWNLOAD_TIMEOUT, max_bytes=MAX_VIDEO_BYTES)
            except Exception:
                raise BeLiveError("BeLive AI Hub video download from its storage link failed") from None
        else:
            path = save_bytes_video(data, prefix=self.name)
        if not _is_mp4(path):
            path.unlink(missing_ok=True)
            raise BeLiveError("BeLive AI Hub video download was not an MP4 file")
        return path
