"""``text_to_speech`` backend: ``POST /audio/speech`` in mp3, opus, aac, flac, wav or pcm."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from agent.tts_provider import TTSProvider

from . import _api
from ._api import BeLiveError

DEFAULT_MODEL = "qwen/qwen-audio-3.0-tts-flash"
DEFAULT_VOICE = "loongjohn"
MAX_CHARACTERS = 600
REQUEST_TIMEOUT = 120.0
MAX_AUDIO_BYTES = 64 * 1024 * 1024
# Hermes format -> (gateway response_format, file extension). The gateway has no `ogg` format:
# its `opus` answer is Ogg Opus, which is what an `ogg` request wants.
_FORMATS = {
    "mp3": ("mp3", ".mp3"), "wav": ("wav", ".wav"), "flac": ("flac", ".flac"), "opus": ("opus", ".ogg"),
    "ogg": ("opus", ".ogg"), "aac": ("aac", ".aac"), "pcm": ("pcm", ".pcm"),
}
_MAGIC = {
    "mp3": lambda b: b[:3] == b"ID3" or (len(b) > 1 and b[0] == 0xFF and b[1] & 0xE0 == 0xE0),
    "wav": lambda b: b[:4] == b"RIFF" and b[8:12] == b"WAVE",
    "flac": lambda b: b[:4] == b"fLaC",
    "opus": lambda b: b[:4] == b"OggS",
    "aac": lambda b: len(b) > 1 and b[0] == 0xFF and b[1] & 0xF0 == 0xF0,
    "pcm": lambda b: len(b) > 0,
}
_PATH, _ROUTE = "/models?output_modalities=audio", "audio_speech"


class BeLiveTTSProvider(TTSProvider):
    @property
    def name(self) -> str:
        return _api.PROVIDER

    @property
    def display_name(self) -> str:
        return "BeLive AI Hub"

    def is_available(self) -> bool:
        return _api.has_key()

    def get_setup_schema(self) -> dict[str, Any]:
        return {"name": "BeLive AI Hub", "badge": "paid", "tag": "Live AI Hub speech models; mp3, opus, aac, flac, wav, pcm",
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

    def default_voice(self) -> Optional[str]:
        # Voices belong to a model and are case-sensitive; the gateway's refusal lists a model's own.
        return DEFAULT_VOICE

    def synthesize(self, text: str, output_path: str, *, voice: Optional[str] = None, model: Optional[str] = None,
                   speed: Optional[float] = None, format: str = "mp3", **extra: Any) -> str:
        length = len(text or "")
        if not 1 <= length <= MAX_CHARACTERS:
            raise ValueError(
                f"BeLive AI Hub speech takes 1 to {MAX_CHARACTERS} characters per request; this text has {length}. "
                f"Set tts.{self.name}.max_text_length: {MAX_CHARACTERS} so Hermes splits longer text")
        wire, extension = _FORMATS.get((format or "").lower(), _FORMATS["mp3"])
        key = _api.require_key()
        model_id = (model or "").strip() or self.default_model() or DEFAULT_MODEL
        _api.find_model(_api.catalogue(_PATH, _ROUTE), model_id, "speech")
        body = {"model": model_id, "input": text, "voice": (voice or "").strip() or DEFAULT_VOICE,
                "response_format": wire}
        _, _, audio = _api.send(
            "POST", _api.base_url() + "/audio/speech", key=key, body=json.dumps(body).encode(), content_type="application/json",
            accept="audio/*", timeout=REQUEST_TIMEOUT, max_bytes=MAX_AUDIO_BYTES, label="speech synthesis")
        if not _MAGIC[wire](audio):
            raise BeLiveError(f"BeLive AI Hub speech synthesis did not return {wire} audio; the request may have "
                              "been charged, so check Activity before retrying")
        target = Path(output_path).with_suffix(extension)
        target.write_bytes(audio)
        return str(target)

