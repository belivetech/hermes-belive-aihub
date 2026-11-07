"""The media plugin installed into an isolated Hermes home and driven through the host's own tools."""

import base64
import json
import shutil
from pathlib import Path

import pytest
from media_helpers import Response, catalogue_row

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 64
MP3 = b"ID3\x04" + b"0" * 32
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 32


@pytest.fixture
def host(tmp_path, gateway, monkeypatch):
    """A fresh home with the plugin installed and enabled, and BeLive selected for every media tool."""
    home = tmp_path / "home"
    shutil.copytree(ROOT / "media", home / "plugins" / "belive-aihub-media",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (home / "config.yaml").write_text(
        "plugins:\n  enabled: [belive-aihub-media]\n"
        "image_gen:\n  provider: belive-aihub\nvideo_gen:\n  provider: belive-aihub\n"
        "tts:\n  provider: belive-aihub\n  output_format: mp3\nstt:\n  provider: belive-aihub\n")
    from hermes_cli.plugins import _ensure_plugins_discovered
    _ensure_plugins_discovered(force=True)
    gateway.on("GET", "/images/models", Response(200, {"data": [catalogue_row(
        "lab/paint", "image_generations", architecture={"input_modalities": ["text"]},
        tokenhub_image_options={"sizes": [{"size": "1024x1024", "aspect_ratio": "1:1", "resolution": "1K"}]})]}))
    gateway.on("GET", "/videos/models", Response(200, {"data": [catalogue_row(
        "lab/film", "video_generations", architecture={"input_modalities": ["text"]},
        supported_parameters=["prompt", "duration", "resolution"], supported_resolutions=["480p"],
        supported_durations=[2, 4])]}))
    gateway.on("GET", "/models?output_modalities=audio", Response(200, {"data": [
        catalogue_row("lab/voice", "audio_speech")]}))
    gateway.on("GET", "/models?output_modalities=all", Response(200, {"data": [
        catalogue_row("lab/ear", "audio_transcriptions")]}))
    return home


def test_discovery_registers_all_four_backends_under_the_selected_name(host):
    from agent import image_gen_registry, transcription_registry, tts_registry, video_gen_registry
    for registry in (image_gen_registry, video_gen_registry, tts_registry, transcription_registry):
        provider = registry.get_provider("belive-aihub")
        assert provider is not None and provider.display_name == "BeLive AI Hub"
    assert image_gen_registry.get_active_provider().name == "belive-aihub"
    assert video_gen_registry.get_active_provider().name == "belive-aihub"


def test_image_generate_tool_reaches_the_gateway(host, gateway):
    from tools.image_generation_tool import _handle_image_generate
    gateway.on("POST", "/images/generations", Response(200, {"data": [{"b64_json": base64.b64encode(PNG).decode()}]}))
    result = json.loads(_handle_image_generate({"prompt": "A red apple", "aspect_ratio": "square"}))
    assert result["success"] and result["provider"] == "belive-aihub" and result["model"] == "lab/paint"
    assert Path(result["image"]).read_bytes() == PNG


def test_video_generate_tool_reaches_the_gateway(host, gateway):
    from tools.video_generation_tool import _handle_video_generate
    gateway.on("POST", "/videos", Response(200, {"id": "job_1", "status": "completed"}))
    gateway.on("GET", "/videos/job_1", Response(200, {"id": "job_1", "status": "completed"}))
    gateway.on("GET", "/videos/job_1/content?index=0", Response(200, MP4))
    result = json.loads(_handle_video_generate({"prompt": "A red balloon", "duration": 3, "resolution": "480p"}))
    assert result["success"] and result["model"] == "lab/film" and result["duration"] == 2
    assert Path(result["video"]).read_bytes() == MP4
    assert json.loads(gateway.calls("POST", "/videos")[0]["data"])["resolution"] == "480p"


def test_text_to_speech_tool_reaches_the_gateway(host, gateway):
    from tools.tts_tool import text_to_speech_tool
    gateway.on("POST", "/audio/speech", Response(200, MP3))
    result = json.loads(text_to_speech_tool("Hello from AI Hub."))
    assert result["success"] and result["provider"] == "belive-aihub"
    assert Path(result["file_path"]).read_bytes() == MP3
    assert json.loads(gateway.calls("POST", "/audio/speech")[0]["data"])["model"] == "lab/voice"


def test_transcribe_audio_reaches_the_gateway(host, gateway, tmp_path):
    from tools.transcription_tools import transcribe_audio
    clip = tmp_path / "clip.wav"
    clip.write_bytes(WAV)
    gateway.on("POST", "/audio/transcriptions", Response(200, {"text": "hello world"}))
    result = transcribe_audio(str(clip))
    assert result["success"] and result["transcript"] == "hello world" and result["provider"] == "belive-aihub"
