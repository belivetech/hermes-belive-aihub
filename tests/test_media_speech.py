import json
import sys

import pytest
from media_helpers import MEDIA_KEY, Response, catalogue_row, http_error

WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"0" * 32
MP3 = b"ID3\x04" + b"0" * 32
OGG = b"OggS\x00\x02" + b"0" * 32
FLAC = b"fLaC" + b"0" * 32
AAC = b"\xff\xf1" + b"0" * 32


@pytest.fixture
def speech(media, gateway):
    gateway.on("GET", "/models?output_modalities=audio", Response(200, {"data": [
        catalogue_row("lab/voice", "audio_speech"), catalogue_row("lab/song", "audio_generations")]}))
    return media.BeLiveTTSProvider()


@pytest.mark.parametrize("asked, wire, extension, audio", [
    ("mp3", "mp3", ".mp3", MP3), ("wav", "wav", ".wav", WAV), ("flac", "flac", ".flac", FLAC),
    ("opus", "opus", ".ogg", OGG), ("ogg", "opus", ".ogg", OGG), ("aac", "aac", ".aac", AAC),
    ("pcm", "pcm", ".pcm", b"\x01\x02" * 8), ("OGG", "opus", ".ogg", OGG), ("surprise", "mp3", ".mp3", MP3)])
def test_each_format_is_requested_and_written_with_its_extension(speech, gateway, tmp_path, asked, wire, extension, audio):
    gateway.on("POST", "/audio/speech", Response(200, audio, {"content-type": "audio/mpeg"}))
    written = speech.synthesize("Hello from AI Hub.", str(tmp_path / "out.mp3"), voice="loongjohn",
                                model="lab/voice", format=asked)
    assert written == str(tmp_path / f"out{extension}") and open(written, "rb").read() == audio
    call = gateway.calls("POST", "/audio/speech")[0]
    assert json.loads(call["data"]) == {"model": "lab/voice", "input": "Hello from AI Hub.",
                                        "voice": "loongjohn", "response_format": wire}
    assert call["headers"]["authorization"] == f"Bearer {MEDIA_KEY}"


def test_defaults_come_from_the_live_catalogue(speech, gateway, tmp_path):
    gateway.on("POST", "/audio/speech", Response(200, MP3))
    assert speech.default_model() == "lab/voice" and speech.default_voice() == "loongjohn"
    assert [m["id"] for m in speech.list_models()] == ["lab/voice"]
    speech.synthesize("Hi", str(tmp_path / "a.mp3"))
    body = json.loads(gateway.calls("POST", "/audio/speech")[0]["data"])
    assert body["model"] == "lab/voice" and body["voice"] == "loongjohn"


def test_long_text_unlisted_model_and_wrong_audio_are_refused(speech, gateway, tmp_path, media):
    with pytest.raises(ValueError, match="max_text_length: 600"):
        speech.synthesize("x" * 601, str(tmp_path / "a.mp3"))
    with pytest.raises(ValueError):
        speech.synthesize("", str(tmp_path / "a.mp3"))
    with pytest.raises(media._api.BeLiveError, match="lists no speech model named 'lab/song'"):
        speech.synthesize("Hi", str(tmp_path / "a.mp3"), model="lab/song")
    assert not gateway.calls("POST", "")
    gateway.on("POST", "/audio/speech", Response(200, b"<html>", {"x-generation-id": "req_speech_1"}))
    with pytest.raises(media._api.BeLiveError, match=r"may have been charged.*\(AI Hub request req_speech_1\)$"):
        speech.synthesize("Hi", str(tmp_path / "a.mp3"), format="wav")
    assert not list(tmp_path.glob("*.wav"))


def test_gateway_refusal_surfaces_the_voice_list_once(speech, gateway, tmp_path, media):
    gateway.on("POST", "/audio/speech", http_error(400, "`voice` must be one of: loongjohn, longanhuan_v3.6"))
    with pytest.raises(media._api.BeLiveError, match="HTTP 400: `voice` must be one of: loongjohn"):
        speech.synthesize("Hi", str(tmp_path / "a.mp3"), voice="nobody")
    assert len(gateway.calls("POST", "/audio/speech")) == 1


def test_opus_is_not_declared_voice_compatible_and_has_no_streaming(speech):
    assert speech.voice_compatible is False and speech.streams_pcm is False
    with pytest.raises(NotImplementedError):
        next(speech.stream("hello"))
