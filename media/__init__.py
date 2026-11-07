"""BeLive AI Hub image, video, speech and transcription backends for Hermes Agent.

``register(ctx)`` adds four backends named ``belive-aihub``: one for ``image_generate``
(``image_gen.provider``), ``video_generate`` (``video_gen.provider``), ``text_to_speech``
(``tts.provider``) and speech-to-text (``stt.provider``). It registers no tools, hooks, commands or
threads. A request goes to BeLive AI Hub with ``BELIVE_TOKENHUB_API_KEY`` only when a backend is
selected and its tool runs.
"""

from __future__ import annotations

from ._api import __version__
from .image import BeLiveImageGenProvider
from .speech import BeLiveTTSProvider
from .transcription import BeLiveTranscriptionProvider
from .video import BeLiveVideoGenProvider

__all__ = [
    "BeLiveImageGenProvider", "BeLiveTTSProvider", "BeLiveTranscriptionProvider", "BeLiveVideoGenProvider",
    "__version__", "register",
]


def register(ctx) -> None:
    """Hermes plugin entry point."""
    ctx.register_image_gen_provider(BeLiveImageGenProvider())
    ctx.register_video_gen_provider(BeLiveVideoGenProvider())
    ctx.register_tts_provider(BeLiveTTSProvider())
    ctx.register_transcription_provider(BeLiveTranscriptionProvider())
