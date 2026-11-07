"""Speech-to-text backend: multipart ``POST /audio/transcriptions`` of an MP3 or WAV recording."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from agent.transcription_provider import TranscriptionProvider

from . import _api
from ._api import BeLiveError

DEFAULT_MODEL = "qwen/qwen-audio-3.0-asr-flash"
# The gateway's own upload limit; it also refuses anything that is not MP3 or WAV by its bytes.
MAX_BYTES = 25_000_000
REQUEST_TIMEOUT = 120.0
POLL_DEADLINE = 300.0
POLL_INTERVAL = 3.0
_PATH, _ROUTE = "/models?output_modalities=all", "audio_transcriptions"
_sleep = time.sleep


def audio_type(data: bytes) -> str | None:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    if data[:3] == b"ID3" or (len(data) > 1 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0):
        return "audio/mpeg"
    return None


class BeLiveTranscriptionProvider(TranscriptionProvider):
    @property
    def name(self) -> str:
        return _api.PROVIDER

    @property
    def display_name(self) -> str:
        return "BeLive AI Hub"

    def is_available(self) -> bool:
        return _api.has_key()

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": "BeLive AI Hub", "badge": "paid", "tag": "Live AI Hub speech-recognition models (MP3 or WAV)",
                "env_vars": [{"key": _api.KEY_ENV, "prompt": "BeLive AI Hub workspace inference key",
                              "url": "https://aihub.belive.sg/"}]}

    def _rows(self) -> list[dict]:
        try:
            return _api.catalogue(_PATH, _ROUTE)
        except BeLiveError:
            return []

    def list_models(self) -> list[dict[str, Any]]:
        return [{"id": r["id"], "display": r.get("name") or r["id"]} for r in self._rows()]

    def default_model(self) -> Optional[str]:
        rows = self._rows()
        return _api.default_id(rows, DEFAULT_MODEL) if rows else DEFAULT_MODEL

    def transcribe(self, file_path: str, *, model: Optional[str] = None, language: Optional[str] = None,
                   **extra: Any) -> dict[str, Any]:
        def result(ok: bool, text: str = "", error: str = "") -> dict[str, Any]:
            out = {"success": ok, "transcript": text, "provider": self.name}
            if not ok:
                out["error"] = error
            return out

        try:
            key = _api.require_key()
            path = Path(file_path)
            if path.stat().st_size > MAX_BYTES:
                return result(False, error="BeLive AI Hub transcription takes recordings up to 25 MB")
            data = path.read_bytes()
            mime = audio_type(data)
            if mime is None:
                return result(False, error="BeLive AI Hub transcription takes MP3 or WAV recordings; convert "
                                           "this file first (for example `ffmpeg -i in.ogg -ac 1 out.wav`)")
            model_id = (model or "").strip() or self.default_model() or DEFAULT_MODEL
            _api.find_model(_api.catalogue(_PATH, _ROUTE), model_id, "transcription")
            fields = {"model": model_id}
            if language and language.strip():
                fields["language"] = language.strip().split("-")[0].split("_")[0].lower()
            _, _, payload = _api.send_json(
                "POST", "/audio/transcriptions", key=key, timeout=REQUEST_TIMEOUT, label="transcription",
                form=_api.multipart(fields, [("file", f"recording{path.suffix.lower() or '.wav'}", mime, data)]))
            text = self._text(key, payload)
        except BeLiveError as exc:
            return result(False, error=str(exc))
        except OSError:
            return result(False, error="The recording could not be read")
        return result(True, text)

    def _text(self, key: str, payload: Any) -> str:
        """The transcript from a synchronous answer, or from the job a long recording answers with."""
        deadline = time.monotonic() + POLL_DEADLINE
        while True:
            if not isinstance(payload, dict):
                raise BeLiveError("BeLive AI Hub transcription response was not understood")
            if isinstance(payload.get("text"), str):
                return payload["text"]
            transcript = payload.get("transcript")
            if payload.get("status") == "completed" and isinstance(transcript, dict) and isinstance(transcript.get("text"), str):
                return transcript["text"]
            job_id = payload.get("id")
            if payload.get("status") in ("failed", "expired") or not isinstance(job_id, str) \
                    or payload.get("status") not in ("pending", "in_progress"):
                error = payload.get("error")
                message = error.get("message") if isinstance(error, dict) else None
                raise BeLiveError("BeLive AI Hub transcription did not complete"
                                  + (f": {message}" if isinstance(message, str) and message else ""))
            if time.monotonic() >= deadline:
                raise BeLiveError(f"BeLive AI Hub transcription job {job_id} was still running after "
                                  f"{int(POLL_DEADLINE)} s; check Activity before submitting it again")
            _sleep(POLL_INTERVAL)
            _, _, payload = _api.send_json("GET", f"/audio/transcriptions/{job_id}", key=key,
                                           timeout=_api.CATALOGUE_TIMEOUT * 4, label="transcription status")
